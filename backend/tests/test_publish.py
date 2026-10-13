import json

import httpx
import pytest

from app.contracts.models import Classification, Finding
from app.pipeline.publish import PublishError, build_review, publish_review

RUN = {"id": 1, "repo": "alice/demo", "pr": 7, "head_sha": "abc123"}


def F(fid, tool="semgrep", severity="high", path="app.py", line=3, rule="sqli", message="SQL injection"):
    return Finding(id=fid, tool=tool, rule_id=rule, severity=severity, path=path, line=line, message=message)


def C(fid, verdict, j="because"):
    return Classification(finding_id=fid, verdict=verdict, justification=j)


FINDINGS = [
    F("F1"),                                                            # confirmed, in diff -> inline
    F("F2", path="old.py", line=40),                                    # confirmed, outside diff -> body
    F("F3", severity="medium", line=9),                                 # false positive -> never
    F("F4", severity="medium", path="lib.py", line=2),                  # unclear -> unresolved
    F("F5", severity="low", line=11),                                   # confirmed but below threshold
    F("F6", tool="gitleaks", rule="generic-api-key", line=5,
      message="generic-api-key has detected secret sk_live_abcdef for file app.py."),  # confirmed secret
]
CLASSES = [C("F1", "confirmed"), C("F2", "confirmed"), C("F3", "false_positive", "constant input"),
           C("F4", "unclear", "cannot tell"), C("F5", "confirmed"), C("F6", "confirmed", "real key")]
IN_DIFF = {"F1", "F3", "F5", "F6"}


def build(**kw):
    args = dict(run=RUN, findings=FINDINGS, classifications=CLASSES, in_diff=IN_DIFF, min_severity="medium")
    args.update(kw)
    return build_review(**args)


def test_payload_shape_and_event():
    payload, published = build()
    assert payload["event"] == "COMMENT" and payload["commit_id"] == "abc123"
    assert set(published) == {"F1", "F2", "F4", "F6"}


def test_inline_only_confirmed_in_diff_at_threshold():
    payload, _ = build()
    inline = {(c["path"], c["line"]) for c in payload["comments"]}
    assert inline == {("app.py", 3), ("app.py", 5)}
    assert all(c["side"] == "RIGHT" for c in payload["comments"])


def test_body_sections():
    payload, _ = build()
    body = payload["body"]
    assert "old.py:40" in body                         # confirmed outside diff
    assert "Unresolved" in body and "lib.py:2" in body  # unclear
    assert "constant input" not in body and "F3" not in body  # false positive never published
    assert "app.py:11" not in body                     # below threshold


def test_secrets_masked():
    payload, _ = build()
    text = json.dumps(payload)
    assert "sk_live_abcdef" not in text
    assert "value redacted" in text


def test_mentions_defused():
    payload, _ = build(classifications=[C("F1", "confirmed", "ping @octocat now")] + CLASSES[1:])
    assert "@octocat" not in json.dumps(payload, ensure_ascii=False)


def test_never_approve_or_request_changes():
    for sev in ("low", "medium", "high"):
        payload, _ = build(min_severity=sev)
        assert payload["event"] == "COMMENT"


def test_no_findings_review_says_so():
    payload, published = build(findings=[], classifications=[], in_diff=set())
    assert published == [] and payload["comments"] == []
    assert "No confirmed" in payload["body"]


def transport(responses, seen):
    def handler(request: httpx.Request):
        seen.append(request)
        return responses.pop(0)
    return httpx.MockTransport(handler)


def test_publish_posts_to_reviews_endpoint():
    seen = []
    client = httpx.Client(transport=transport([httpx.Response(200, json={"id": 555})], seen))
    payload, _ = build()
    review_id = publish_review(client, "https://api.github.com", "tok", "alice/demo", 7, payload)
    assert review_id == 555
    req = seen[0]
    assert req.method == "POST" and req.url.path == "/repos/alice/demo/pulls/7/reviews"
    assert req.headers["authorization"] == "Bearer tok"
    assert json.loads(req.content)["event"] == "COMMENT"


def test_publish_422_falls_back_to_body_only():
    seen = []
    client = httpx.Client(transport=transport(
        [httpx.Response(422, json={"message": "Line could not be resolved"}), httpx.Response(200, json={"id": 9})], seen))
    payload, _ = build()
    assert publish_review(client, "https://api.github.com", "tok", "alice/demo", 7, payload) == 9
    retry = json.loads(seen[1].content)
    assert retry["comments"] == [] and "app.py:3" in retry["body"]


def test_publish_error_has_no_token():
    client = httpx.Client(transport=transport([httpx.Response(401, json={"message": "Bad credentials"})], []))
    payload, _ = build()
    with pytest.raises(PublishError) as e:
        publish_review(client, "https://api.github.com", "sekrit", "alice/demo", 7, payload)
    assert "401" in str(e.value) and "sekrit" not in str(e.value)


def test_finding_without_line_goes_to_body_as_path():
    fs = [F("F1", tool="osv-scanner", path="requirements.txt", line=0, rule="CVE-1", message="Package 'urllib3@1.0'")]
    payload, published = build(findings=fs, classifications=[C("F1", "confirmed")], in_diff=set())
    assert payload["comments"] == [] and published == ["F1"]
    assert "`requirements.txt`" in payload["body"] and "requirements.txt:0" not in payload["body"]


def test_findings_on_same_line_are_grouped_into_one_comment():
    fs = [F("F1", tool="osv-scanner", path="requirements.txt", line=3, rule="CVE-1", message="Package 'requests@2.19.1' a"),
          F("F2", tool="osv-scanner", path="requirements.txt", line=3, rule="CVE-2", message="Package 'requests@2.19.1' b")]
    payload, published = build(findings=fs, classifications=[C("F1", "confirmed"), C("F2", "confirmed")],
                               in_diff={"F1", "F2"})
    assert len(payload["comments"]) == 1 and published == ["F1", "F2"]
    assert "CVE-1" in payload["comments"][0]["body"] and "CVE-2" in payload["comments"][0]["body"]


def test_package_versions_are_not_mangled():
    fs = [F("F1", tool="osv-scanner", path="requirements.txt", line=0, rule="CVE-1",
            message="Package 'flask@3.1.2' is vulnerable; cc @octocat")]
    payload, _ = build(findings=fs, classifications=[C("F1", "confirmed")], in_diff=set())
    assert "flask@3.1.2" in payload["body"]
    assert "@octocat" not in payload["body"]


def test_set_commit_status_payload():
    from app.pipeline.publish import set_commit_status
    seen = []
    client = httpx.Client(transport=transport([httpx.Response(201, json={"id": 1})], seen))
    set_commit_status(client, "https://api.github.com", "tok", "alice/demo", "abc123", "success",
                      "2 confirmed " + "y" * 300, "http://localhost:5173/runs/4")
    req = seen[0]
    assert req.url.path == "/repos/alice/demo/statuses/abc123"
    body = json.loads(req.content)
    assert body["state"] == "success" and body["context"] == "pr-gate"
    assert len(body["description"]) <= 140 and body["target_url"] == "http://localhost:5173/runs/4"


def test_set_commit_status_error_raises():
    from app.pipeline.publish import set_commit_status
    client = httpx.Client(transport=transport([httpx.Response(403, json={"message": "Resource not accessible"})], []))
    with pytest.raises(PublishError, match="403"):
        set_commit_status(client, "https://api.github.com", "tok", "a/b", "s", "pending", "d", "u")
