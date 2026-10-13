import json

import jsonschema
import pytest
from pydantic import ValidationError

from app.contracts.models import CLASSIFICATION_SCHEMA, Classification, Finding, RunRequest
from app.db import Database


def req(head_sha="h1"):
    return RunRequest(repo="alice/demo", pr=7, head_ref="feat", base_sha="b1", head_sha=head_sha)


def finding(fid="F1", **kw):
    base = dict(id=fid, tool="semgrep", rule_id="sqli", severity="high", path="app.py", line=3, message="SQL injection")
    base.update(kw)
    return Finding(**base)


@pytest.fixture
def db(tmp_path):
    d = Database(str(tmp_path / "t.db"))
    d.init()
    return d


def test_finding_rejects_bad_severity():
    with pytest.raises(ValidationError):
        finding(severity="critical")


def test_classification_rejects_bad_verdict():
    with pytest.raises(ValidationError):
        Classification(finding_id="F1", verdict="maybe", justification="x")


def test_schema_accepts_valid_and_rejects_invalid():
    ok = {"results": [{"finding_id": "F1", "verdict": "confirmed", "justification": "reachable"}]}
    jsonschema.validate(ok, CLASSIFICATION_SCHEMA)
    bad = {"results": [{"finding_id": "F1", "verdict": "nope", "justification": "x"}]}
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(bad, CLASSIFICATION_SCHEMA)
    extra = {"results": [], "note": "x"}
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(extra, CLASSIFICATION_SCHEMA)


def test_wal_mode(db):
    with db.connect() as c:
        assert c.execute("PRAGMA journal_mode").fetchone()[0] == "wal"


def test_run_lifecycle(db):
    run_id = db.create_run(req())
    run = db.get_run(run_id)
    assert run["status"] == "queued" and run["head_sha"] == "h1"
    db.update_run(run_id, status="running", merge_base="mb")
    assert db.get_run(run_id)["status"] == "running"
    assert db.run_ids_with_status("running") == [run_id]
    with pytest.raises(ValueError):
        db.update_run(run_id, **{"status = 'x'; --": "y"})


def test_pr_latest_upsert(db):
    db.upsert_pr_latest("alice/demo", 7, "h1")
    db.upsert_pr_latest("alice/demo", 7, "h2")
    assert db.get_pr_latest("alice/demo", 7) == "h2"
    assert db.get_pr_latest("alice/demo", 8) is None


def test_findings_verdicts_and_published(db):
    run_id = db.create_run(req())
    db.insert_findings(run_id, [finding("F1"), finding("F2", line=9)], in_diff={"F1"})
    db.set_verdicts(run_id, [Classification(finding_id="F1", verdict="confirmed", justification="yes"),
                             Classification(finding_id="F2", verdict="false_positive", justification="no")])
    db.mark_published(run_id, ["F1"])
    rows = {r["finding_id"]: r for r in db.findings_for_run(run_id)}
    assert rows["F1"]["in_diff"] == 1 and rows["F1"]["verdict"] == "confirmed" and rows["F1"]["published"] == 1
    assert rows["F2"]["in_diff"] == 0 and rows["F2"]["published"] == 0


def test_message_with_sql_is_stored_literally(db):
    run_id = db.create_run(req())
    evil = "x'); DROP TABLE runs; --"
    db.insert_findings(run_id, [finding(message=evil)], in_diff=set())
    assert db.findings_for_run(run_id)[0]["message"] == evil
    assert db.get_run(run_id) is not None


def test_tool_reports(db):
    run_id = db.create_run(req())
    db.add_tool_report(run_id, "semgrep", "ok", None)
    db.add_tool_report(run_id, "gitleaks", "error", "timeout")
    assert [(r["tool"], r["status"]) for r in db.tool_reports_for_run(run_id)] == [("semgrep", "ok"), ("gitleaks", "error")]


def test_record_delivery_is_idempotent(db):
    assert db.record_delivery("abc-1") is True
    assert db.record_delivery("abc-1") is False
    assert db.record_delivery("abc-2") is True


def test_run_exists(db):
    db.create_run(req("h1"))
    assert db.run_exists("alice/demo", 7, "h1")
    assert not db.run_exists("alice/demo", 7, "h2")
    assert not db.run_exists("alice/demo", 8, "h1")


def test_migration_adds_label_columns_to_old_db(tmp_path):
    import sqlite3
    path = tmp_path / "old.db"
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE runs (id INTEGER PRIMARY KEY AUTOINCREMENT, repo TEXT NOT NULL, pr INTEGER NOT NULL,
            head_ref TEXT NOT NULL, base_sha TEXT NOT NULL, head_sha TEXT NOT NULL, merge_base TEXT,
            status TEXT NOT NULL, error TEXT, created_at TEXT NOT NULL, started_at TEXT, finished_at TEXT,
            review_id INTEGER);
        CREATE TABLE findings (id INTEGER PRIMARY KEY AUTOINCREMENT, run_id INTEGER NOT NULL, finding_id TEXT NOT NULL,
            tool TEXT NOT NULL, rule_id TEXT NOT NULL, severity TEXT NOT NULL, path TEXT NOT NULL, line INTEGER NOT NULL,
            message TEXT NOT NULL, in_diff INTEGER NOT NULL DEFAULT 0, verdict TEXT, justification TEXT,
            published INTEGER NOT NULL DEFAULT 0, UNIQUE (run_id, finding_id));
        INSERT INTO runs (repo, pr, head_ref, base_sha, head_sha, status, created_at) VALUES ('a/b', 1, 'x', 'b', 'h', 'completed', 't');
        INSERT INTO findings (run_id, finding_id, tool, rule_id, severity, path, line, message) VALUES (1, 'F1', 'semgrep', 'r', 'high', 'a.py', 1, 'm');
    """)
    conn.commit(); conn.close()
    d = Database(str(path))
    d.init()
    d.init()  # idempotent
    row = d.findings_for_run(1)[0]
    assert row["human_label"] is None and "labelled_at" in row
    assert d.record_delivery("x") is True


def test_set_human_label(db):
    run_id = db.create_run(req())
    db.insert_findings(run_id, [finding("F1")], in_diff=set())
    fid = db.findings_for_run(run_id)[0]["id"]
    assert db.set_human_label(fid, "false_positive") is True
    row = db.findings_for_run(run_id)[0]
    assert row["human_label"] == "false_positive" and row["labelled_at"]
    assert db.set_human_label(fid, None) is True
    assert db.findings_for_run(run_id)[0]["human_label"] is None
    assert db.set_human_label(9999, "confirmed") is False
