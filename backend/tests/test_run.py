import os
import stat
from pathlib import Path

import pytest

from app.contracts.models import Classification, Finding, RunRequest
from app.db import Database
from app.pipeline.run import DiffResult, PublishResult, Steps, force_rmtree, process_run


@pytest.fixture
def db(tmp_path):
    d = Database(str(tmp_path / "t.db"))
    d.init()
    return d


def make_run(db, head_sha="h1"):
    db.upsert_pr_latest("alice/demo", 7, head_sha)
    return db.create_run(RunRequest(repo="alice/demo", pr=7, head_ref="feat", base_sha="b1", head_sha=head_sha))


F1 = Finding(id="F1", tool="semgrep", rule_id="r", severity="high", path="a.py", line=2, message="m")
F2 = Finding(id="F2", tool="semgrep", rule_id="r", severity="low", path="b.py", line=9, message="m")


class FakeSteps:
    """Records calls; each step can be overridden to raise or mutate state."""

    def __init__(self, db):
        self.db = db
        self.calls = []
        self.published = False
        self.seen_workspace = None

    def checkout(self, run, workspace: Path):
        self.calls.append("checkout")
        self.seen_workspace = workspace
        workspace.mkdir(parents=True)
        (workspace / "a.py").write_text("x\n")
        os.chmod(workspace / "a.py", stat.S_IREAD)
        os.chmod(workspace, stat.S_IREAD | stat.S_IEXEC)  # read-only like the real sanitiser

    def diff(self, run, workspace):
        self.calls.append("diff")
        return DiffResult(merge_base="mb", diff_text="diff", changed={"a.py": {2}})

    def scan(self, run_id, workspace, reports_dir):
        self.calls.append("scan")
        return [F1, F2]

    def verify(self, run_id, workspace, findings, diff_text):
        self.calls.append("verify")
        return [Classification(finding_id=f.id, verdict="confirmed", justification="j") for f in findings]

    def publish(self, run, findings, classifications, in_diff):
        self.calls.append("publish")
        self.published = True
        return PublishResult(review_id=99, published_ids=["F1"])

    def steps(self):
        return Steps(checkout=self.checkout, diff=self.diff, scan=self.scan, verify=self.verify, publish=self.publish)


def test_happy_path(db, tmp_path):
    run_id = make_run(db)
    fake = FakeSteps(db)
    process_run(run_id, db, fake.steps(), tmp_path / "ws")
    run = db.get_run(run_id)
    assert run["status"] == "completed", run["error"]
    assert run["review_id"] == 99 and run["merge_base"] == "mb"
    assert run["started_at"] and run["finished_at"]
    rows = {r["finding_id"]: r for r in db.findings_for_run(run_id)}
    assert rows["F1"]["in_diff"] == 1 and rows["F2"]["in_diff"] == 0
    assert rows["F1"]["verdict"] == "confirmed" and rows["F1"]["published"] == 1
    assert fake.calls == ["checkout", "diff", "scan", "verify", "publish"]
    assert not fake.seen_workspace.exists()


def test_superseded_before_start(db, tmp_path):
    run_id = make_run(db, "h1")
    db.upsert_pr_latest("alice/demo", 7, "h2")
    fake = FakeSteps(db)
    process_run(run_id, db, fake.steps(), tmp_path / "ws")
    run = db.get_run(run_id)
    assert run["status"] == "failed" and run["error"] == "superseded by h2"
    assert fake.calls == []


def test_superseded_before_publish(db, tmp_path):
    run_id = make_run(db, "h1")
    fake = FakeSteps(db)
    orig = fake.verify

    def verify_then_new_push(*a):
        db.upsert_pr_latest("alice/demo", 7, "h3")
        return orig(*a)

    fake.verify = verify_then_new_push
    process_run(run_id, db, fake.steps(), tmp_path / "ws")
    run = db.get_run(run_id)
    assert run["status"] == "failed" and run["error"] == "superseded by h3"
    assert not fake.published
    assert not fake.seen_workspace.exists()


def test_failure_marks_failed_and_removes_workspace(db, tmp_path):
    run_id = make_run(db)
    fake = FakeSteps(db)

    def boom(*a):
        raise RuntimeError("claude exploded")

    fake.verify = boom
    process_run(run_id, db, fake.steps(), tmp_path / "ws")
    run = db.get_run(run_id)
    assert run["status"] == "failed" and "claude exploded" in run["error"]
    assert not fake.published
    assert not fake.seen_workspace.exists()
    assert list((tmp_path / "ws").iterdir()) == []


def test_no_findings_skips_verify(db, tmp_path):
    run_id = make_run(db)
    fake = FakeSteps(db)
    fake.scan = lambda *a: fake.calls.append("scan") or []
    process_run(run_id, db, fake.steps(), tmp_path / "ws")
    assert db.get_run(run_id)["status"] == "completed"
    assert "verify" not in fake.calls and "publish" in fake.calls


def test_non_queued_run_is_skipped(db, tmp_path):
    run_id = make_run(db)
    db.update_run(run_id, status="completed")
    fake = FakeSteps(db)
    process_run(run_id, db, fake.steps(), tmp_path / "ws")
    assert fake.calls == []


def test_force_rmtree_handles_readonly_tree(tmp_path):
    d = tmp_path / "ro"
    (d / "sub").mkdir(parents=True)
    (d / "sub" / "f").write_text("x")
    for p in [d / "sub" / "f", d / "sub", d]:
        os.chmod(p, stat.S_IREAD | (stat.S_IEXEC if p.is_dir() else 0))
    force_rmtree(d)
    assert not d.exists()
    force_rmtree(d)  # missing path is fine


# ---- B2 commit status -----------------------------------------------------------------------

def with_status(fake):
    calls = []
    steps = fake.steps()
    steps.status = lambda run, state, desc: calls.append((state, desc))
    return steps, calls


def test_status_pending_then_success(db, tmp_path):
    run_id = make_run(db)
    fake = FakeSteps(db)
    steps, calls = with_status(fake)
    process_run(run_id, db, steps, tmp_path / "ws")
    assert [c[0] for c in calls] == ["pending", "success"]
    assert "2 confirmed" in calls[1][1] and "advisory" in calls[1][1]


def test_status_error_on_failure(db, tmp_path):
    run_id = make_run(db)
    fake = FakeSteps(db)
    fake.verify = lambda *a: (_ for _ in ()).throw(RuntimeError("x" * 500))
    steps, calls = with_status(fake)
    process_run(run_id, db, steps, tmp_path / "ws")
    assert [c[0] for c in calls] == ["pending", "error"]
    assert len(calls[1][1]) <= 140


def test_no_final_status_when_superseded(db, tmp_path):
    run_id = make_run(db, "h1")
    fake = FakeSteps(db)
    orig = fake.verify

    def verify_then_new_push(*a):
        db.upsert_pr_latest("alice/demo", 7, "h3")
        return orig(*a)

    fake.verify = verify_then_new_push
    steps, calls = with_status(fake)
    process_run(run_id, db, steps, tmp_path / "ws")
    assert [c[0] for c in calls] == ["pending"]


def test_status_failure_never_fails_run(db, tmp_path):
    run_id = make_run(db)
    fake = FakeSteps(db)
    steps = fake.steps()

    def broken(*a):
        raise RuntimeError("github down")

    steps.status = broken
    process_run(run_id, db, steps, tmp_path / "ws")
    assert db.get_run(run_id)["status"] == "completed"


def test_failure_caused_by_cancel_is_reported_as_superseded(db, tmp_path):
    """B6: when a newer push cancels a running step, the step fails; the run must end as superseded."""
    run_id = make_run(db, "h1")
    fake = FakeSteps(db)

    def verify_killed_by_new_push(*a):
        db.upsert_pr_latest("alice/demo", 7, "h9")
        raise RuntimeError("claude run cancelled (superseded)")

    fake.verify = verify_killed_by_new_push
    steps, calls = with_status(fake)
    process_run(run_id, db, steps, tmp_path / "ws")
    run = db.get_run(run_id)
    assert run["status"] == "failed" and run["error"] == "superseded by h9"
    assert [c[0] for c in calls] == ["pending"]
