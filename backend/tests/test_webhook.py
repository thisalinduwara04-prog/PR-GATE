import hashlib
import hmac
import json

from tests.conftest import WEBHOOK_SECRET


def sign(body: bytes, secret: str = WEBHOOK_SECRET) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def pr_payload(action="opened", repo="alice/demo", head_sha="h1", number=7):
    return {
        "action": action,
        "number": number,
        "repository": {"full_name": repo},
        "pull_request": {
            "number": number,
            "head": {"sha": head_sha, "ref": "feat"},
            "base": {"sha": "b1", "ref": "main"},
        },
    }


def post(client, payload, event="pull_request", signature=None, raw=None):
    body = raw if raw is not None else json.dumps(payload).encode()
    headers = {"X-GitHub-Event": event, "Content-Type": "application/json",
               "X-Hub-Signature-256": signature if signature is not None else sign(body)}
    return client.post("/webhook/github", content=body, headers=headers)


def test_valid_pr_opened_creates_queued_run(client):
    r = post(client, pr_payload())
    assert r.status_code == 202
    run_id = r.json()["run_id"]
    db = client.app.state.db
    run = db.get_run(run_id)
    assert run["status"] == "queued" and run["repo"] == "alice/demo" and run["pr"] == 7
    assert run["head_sha"] == "h1" and run["base_sha"] == "b1" and run["head_ref"] == "feat"
    assert db.get_pr_latest("alice/demo", 7) == "h1"
    assert client.app.state.queue.get_nowait() == run_id


def test_synchronize_updates_pr_latest(client):
    post(client, pr_payload())
    post(client, pr_payload(action="synchronize", head_sha="h2"))
    assert client.app.state.db.get_pr_latest("alice/demo", 7) == "h2"


def test_invalid_signature_401(client):
    r = post(client, pr_payload(), signature=sign(b"other"))
    assert r.status_code == 401
    assert client.app.state.db.list_runs() == []


def test_missing_signature_401(client):
    body = json.dumps(pr_payload()).encode()
    r = client.post("/webhook/github", content=body, headers={"X-GitHub-Event": "pull_request"})
    assert r.status_code == 401


def test_wrong_secret_401(client):
    body = json.dumps(pr_payload()).encode()
    r = post(client, None, raw=body, signature=sign(body, "wrong"))
    assert r.status_code == 401


def test_ping_200(client):
    r = post(client, {"zen": "hi"}, event="ping")
    assert r.status_code == 200


def test_ignored_action(client):
    r = post(client, pr_payload(action="closed"))
    assert r.status_code == 200 and r.json()["ignored"]
    assert client.app.state.db.list_runs() == []


def test_ignored_event(client):
    r = post(client, {"ref": "x"}, event="push")
    assert r.status_code == 200 and r.json()["ignored"]


def test_unwatched_repo_ignored(client):
    r = post(client, pr_payload(repo="mallory/other"))
    assert r.status_code == 200 and r.json()["ignored"]
    assert client.app.state.db.list_runs() == []


def test_repo_match_is_case_insensitive(client):
    assert post(client, pr_payload(repo="Alice/Demo")).status_code == 202


def test_body_too_large_413(client, settings):
    big = b"x" * (settings.max_body_bytes + 1)
    r = post(client, None, raw=big)
    assert r.status_code == 413


def test_bad_json_400(client):
    r = post(client, None, raw=b"not json")
    assert r.status_code == 400


def test_empty_secret_rejects_everything(settings):
    from fastapi.testclient import TestClient
    from app.main import create_app

    settings.github_webhook_secret = ""
    with TestClient(create_app(settings, start_worker=False)) as c:
        body = json.dumps(pr_payload()).encode()
        r = c.post("/webhook/github", content=body,
                   headers={"X-GitHub-Event": "pull_request", "X-Hub-Signature-256": sign(body, "")})
        assert r.status_code == 401


def post_with_delivery(client, payload, delivery_id):
    body = json.dumps(payload).encode()
    return client.post("/webhook/github", content=body, headers={
        "X-GitHub-Event": "pull_request", "X-GitHub-Delivery": delivery_id,
        "Content-Type": "application/json", "X-Hub-Signature-256": sign(body)})


def test_duplicate_delivery_ignored(client):
    assert post_with_delivery(client, pr_payload(), "d-1").status_code == 202
    r = post_with_delivery(client, pr_payload(), "d-1")
    assert r.status_code == 200 and r.json()["ignored"] == "duplicate delivery"
    assert len(client.app.state.db.list_runs()) == 1
    assert post_with_delivery(client, pr_payload(head_sha="h2"), "d-2").status_code == 202


def test_unsigned_request_does_not_consume_delivery_id(client):
    body = json.dumps(pr_payload()).encode()
    r = client.post("/webhook/github", content=body, headers={
        "X-GitHub-Event": "pull_request", "X-GitHub-Delivery": "d-9", "X-Hub-Signature-256": "sha256=00"})
    assert r.status_code == 401
    assert post_with_delivery(client, pr_payload(), "d-9").status_code == 202
