"""Backend-side clients for the scanner and agent services (B5)."""

import logging

import httpx

from app.pipeline.proc import ProcCancelled
from app.pipeline.scan import TOOLS, ToolOutcome
from app.pipeline.verify import DEFAULT_MAX_TURNS, Invoke, VerifyCancelled, VerifyError, VerifyTimeout

log = logging.getLogger("pr-gate.remote")
HTTP_MARGIN_S = 60


def _detail(resp: httpx.Response) -> str:
    try:
        body = resp.json()
        return str(body.get("error") or body.get("detail") or "")[:300]
    except ValueError:
        return ""


def remote_scan(http: httpx.Client, base_url: str, run_id: int, timeout_s: float) -> list[ToolOutcome]:
    try:
        resp = http.post(f"{base_url.rstrip('/')}/scan", json={"run_id": run_id, "timeout_s": timeout_s},
                         timeout=timeout_s * len(TOOLS) + HTTP_MARGIN_S)
    except httpx.HTTPError as e:
        raise RuntimeError(f"scanner service unreachable: {type(e).__name__}") from None
    if resp.status_code == 409:
        raise ProcCancelled(f"run {run_id} was cancelled")
    if resp.status_code != 200:
        raise RuntimeError(f"scanner service error {resp.status_code}: {_detail(resp)}")
    return [ToolOutcome(**o) for o in resp.json()["outcomes"]]


def remote_invoker(http: httpx.Client, base_url: str, run_id: int, max_turns: int = DEFAULT_MAX_TURNS) -> Invoke:
    def invoke(prompt: str, timeout_s: float) -> str:
        try:
            resp = http.post(f"{base_url.rstrip('/')}/verify", timeout=timeout_s + HTTP_MARGIN_S,
                             json={"run_id": run_id, "prompt": prompt, "timeout_s": timeout_s,
                                   "max_turns": max_turns})
        except httpx.TimeoutException:
            raise VerifyTimeout(f"agent service did not answer within {timeout_s}s") from None
        except httpx.HTTPError as e:
            raise VerifyError(f"agent service unreachable: {type(e).__name__}") from None
        if resp.status_code == 504:
            raise VerifyTimeout(_detail(resp) or "claude timed out")
        if resp.status_code == 409:
            raise VerifyCancelled(_detail(resp) or "claude run cancelled")
        if resp.status_code != 200:
            raise VerifyError(f"agent service error {resp.status_code}: {_detail(resp)}")
        return resp.json()["stdout"]
    return invoke


def remote_cancel(http: httpx.Client, base_url: str, run_id: int) -> None:
    """Best effort: a failed cancel only means the run finishes and is then marked superseded."""
    try:
        http.post(f"{base_url.rstrip('/')}/cancel/{run_id}", timeout=10)
    except httpx.HTTPError as e:
        log.warning("could not cancel run %s at %s: %s", run_id, base_url, type(e).__name__)
