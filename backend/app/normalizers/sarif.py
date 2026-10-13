"""One SARIF parser; per-tool behaviour comes from a severity function and an optional line locator."""

import re
from pathlib import Path
from typing import Callable
from urllib.parse import unquote, urlparse

from app.contracts.models import Finding, Severity

MAX_MESSAGE_LEN = 1000
LEVEL_SEVERITY: dict[str, Severity] = {"error": "high", "warning": "medium", "note": "low", "none": "low"}

SeverityFn = Callable[[dict, dict], Severity]          # (result, rule) -> severity
LineFn = Callable[[dict, str, Path], int | None]       # (result, rel_path, workspace) -> line


def result_level(result: dict, rule: dict) -> str:
    return result.get("level") or (rule.get("defaultConfiguration") or {}).get("level") or "warning"


def level_severity(result: dict, rule: dict) -> Severity:
    return LEVEL_SEVERITY.get(result_level(result, rule), "medium")


def always_high(result: dict, rule: dict) -> Severity:
    return "high"


def cvss_or_level_severity(result: dict, rule: dict) -> Severity:
    try:
        score = float((rule.get("properties") or {}).get("security-severity"))
    except (TypeError, ValueError):
        return level_severity(result, rule)
    return "high" if score >= 7.0 else "medium" if score >= 4.0 else "low"


PACKAGE_RE = re.compile(r"Package '([^'@]+)@")


def osv_package_line(result: dict, rel_path: str, workspace: Path) -> int | None:
    """OSV reports have no region; find the manifest line that names the package."""
    m = PACKAGE_RE.search((result.get("message") or {}).get("text", ""))
    if not m:
        return None
    name = m.group(1).lower().replace("_", "-")
    try:
        lines = (workspace / rel_path).read_text(errors="replace").splitlines()
    except OSError:
        return None
    pat = re.compile(rf"""^\s*["']?{re.escape(name)}["']?\s*([=<>!~;:\[@ ,]|$)""", re.IGNORECASE)
    for i, text in enumerate(lines, start=1):
        if pat.search(text.replace("_", "-")):
            return i
    return None


def relative_path(uri: str, scan_root: Path) -> str:
    if uri.startswith("file://"):
        uri = unquote(urlparse(uri).path)
    p = Path(uri)
    if p.is_absolute():
        try:
            p = p.relative_to(scan_root)
        except ValueError:
            pass
    return p.as_posix().removeprefix("./")


def make_sarif_normalizer(severity: SeverityFn, locate_line: LineFn | None = None,
                          rule_id: Callable[[str], str] = lambda r: r):
    def normalize(tool: str, report: dict, scan_root: Path, workspace: Path) -> list[Finding]:
        try:
            runs = report["runs"]
            findings = []
            for run in runs:
                rules = {r["id"]: r for r in run.get("tool", {}).get("driver", {}).get("rules", []) or []}
                for res in run.get("results", []) or []:
                    rule = rules.get(res.get("ruleId", ""), {})
                    loc = (res.get("locations") or [{}])[0].get("physicalLocation", {})
                    uri = loc.get("artifactLocation", {}).get("uri", "")
                    if not uri:
                        continue
                    path = relative_path(uri, scan_root)
                    line = (loc.get("region") or {}).get("startLine")
                    if line is None and locate_line:
                        line = locate_line(res, path, workspace)
                    findings.append(Finding(
                        id="",
                        tool=tool,
                        rule_id=rule_id(res.get("ruleId") or "unknown"),
                        severity=severity(res, rule),
                        path=path,
                        line=int(line or 0),  # 0 = no specific line (e.g. transitive dependency)
                        message=((res.get("message") or {}).get("text") or "")[:MAX_MESSAGE_LEN],
                    ))
            return findings
        except (KeyError, TypeError, AttributeError, ValueError) as e:
            raise ValueError(f"malformed SARIF: {e}") from e

    return normalize


def dedupe_and_number(findings: list[Finding]) -> list[Finding]:
    seen, out = set(), []
    for f in findings:
        key = (f.tool, f.rule_id, f.path, f.line)
        if key in seen:
            continue
        seen.add(key)
        out.append(f.model_copy(update={"id": f"F{len(out) + 1}"}))
    return out
