"""Merge-base diff of the PR and the set of changed (added) lines per file."""

from pathlib import Path

from unidiff import PatchSet

from app.pipeline.run import DiffResult
from app.pipeline.workspace import run_git


def parse_changed_lines(diff_text: str) -> dict[str, set[int]]:
    changed: dict[str, set[int]] = {}
    for pf in PatchSet(diff_text):
        if pf.is_removed_file:
            continue
        lines = {ln.target_line_no for hunk in pf for ln in hunk if ln.is_added}
        if lines:
            changed[pf.path] = lines
    return changed


def compute_diff(workspace: Path, base_sha: str, head_sha: str, token: str = "") -> DiffResult:
    merge_base = run_git(workspace, ["merge-base", base_sha, head_sha], token).strip()
    diff_text = run_git(
        workspace,
        ["diff", "--unified=0", "--no-color", "--no-ext-diff", "--no-textconv", f"{merge_base}..{head_sha}"],
        token,
    )
    return DiffResult(merge_base=merge_base, diff_text=diff_text, changed=parse_changed_lines(diff_text))
