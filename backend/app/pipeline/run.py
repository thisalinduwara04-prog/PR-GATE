"""One PR-Gate run: checkout -> diff -> scan -> verify -> publish, with supersede checks and cleanup."""

import logging
import os
import shutil
import stat
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from app.contracts.models import Classification, Finding
from app.db import Database, now_iso

log = logging.getLogger("pr-gate.run")

MAX_ERROR_LEN = 2000


@dataclass
class DiffResult:
    merge_base: str
    diff_text: str
    changed: dict[str, set[int]] = field(default_factory=dict)


@dataclass
class PublishResult:
    review_id: int | None
    published_ids: list[str]


@dataclass
class Steps:
    checkout: Callable[[dict, Path], None]
    diff: Callable[[dict, Path], DiffResult]
    scan: Callable[[int, Path, Path], list[Finding]]
    verify: Callable[[int, Path, list[Finding], str], list[Classification]]
    publish: Callable[[dict, list[Finding], list[Classification], set[str]], PublishResult]
    # Optional B2 hook: (run, state, description) -> None. Failures are logged, never fatal.
    status: Callable[[dict, str, str], None] | None = None


class Superseded(Exception):
    pass


STATUS_DESCRIPTION_LEN = 140  # GitHub limit


def report_status(steps: Steps, run: dict, state: str, description: str) -> None:
    if steps.status is None:
        return
    try:
        steps.status(run, state, description[:STATUS_DESCRIPTION_LEN])
    except Exception:
        log.exception("run %s: could not set commit status %s", run["id"], state)


def status_summary(classifications: list[Classification]) -> str:
    confirmed = sum(c.verdict == "confirmed" for c in classifications)
    unclear = sum(c.verdict == "unclear" for c in classifications)
    return f"{confirmed} confirmed, {unclear} unresolved (advisory)"


def check_not_superseded(db: Database, run: dict) -> None:
    latest = db.get_pr_latest(run["repo"], run["pr"])
    if latest and latest != run["head_sha"]:
        raise Superseded(f"superseded by {latest}")


def force_rmtree(path: Path) -> None:
    """Remove a tree even when the sanitiser made it read-only."""
    if not os.path.lexists(path):
        return
    for root, dirs, _ in os.walk(path):
        for d in dirs:
            p = os.path.join(root, d)
            if not os.path.islink(p):
                os.chmod(p, stat.S_IRWXU)
    os.chmod(path, stat.S_IRWXU)
    shutil.rmtree(path)


def process_run(run_id: int, db: Database, steps: Steps, workspaces_dir: Path) -> None:
    run = db.get_run(run_id)
    if run is None or run["status"] != "queued":
        return
    db.update_run(run_id, status="running", started_at=now_iso())
    run_root = Path(workspaces_dir) / str(run_id)
    workspace = run_root / "src"  # git dir lives at run_root / "git"
    reports_dir = Path(tempfile.mkdtemp(prefix=f"pr-gate-reports-{run_id}-"))
    try:
        check_not_superseded(db, run)
        report_status(steps, run, "pending", "PR-Gate review running")

        steps.checkout(run, workspace)
        diff = steps.diff(run, workspace)
        db.update_run(run_id, merge_base=diff.merge_base)

        findings = steps.scan(run_id, workspace, reports_dir)
        in_diff = {f.id for f in findings if f.line in diff.changed.get(f.path, ())}
        db.insert_findings(run_id, findings, in_diff)

        classifications = steps.verify(run_id, workspace, findings, diff.diff_text) if findings else []
        db.set_verdicts(run_id, classifications)

        check_not_superseded(db, run)
        result = steps.publish(run, findings, classifications, in_diff)
        db.mark_published(run_id, result.published_ids)
        db.update_run(run_id, status="completed", review_id=result.review_id, finished_at=now_iso())
        report_status(steps, run, "success", status_summary(classifications))
    except Superseded as e:
        db.update_run(run_id, status="failed", error=str(e), finished_at=now_iso())
    except Exception as e:  # any failure: record it, publish nothing
        latest = db.get_pr_latest(run["repo"], run["pr"])
        if latest and latest != run["head_sha"]:  # B6: a newer push cancelled this run mid-step
            db.update_run(run_id, status="failed", error=f"superseded by {latest}", finished_at=now_iso())
            return
        log.exception("run %s failed", run_id)
        msg = f"{type(e).__name__}: {e}"[:MAX_ERROR_LEN]
        db.update_run(run_id, status="failed", error=msg, finished_at=now_iso())
        report_status(steps, run, "error", f"PR-Gate failed: {msg}")
    finally:
        for path in (run_root, reports_dir):
            try:
                force_rmtree(path)
            except OSError:
                log.exception("could not remove %s", path)
