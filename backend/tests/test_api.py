from pathlib import Path

from app.contracts.models import Classification, Finding, RunRequest


def seed(db):
    r1 = db.create_run(RunRequest(repo="alice/demo", pr=1, head_ref="a", base_sha="b", head_sha="h1"))
    r2 = db.create_run(RunRequest(repo="alice/demo", pr=2, head_ref="c", base_sha="b", head_sha="h2"))
    fs = [
        Finding(id="F1", tool="semgrep", rule_id="sqli", severity="high", path="a.py", line=1, message="m"),
        Finding(id="F2", tool="semgrep", rule_id="md5", severity="low", path="a.py", line=2, message="m"),
        Finding(id="F3", tool="gitleaks", rule_id="key", severity="high", path="k.py", line=1, message="m"),
    ]
    db.insert_findings(r1, fs, in_diff={"F1"})
    db.set_verdicts(r1, [Classification(finding_id="F1", verdict="confirmed", justification="yes"),
                         Classification(finding_id="F2", verdict="false_positive", justification="no"),
                         Classification(finding_id="F3", verdict="unclear", justification="?")])
    db.mark_published(r1, ["F1", "F3"])
    db.add_tool_report(r1, "semgrep", "ok", None)
    db.update_run(r1, status="completed", review_id=10)
    db.update_run(r2, status="failed", error="superseded by h9")
    return r1, r2


def test_runs_empty(client):
    assert client.get("/api/runs").json() == []


def test_runs_newest_first_with_counts(client):
    r1, r2 = seed(client.app.state.db)
    runs = client.get("/api/runs").json()
    assert [r["id"] for r in runs] == [r2, r1]
    one = runs[1]
    assert one["status"] == "completed"
    assert one["severity_counts"] == {"high": 2, "medium": 0, "low": 1}
    assert one["verdict_counts"] == {"confirmed": 1, "false_positive": 1, "unclear": 1}
    assert runs[0]["error"] == "superseded by h9"


def test_run_detail_includes_all_findings(client):
    r1, _ = seed(client.app.state.db)
    d = client.get(f"/api/runs/{r1}").json()
    assert d["run"]["id"] == r1 and d["run"]["review_id"] == 10
    by = {f["finding_id"]: f for f in d["findings"]}
    assert by["F2"]["verdict"] == "false_positive" and by["F2"]["published"] is False
    assert by["F1"]["published"] is True and by["F1"]["in_diff"] is True
    assert by["F1"]["justification"] == "yes"
    assert d["tool_reports"][0]["tool"] == "semgrep"


def test_run_detail_404(client):
    assert client.get("/api/runs/999").status_code == 404


def test_tool_stats(client):
    seed(client.app.state.db)
    stats = {s["tool"]: s for s in client.get("/api/stats/tools").json()}
    s = stats["semgrep"]
    assert (s["total"], s["confirmed"], s["false_positive"], s["unclear"]) == (2, 1, 1, 0)
    assert s["confirmed_pct"] == 50.0 and s["false_positive_pct"] == 50.0
    assert stats["gitleaks"]["unclear"] == 1 and stats["gitleaks"]["confirmed_pct"] == 0.0


def test_api_is_read_only(client):
    assert client.post("/api/runs").status_code == 405


def test_artifacts_zip(client, settings):
    import io
    import zipfile
    r1, _ = seed(client.app.state.db)
    assert client.get(f"/api/runs/{r1}").json()["has_artifacts"] is False
    assert client.get(f"/api/runs/{r1}/artifacts.zip").status_code == 404
    d = Path(settings.artifacts_dir) / str(r1)
    d.mkdir(parents=True)
    (d / "semgrep.sarif").write_text('{"runs": []}')
    (d / "agent_input.json").write_text('{"findings": []}')
    assert client.get(f"/api/runs/{r1}").json()["has_artifacts"] is True
    r = client.get(f"/api/runs/{r1}/artifacts.zip")
    assert r.status_code == 200 and r.headers["content-type"] == "application/zip"
    assert "attachment" in r.headers["content-disposition"] and f"run-{r1}" in r.headers["content-disposition"]
    names = zipfile.ZipFile(io.BytesIO(r.content)).namelist()
    assert sorted(names) == ["agent_input.json", "semgrep.sarif"]


def test_artifacts_zip_unknown_run(client):
    assert client.get("/api/runs/999/artifacts.zip").status_code == 404
