"""Tool name -> normaliser. Adding a tool means adding one entry here (and a ToolSpec in scan.py)."""

from pathlib import Path

from app.contracts.models import Finding
from app.normalizers.base import Normalizer
from app.normalizers.sarif import (
    always_high,
    cvss_or_level_severity,
    level_severity,
    make_sarif_normalizer,
    osv_package_line,
)

NORMALIZERS: dict[str, Normalizer] = {
    # Semgrep prefixes local rule ids with the config path ("app.config.<id>").
    "semgrep": make_sarif_normalizer(level_severity, rule_id=lambda r: r.rsplit(".", 1)[-1]),
    "gitleaks": make_sarif_normalizer(always_high),
    "osv-scanner": make_sarif_normalizer(cvss_or_level_severity, locate_line=osv_package_line),
}


def normalize_report(tool: str, report: dict, scan_root: Path, workspace: Path | None = None) -> list[Finding]:
    return NORMALIZERS[tool](tool, report, scan_root, workspace or scan_root)
