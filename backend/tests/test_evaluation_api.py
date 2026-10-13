import csv
import io

import pytest

from app.contracts.models import Classification, Finding, RunRequest

DASH = {"Origin": "http://localhost:5173", "Content-Type": "application/json"}


def seed(db):
    run = db.create_run(RunRequest(repo="alice/demo", pr=3, head_ref="f", base_sha="b", head_sha="h" * 40))
    fs = [Finding(id=f"F{i}", tool=t, rule_id="r", severity="high", path="a.py", line=i, message=f"m{i}")
          for i, t in enumerate(["semgrep", "semgrep", "semgrep", "gitleaks", "osv-scanner"], start=1)]
    db.insert_findings(run, fs, in_diff={"F1"})
    db.set_verdicts(run, [Classification(finding_id="F1", verdict="confirmed", justification="j1"),
                          Classification(finding_id="F2", verdict="confirmed", justification="j2"),
                          Classification(finding_id="F3", verdict="false_positive", justification="j3"),
                          Classification(finding_id="F4", verdict="confirmed", justification="j4")])
    # F5 has no agent verdict (e.g. failed run) -> never in the queue or metrics
    return {r["finding_id"]: r["id"] for r in db.findings_for_run(run)}


def label(client, row_id, value, headers=DASH):
    return client.post(f"/api/findings/{row_id}/label", json={"label": value}, headers=headers)


def test_label_roundtrip(client):
    ids = seed(client.app.state.db)
    r = label(client, ids["F1"], "confirmed")
    assert r.status_code == 200 and r.json()["human_label"] == "confirmed"
    run_id = client.get("/api/runs").json()[0]["id"]
    rows = {f["finding_id"]: f for f in client.get(f"/api/runs/{run_id}").json()["findings"]}
    assert rows["F1"]["human_label"] == "confirmed"
    assert label(client, ids["F1"], None).status_code == 200
    assert client.get(f"/api/runs/{run_id}").json()["findings"][0]["human_label"] is None


@pytest.mark.parametrize("headers,status", [
    ({"Content-Type": "application/json"}, 403),                                   # no Origin
    ({"Origin": "http://evil.com", "Content-Type": "application/json"}, 403),      # foreign Origin
    ({"Origin": "http://localhost:5173", "Content-Type": "text/plain"}, 415),       # simple (no-preflight) request
])
def test_label_csrf_guards(client, headers, status):
    ids = seed(client.app.state.db)
    r = client.post(f"/api/findings/{ids['F1']}/label", content=b'{"label": "confirmed"}', headers=headers)
    assert r.status_code == status


def test_label_rejects_foreign_host(client):
    ids = seed(client.app.state.db)
    assert label(client, ids["F1"], "confirmed", {**DASH, "Host": "x.ngrok-free.dev"}).status_code == 403


def test_label_validation(client):
    ids = seed(client.app.state.db)
    assert label(client, ids["F1"], "maybe").status_code == 400
    assert label(client, 99999, "confirmed").status_code == 404


def test_cors_preflight_allows_post_from_dashboard(client):
    r = client.options("/api/findings/1/label", headers={
        "Origin": "http://localhost:5173", "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "content-type"})
    assert r.status_code == 200 and "POST" in r.headers["access-control-allow-methods"]


def test_evaluation_metrics_and_queue(client):
    ids = seed(client.app.state.db)
    label(client, ids["F1"], "confirmed")       # agent confirmed, human agrees
    label(client, ids["F2"], "false_positive")  # agent confirmed, human disagrees
    label(client, ids["F3"], "false_positive")  # agent FP, human agrees
    ev = client.get("/api/evaluation").json()
    o = ev["overall"]
    assert o["labelled"] == 3
    assert o["agreement"] == pytest.approx(66.7, abs=0.1)
    assert o["agent_precision"] == 50.0
    assert o["fp_filter_accuracy"] == 100.0
    by_tool = {t["tool"]: t for t in ev["by_tool"]}
    assert by_tool["semgrep"]["labelled"] == 3 and "gitleaks" not in by_tool
    queue_ids = {q["finding_id"] for q in ev["queue"]}
    assert queue_ids == {"F4"}  # classified but unlabelled; F5 has no verdict
    q = ev["queue"][0]
    assert q["repo"] == "alice/demo" and q["pr"] == 3 and q["justification"] == "j4"


def test_evaluation_empty(client):
    ev = client.get("/api/evaluation").json()
    assert ev["overall"]["labelled"] == 0 and ev["overall"]["agent_precision"] is None and ev["queue"] == []


def test_csv_export(client):
    ids = seed(client.app.state.db)
    label(client, ids["F1"], "confirmed")
    r = client.get("/api/export/findings.csv")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    assert "attachment" in r.headers["content-disposition"]
    rows = list(csv.DictReader(io.StringIO(r.text)))
    assert len(rows) == 5
    f1 = next(x for x in rows if x["finding_id"] == "F1")
    assert f1["repo"] == "alice/demo" and f1["verdict"] == "confirmed" and f1["human_label"] == "confirmed"
    assert f1["in_diff"] == "1" and f1["justification"] == "j1"


def test_csv_neutralises_formula_injection(client):
    db = client.app.state.db
    run = db.create_run(RunRequest(repo="a/b", pr=1, head_ref="f", base_sha="b", head_sha="h"))
    db.insert_findings(run, [Finding(id="F1", tool="semgrep", rule_id="r", severity="low", path="a.py", line=1,
                                     message="=HYPERLINK(\"http://evil\")")], in_diff=set())
    rows = list(csv.DictReader(io.StringIO(client.get("/api/export/findings.csv").text)))
    assert rows[0]["message"].startswith("'=")
