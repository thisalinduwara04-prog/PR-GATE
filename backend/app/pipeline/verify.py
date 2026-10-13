"""Ask Claude (read-only, sandboxed CLI) to classify each finding, then validate the answer strictly."""

import json
import logging
import re
import secrets
import time
from pathlib import Path
from typing import Callable

import jsonschema

from app.contracts.models import CLASSIFICATION_SCHEMA, Classification, Finding
from app.pipeline.proc import ProcCancelled, ProcTimeout, base_env, run_proc

PROMPT_PATH = Path(__file__).resolve().parents[1] / "prompts" / "verify.md"
MAX_DIFF_CHARS = 150_000
DEFAULT_MAX_TURNS = 30
MAX_ATTEMPTS = 3          # only transient API errors are retried
RETRY_BACKOFF_S = 15      # seconds, multiplied by the attempt number

DISALLOWED_TOOLS = [
    "Bash", "Edit", "Write", "NotebookEdit", "WebFetch", "WebSearch", "Task",
    # Belt and braces on top of --restricted: never read process env, the DB, home or app dirs.
    "Read(//proc/**)", "Read(//data/**)", "Read(//home/**)", "Read(//app/**)", "Read(//etc/**)",
    "Grep(//proc/**)", "Grep(//data/**)", "Grep(//home/**)",
]


class VerifyError(RuntimeError):
    pass


class TransientVerifyError(VerifyError):
    """The CLI hit an API error (overload, rate limit); worth retrying."""


class VerifyTimeout(VerifyError):
    pass


class VerifyCancelled(VerifyError):
    pass


def build_prompt(findings: list[Finding], diff_text: str) -> str:
    if len(diff_text) > MAX_DIFF_CHARS:
        diff_text = diff_text[:MAX_DIFF_CHARS] + f"\n[diff truncated at {MAX_DIFF_CHARS} characters]"
    values = {
        "NONCE": secrets.token_hex(8),
        "FINDINGS": json.dumps([f.model_dump() for f in findings], indent=1),
        "DIFF": diff_text,
    }
    # Single pass, so placeholder-looking text inside the data is never expanded.
    return re.sub(r"\{\{(NONCE|FINDINGS|DIFF)\}\}", lambda m: values[m.group(1)], PROMPT_PATH.read_text())


def claude_argv(claude_bin: str, max_turns: int) -> list[str]:
    return [
        claude_bin, "-p",
        "--restricted",
        "--tools", "Read,Grep,Glob",
        "--disallowedTools", ",".join(DISALLOWED_TOOLS),
        "--permission-mode", "dontAsk",
        "--strict-mcp-config",
        "--no-session-persistence",
        "--max-turns", str(max_turns),
        "--output-format", "json",
        "--json-schema", json.dumps(CLASSIFICATION_SCHEMA),
    ]


def parse_cli_output(stdout: str) -> dict:
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError:
        raise VerifyError("claude output is not JSON") from None
    if not isinstance(payload, dict):
        raise VerifyError("claude output is not a JSON object")
    if payload.get("is_error") or payload.get("subtype") != "success":
        detail = " ".join(str(payload.get("result") or "").split())[:200]
        error_cls = TransientVerifyError if payload.get("terminal_reason") == "api_error" else VerifyError
        raise error_cls(
            f"claude run did not succeed (subtype={payload.get('subtype')}, "
            f"terminal_reason={payload.get('terminal_reason')}, api_error_status={payload.get('api_error_status')})"
            + (f": {detail}" if detail else ""))
    out = payload.get("structured_output")
    if out is None:
        try:
            out = json.loads(payload.get("result") or "")
        except json.JSONDecodeError:
            raise VerifyError("claude result is not JSON") from None
    return out


def validate(output: dict, findings: list[Finding]) -> list[Classification]:
    try:
        jsonschema.validate(output, CLASSIFICATION_SCHEMA)
    except jsonschema.ValidationError as e:
        raise VerifyError(f"schema violation: {e.message}") from None
    ids = [r["finding_id"] for r in output["results"]]
    expected = {f.id for f in findings}
    dupes = sorted({i for i in ids if ids.count(i) > 1})
    missing = sorted(expected - set(ids))
    unknown = sorted(set(ids) - expected)
    if dupes or missing or unknown:
        raise VerifyError(f"id coverage mismatch: missing={missing} unknown={unknown} duplicate={dupes}")
    by_id = {r["finding_id"]: Classification(**r) for r in output["results"]}
    return [by_id[f.id] for f in findings]


Invoke = Callable[[str, float], str]  # (prompt, timeout_s) -> raw claude CLI stdout


def claude_env(oauth_token: str) -> dict[str, str]:
    return base_env(
        CLAUDE_CODE_OAUTH_TOKEN=oauth_token,
        DISABLE_AUTOUPDATER="1",
        DISABLE_TELEMETRY="1",
        CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC="1",
    )


def local_invoker(claude_bin: str, oauth_token: str, workspace: Path, max_turns: int = DEFAULT_MAX_TURNS,
                  key: str | None = None) -> Invoke:
    """Run the claude CLI as a child process here (inside the agent container, or locally in tests)."""
    if not oauth_token:
        raise VerifyError("CLAUDE_CODE_OAUTH_TOKEN is not set")
    env = claude_env(oauth_token)
    return lambda prompt, timeout_s: run_claude(claude_bin, max_turns, workspace, env, timeout_s, prompt, key)


def verify_findings(workspace: Path, findings: list[Finding], diff_text: str, *, timeout_s: float,
                    claude_bin: str = "claude", oauth_token: str = "", max_turns: int = DEFAULT_MAX_TURNS,
                    artifacts: Path | None = None, invoke: Invoke | None = None) -> list[Classification]:
    """artifacts: optional directory where the exact prompt and raw CLI output are saved for download.
    invoke: how to run claude; defaults to a local child process (the backend passes the remote agent, B5)."""
    if invoke is None:
        invoke = local_invoker(claude_bin, oauth_token, workspace, max_turns)
    prompt = build_prompt(findings, diff_text)
    save_artifact(artifacts, "agent_prompt.md", prompt)
    deadline = time.monotonic() + timeout_s
    for attempt in range(1, MAX_ATTEMPTS + 1):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise VerifyTimeout(f"claude timed out after {timeout_s}s")
        try:
            stdout = invoke(prompt, remaining)
            save_artifact(artifacts, "agent_output.json", stdout)
            return validate(parse_cli_output(stdout), findings)
        except TransientVerifyError as e:
            wait = RETRY_BACKOFF_S * attempt
            if attempt == MAX_ATTEMPTS or deadline - time.monotonic() <= wait:
                raise VerifyError(f"{e} (after {attempt} attempt(s))") from None
            time.sleep(wait)
    raise AssertionError("unreachable")


def run_claude(claude_bin: str, max_turns: int, workspace: Path, env: dict[str, str], timeout_s: float,
               prompt: str, key: str | None = None) -> str:
    try:
        res = run_proc(claude_argv(claude_bin, max_turns), workspace, env, timeout_s, input_text=prompt, key=key)
    except ProcTimeout as e:
        raise VerifyTimeout(f"claude {e}") from None
    except ProcCancelled:
        raise VerifyCancelled("claude run cancelled (superseded)") from None
    except OSError as e:
        raise VerifyError(f"could not start claude: {e}") from None
    if res.returncode != 0 and not res.stdout.strip():
        raise VerifyError(f"claude exited {res.returncode}: {res.stderr.strip()[-300:]}")
    return res.stdout


def save_artifact(directory: Path | None, name: str, text: str) -> None:
    """Best effort: saving a download artifact must never fail a run."""
    if directory is None:
        return
    try:
        directory.mkdir(parents=True, exist_ok=True)
        (directory / name).write_text(text)
    except OSError:
        logging.getLogger("pr-gate.verify").exception("could not save artifact %s", name)
