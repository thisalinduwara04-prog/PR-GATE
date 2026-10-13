from pathlib import Path
from typing import Protocol

from app.contracts.models import Finding


class Normalizer(Protocol):
    def __call__(self, tool: str, report: dict, scan_root: Path, workspace: Path) -> list[Finding]:
        """Convert one tool report into Findings (ids unassigned: ""). Raise ValueError if malformed."""
