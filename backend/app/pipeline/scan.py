"""Run the static-analysis tools; one failing tool never stops the others."""

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from app.contracts.models import Finding
from app.normalizers.registry import normalize_report
from app.normalizers.sarif import dedupe_and_number
from app.pipeline.proc import ProcTimeout, base_env, run_proc

log = logging.getLogger("pr-gate.scan")

CONFIG_DIR = Path(__file__).resolve().parents[2] / "config"
MAX_ERROR_LEN = 500

Record = Callable[[str, str, str | None], None]  # (tool, status, error)


@dataclass
class ToolSpec:
    name: str
    argv: Callable[[Path, Path], list[str]]  # (workspace, report_path) -> argv
    report_name: str
    ok_codes: set[int]


def semgrep_argv(ws: Path, out: Path) -> list[str]:
    return ["semgrep", "scan", "--config", str(CONFIG_DIR / "semgrep-rules.yml"), f"--sarif-output={out}",
            "--metrics=off", "--disable-nosem", "--x-ignore-semgrepignore-files", "--quiet", "."]


def gitleaks_argv(ws: Path, out: Path) -> list[str]:
    empty = out.parent / "gitleaks-empty-ignore"
    empty.mkdir(exist_ok=True)
    return ["gitleaks", "dir", ".", "--redact", "--no-banner", "--report-format", "sarif", "--report-path", str(out),
            "--config", str(CONFIG_DIR / "gitleaks.toml"), "--gitleaks-ignore-path", str(empty),
            "--ignore-gitleaks-allow", "--exit-code", "0"]


def osv_argv(ws: Path, out: Path) -> list[str]:
    return ["osv-scanner", "scan", "source", "-r", "--no-ignore", "--allow-no-lockfiles",
            "--config", str(CONFIG_DIR / "osv-scanner.toml"), "--format", "sarif", "--output-file", str(out), "."]


TOOLS: list[ToolSpec] = [
    ToolSpec("semgrep", semgrep_argv, "semgrep.sarif", {0}),
    ToolSpec("gitleaks", gitleaks_argv, "gitleaks.sarif", {0}),
    ToolSpec("osv-scanner", osv_argv, "osv.sarif", {0, 1}),  # 1 = vulnerabilities found
]


def scanner_env() -> dict[str, str]:
    return base_env(SEMGREP_ENABLE_VERSION_CHECK="0", SEMGREP_SEND_METRICS="off")


@dataclass
class ToolOutcome:
    """Result of running one tool. `report` is the raw SARIF text when the tool exited normally."""
    name: str
    report_name: str
    status: str  # ok | failed | timeout
    error: str | None = None
    report: str | None = None


def execute_tools(workspace: Path, reports_dir: Path, timeout_s: float, key: str | None = None) -> list[ToolOutcome]:
    """Run every tool (no parsing). Runs inside the scanner container (B5), or locally in tests.
    Raises ProcCancelled if the run is cancelled (B6)."""
    outcomes: list[ToolOutcome] = []
    for spec in TOOLS:
        out = reports_dir / spec.report_name
        try:
            res = run_proc(spec.argv(workspace, out), workspace, scanner_env(), timeout_s, key=key)
        except ProcTimeout as e:
            outcomes.append(ToolOutcome(spec.name, spec.report_name, "timeout", str(e)))
            continue
        except OSError as e:
            outcomes.append(ToolOutcome(spec.name, spec.report_name, "failed", str(e)[:MAX_ERROR_LEN]))
            continue
        if res.returncode not in spec.ok_codes:
            tail = " | ".join(res.stderr.strip().splitlines()[-5:])
            outcomes.append(ToolOutcome(spec.name, spec.report_name, "failed",
                                        f"exit {res.returncode}: {tail}"[:MAX_ERROR_LEN]))
            continue
        try:
            report = out.read_text()
        except OSError:
            outcomes.append(ToolOutcome(spec.name, spec.report_name, "ok", None, None))  # -> parse_error later
            continue
        outcomes.append(ToolOutcome(spec.name, spec.report_name, "ok", None, report))
    return outcomes


def normalize_outcomes(outcomes: list[ToolOutcome], workspace: Path, record: Record) -> list[Finding]:
    """Turn tool outcomes into numbered, de-duplicated findings and record each tool's status. Runs in the backend."""
    findings: list[Finding] = []
    for o in outcomes:
        if o.status != "ok":
            record(o.name, o.status, o.error)
            continue
        try:
            if o.report is None:
                raise ValueError("tool produced no report")
            found = normalize_report(o.name, json.loads(o.report), workspace)
        except ValueError as e:
            record(o.name, "parse_error", str(e)[:MAX_ERROR_LEN])
            continue
        record(o.name, "ok", None)
        findings.extend(found)
    return dedupe_and_number(findings)


def run_scanners(workspace: Path, reports_dir: Path, record: Record, timeout_s: float,
                 key: str | None = None) -> list[Finding]:
    """Local mode: run the tools in this process and normalise."""
    return normalize_outcomes(execute_tools(workspace, reports_dir, timeout_s, key), workspace, record)
