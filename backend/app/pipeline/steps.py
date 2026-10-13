"""The production pipeline steps, bound to settings, the database and an HTTP client."""

import json
import logging
import shutil
from pathlib import Path

import httpx

from app.config import Settings
from app.contracts.models import Classification, Finding
from app.db import Database
from app.pipeline import diff as diff_mod
from app.pipeline import workspace as workspace_mod
from app.pipeline.publish import build_review, publish_review, set_commit_status
from app.pipeline.run import DiffResult, PublishResult, Steps
from app.pipeline import proc
from app.pipeline.remote import remote_cancel, remote_invoker, remote_scan
from app.pipeline.scan import normalize_outcomes, run_scanners
from app.pipeline.verify import local_invoker, save_artifact, verify_findings

log = logging.getLogger("pr-gate.steps")


def artifacts_for(settings: Settings, run_id: int) -> Path:
    return Path(settings.artifacts_dir) / str(run_id)


def build_steps(settings: Settings, db: Database, http: httpx.Client) -> Steps:
    def remote_url(repo: str) -> str:
        return f"{settings.github_clone_base.rstrip('/')}/{repo}.git"

    def checkout(run: dict, ws: Path) -> None:
        removed = workspace_mod.checkout(remote_url(run["repo"]), run["base_sha"], run["head_sha"], ws,
                                         settings.github_token)
        db.add_tool_report(run["id"], "sanitizer", "ok", ("removed: " + ", ".join(removed)) if removed else None)

    def diff(run: dict, ws: Path) -> DiffResult:
        return diff_mod.compute_diff(ws, run["base_sha"], run["head_sha"], settings.github_token)

    def scan(run_id: int, ws: Path, reports: Path) -> list[Finding]:
        record = lambda tool, status, err: db.add_tool_report(run_id, tool, status, err)
        try:
            if not settings.scanner_url:
                return run_scanners(ws, reports, record, settings.scan_timeout_s, key=str(run_id))
            outcomes = remote_scan(http, settings.scanner_url, run_id, settings.scan_timeout_s)
            for o in outcomes:  # keep the raw reports for the download, as in local mode
                if o.report is not None:
                    (reports / o.report_name).write_text(o.report)
            return normalize_outcomes(outcomes, ws, record)
        finally:
            keep_reports(reports, artifacts_for(settings, run_id))

    def verify(run_id: int, ws: Path, findings: list[Finding], diff_text: str) -> list[Classification]:
        art = artifacts_for(settings, run_id)
        save_artifact(art, "agent_input.json", json.dumps(
            {"findings": [f.model_dump() for f in findings], "diff": diff_text}, indent=2))
        if settings.agent_url:
            invoke = remote_invoker(http, settings.agent_url, run_id)
        else:
            invoke = local_invoker(settings.claude_bin, settings.claude_code_oauth_token, ws, key=str(run_id))
        return verify_findings(ws, findings, diff_text, timeout_s=settings.verify_timeout_s, artifacts=art,
                               invoke=invoke)

    def publish(run: dict, findings: list[Finding], classifications: list[Classification],
                in_diff: set[str]) -> PublishResult:
        payload, published = build_review(run, findings, classifications, in_diff, settings.publish_min_severity)
        review_id = publish_review(http, settings.github_api_url, settings.github_token, run["repo"], run["pr"],
                                   payload)
        return PublishResult(review_id=review_id, published_ids=published)

    def status(run: dict, state: str, description: str) -> None:
        set_commit_status(http, settings.github_api_url, settings.github_token, run["repo"], run["head_sha"], state,
                          description, f"{settings.dashboard_url.rstrip('/')}/runs/{run['id']}")

    return Steps(checkout=checkout, diff=diff, scan=scan, verify=verify, publish=publish,
                 status=status if settings.commit_status_enabled else None)


def keep_reports(reports: Path, artifacts: Path) -> None:
    """Copy the raw scanner reports out of the temp dir before the run's cleanup deletes it."""
    try:
        artifacts.mkdir(parents=True, exist_ok=True)
        for sarif in reports.glob("*.sarif"):
            if sarif.is_file():
                shutil.copyfile(sarif, artifacts / sarif.name)
    except OSError:
        log.exception("could not keep scanner reports")


def make_canceller(settings: Settings, http: httpx.Client):
    """B6: stop a superseded run's tool and agent processes, wherever they run."""
    def cancel(run_id: int) -> None:
        proc.cancel(str(run_id))
        for url in (settings.scanner_url, settings.agent_url):
            if url:
                remote_cancel(http, url, run_id)
        log.info("cancel requested for superseded run %s", run_id)
    return cancel
