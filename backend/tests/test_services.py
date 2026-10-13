"""B5: the scanner and agent services, and the backend's clients for them."""

import json
import shutil
import threading
import time
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from app.contracts.models import Finding
from app.pipeline import proc
from app.pipeline.remote import remote_cancel, remote_invoker, remote_scan
from app.pipeline.verify import VerifyCancelled, VerifyError, VerifyTimeout, verify_findings
from app.services.agent import create_agent_app
from app.services.scanner import create_scanner_app
from tests.fake_claude import make_fake_claude

SAMPLE = Path(__file__).parent / "fixtures" / "sample"
FINDINGS = [Finding(id="F1", tool="semgrep", rule_id="r", severity="high", path="app.py", line=1, message="m")]


@pytest.fixture
def workspaces(tmp_path):
    ws = tmp_path / "workspaces" / "41" / "src"
    shutil.copytree(SAMPLE, ws)
    return tmp_path / "workspaces"


def forward_to(client: TestClient):
    """httpx transport that forwards backend requests to an in-process service (like the compose network)."""
    def handler(request: httpx.Request):
        r = client.request(request.method, request.url.path, content=request.content,
                           headers={"content-type": request.headers.get("content-type", "")})
        return httpx.Response(r.status_code, content=r.content, headers={"content-type": r.headers["content-type"]})
    return httpx.MockTransport(handler)


# ---- scanner service ------------------------------------------------------------------------

@pytest.mark.skipif(shutil.which("semgrep") is None, reason="scanners not installed")
def test_scanner_service_returns_raw_reports(workspaces):
    svc = TestClient(create_scanner_app(str(workspaces)))
    assert svc.get("/health").json() == {"status": "ok"}
    http = httpx.Client(transport=forward_to(svc))
    outcomes = remote_scan(http, "http://scanner:9000", 41, timeout_s=120)
    by_tool = {o.name: o for o in outcomes}
    assert by_tool["semgrep"].status == "ok" and json.loads(by_tool["semgrep"].report)["runs"]
    assert by_tool["gitleaks"].status == "ok"
    assert set(by_tool) == {"semgrep", "gitleaks", "osv-scanner"}


def test_scanner_service_unknown_run_404(workspaces):
    svc = TestClient(create_scanner_app(str(workspaces)))
    assert svc.post("/scan", json={"run_id": 999, "timeout_s": 10}).status_code == 404


def test_scanner_rejects_non_integer_run_id(workspaces):
    svc = TestClient(create_scanner_app(str(workspaces)))
    assert svc.post("/scan", json={"run_id": "../../etc", "timeout_s": 10}).status_code == 422


def test_scanner_cancelled_run_returns_409_and_client_raises(workspaces):
    svc = TestClient(create_scanner_app(str(workspaces)))
    http = httpx.Client(transport=forward_to(svc))
    remote_cancel(http, "http://scanner:9000", 41)
    with pytest.raises(proc.ProcCancelled):
        remote_scan(http, "http://scanner:9000", 41, timeout_s=10)


# ---- agent service --------------------------------------------------------------------------

def agent(tmp_path, workspaces, mode, token="tok"):
    exe, record = make_fake_claude(tmp_path, mode)
    svc = TestClient(create_agent_app(str(workspaces), claude_bin=str(exe), oauth_token=token))
    return svc, httpx.Client(transport=forward_to(svc)), record


def test_agent_service_runs_claude_in_workspace(tmp_path, workspaces):
    svc, http, record = agent(tmp_path, workspaces, "mixed")
    assert svc.get("/health").json() == {"status": "ok"}
    result = verify_findings(workspaces / "41" / "src", FINDINGS, "diff", timeout_s=30,
                             invoke=remote_invoker(http, "http://agent:9100", 41))
    assert result[0].finding_id == "F1"
    rec = json.loads(record.read_text())
    assert rec["cwd"].endswith("/41/src") and "--restricted" in rec["argv"]
    assert rec["env"]["CLAUDE_CODE_OAUTH_TOKEN"] == "tok"


def test_agent_service_timeout_maps_to_verify_timeout(tmp_path, workspaces):
    _, http, _ = agent(tmp_path, workspaces, "sleep")
    with pytest.raises(VerifyTimeout):
        remote_invoker(http, "http://agent:9100", 41)("prompt", 1)


def test_agent_service_cancel(tmp_path, workspaces):
    _, http, _ = agent(tmp_path, workspaces, "sleep")
    errors = []

    def call():
        try:
            remote_invoker(http, "http://agent:9100", 42)("prompt", 60)
        except Exception as e:  # noqa: BLE001
            errors.append(e)

    shutil.copytree(SAMPLE, workspaces / "42" / "src")
    t = threading.Thread(target=call)
    t.start()
    deadline = time.time() + 10
    while not proc.is_running("42"):
        assert time.time() < deadline
        time.sleep(0.05)
    remote_cancel(http, "http://agent:9100", 42)
    t.join(10)
    assert not t.is_alive() and isinstance(errors[0], VerifyCancelled)


def test_agent_service_without_token_is_503(tmp_path, workspaces):
    _, http, _ = agent(tmp_path, workspaces, "mixed", token="")
    with pytest.raises(VerifyError, match="CLAUDE_CODE_OAUTH_TOKEN"):
        remote_invoker(http, "http://agent:9100", 41)("prompt", 10)


def test_agent_unreachable_is_verify_error():
    def boom(request):
        raise httpx.ConnectError("no route")
    http = httpx.Client(transport=httpx.MockTransport(boom))
    with pytest.raises(VerifyError, match="unreachable"):
        remote_invoker(http, "http://agent:9100", 1)("p", 5)
