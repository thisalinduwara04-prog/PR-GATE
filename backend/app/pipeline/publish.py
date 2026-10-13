"""Build and post one advisory COMMENT review per run. Never APPROVE or REQUEST_CHANGES."""

import re
from itertools import groupby

import httpx

from app.contracts.models import SEVERITY_RANK, Classification, Finding

GITHUB_HEADERS = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
MAX_TEXT = 1500


class PublishError(RuntimeError):
    pass


MENTION_RE = re.compile(r"(?<![\w.@])@(?=[A-Za-z0-9-])")


def clean(text: str) -> str:
    """Defuse @mentions (not package@version) and cap length; tool and model text is untrusted."""
    return MENTION_RE.sub("@​", text)[:MAX_TEXT]


def finding_message(f: Finding) -> str:
    if f.tool == "gitleaks":
        return f"Secret detected ({f.rule_id}) — value redacted"
    return f.message


def comment_body(f: Finding, c: Classification) -> str:
    return (f"**PR-Gate · {f.severity.upper()}** · `{f.tool}` · `{f.rule_id}`\n\n"
            f"{clean(finding_message(f))}\n\n**Why confirmed:** {clean(c.justification)}")


def location(f: Finding) -> str:
    return f"{f.path}:{f.line}" if f.line > 0 else f.path


def bullet(f: Finding, c: Classification) -> str:
    return (f"- **{f.severity.upper()}** `{location(f)}` · `{f.tool}` · `{f.rule_id}` — "
            f"{clean(finding_message(f))}\n  - {clean(c.justification)}")


def grouped_comments(inline: list[tuple[Finding, Classification]]) -> list[dict]:
    """One inline comment per (path, line), even when several findings share that line."""
    key = lambda fc: (fc[0].path, fc[0].line)
    comments = []
    for (path, line), group in groupby(sorted(inline, key=key), key=key):
        body = "\n\n---\n\n".join(comment_body(f, c) for f, c in group)
        comments.append({"path": path, "line": line, "side": "RIGHT", "body": body})
    return comments


def build_review(run: dict, findings: list[Finding], classifications: list[Classification],
                 in_diff: set[str], min_severity: str) -> tuple[dict, list[str]]:
    verdicts = {c.finding_id: c for c in classifications}
    floor = SEVERITY_RANK[min_severity]
    inline, outside, unresolved = [], [], []
    for f in findings:
        c = verdicts.get(f.id)
        if c is None or SEVERITY_RANK[f.severity] < floor:
            continue
        if c.verdict == "confirmed":
            (inline if f.id in in_diff else outside).append((f, c))
        elif c.verdict == "unclear":
            unresolved.append((f, c))

    lines = ["## PR-Gate security review", ""]
    if not (inline or outside or unresolved):
        lines.append("No confirmed security issues at or above "
                     f"**{min_severity}** severity were found in this pull request.")
    else:
        lines.append(f"**{len(inline) + len(outside)} confirmed** finding(s) "
                     f"({len(inline)} inline on changed lines), **{len(unresolved)} unresolved**. "
                     f"Threshold: {min_severity}.")
    if outside:
        lines += ["", "### Confirmed findings outside the changed lines", *[bullet(f, c) for f, c in outside]]
    if unresolved:
        lines += ["", "### Unresolved (needs human review)", *[bullet(f, c) for f, c in unresolved]]
    lines += ["", f"<sub>Advisory review by PR-Gate for commit {run['head_sha'][:12]}. "
                  "Findings were verified by an AI agent; false positives are hidden. "
                  "This review never blocks merging.</sub>"]

    payload = {
        "commit_id": run["head_sha"],
        "event": "COMMENT",
        "body": "\n".join(lines),
        "comments": grouped_comments(inline),
    }
    published = [f.id for f, _ in inline + outside + unresolved]
    return payload, published


def inline_to_body(payload: dict) -> dict:
    moved = [f"- `{c['path']}:{c['line']}` — {c['body'].replace(chr(10), ' ')}" for c in payload["comments"]]
    body = payload["body"] + "\n\n### Confirmed findings on changed lines\n" + "\n".join(moved)
    return {**payload, "body": body, "comments": []}


def publish_review(client: httpx.Client, api_url: str, token: str, repo: str, pr: int, payload: dict) -> int:
    url = f"{api_url.rstrip('/')}/repos/{repo}/pulls/{pr}/reviews"
    headers = {**GITHUB_HEADERS, "Authorization": f"Bearer {token}"}
    resp = client.post(url, headers=headers, json=payload, timeout=30)
    if resp.status_code == 422 and payload["comments"]:
        # An inline anchor GitHub cannot resolve: keep the review, move everything into the body.
        resp = client.post(url, headers=headers, json=inline_to_body(payload), timeout=30)
    if resp.status_code >= 300:
        try:
            detail = resp.json().get("message", "")
        except ValueError:
            detail = ""
        raise PublishError(f"GitHub review POST failed: {resp.status_code} {detail}"[:300])
    return int(resp.json()["id"])


def set_commit_status(client: httpx.Client, api_url: str, token: str, repo: str, sha: str, state: str,
                      description: str, target_url: str) -> None:
    """B2: advisory `pr-gate` commit status (pending / success / error)."""
    url = f"{api_url.rstrip('/')}/repos/{repo}/statuses/{sha}"
    headers = {**GITHUB_HEADERS, "Authorization": f"Bearer {token}"}
    body = {"state": state, "context": "pr-gate", "description": description[:140], "target_url": target_url}
    resp = client.post(url, headers=headers, json=body, timeout=30)
    if resp.status_code >= 300:
        try:
            detail = resp.json().get("message", "")
        except ValueError:
            detail = ""
        raise PublishError(f"GitHub status POST failed: {resp.status_code} {detail}"[:300])
