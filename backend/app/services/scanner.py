"""Scanner service (B5): runs Semgrep, Gitleaks and OSV-Scanner on a run's checkout.

Runs in its own container with no secrets, the workspaces volume mounted read-only, and no route from the
internet (only the backend can reach it on the internal compose network). Returns the raw reports; the
backend normalises them.
"""

import tempfile
from dataclasses import asdict
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.config import Settings
from app.pipeline import proc
from app.pipeline.scan import execute_tools


class ScanRequest(BaseModel):
    run_id: int = Field(ge=1)
    timeout_s: float = Field(default=300, gt=0, le=3600)


def create_scanner_app(workspaces_dir: str | None = None) -> FastAPI:
    workspaces = Path(workspaces_dir or Settings().workspaces_dir)
    app = FastAPI(title="PR-Gate scanner", docs_url=None, redoc_url=None, openapi_url=None)

    @app.get("/health")
    def health():
        return {"status": "ok"}

    @app.post("/scan")
    def scan(req: ScanRequest):
        ws = workspaces / str(req.run_id) / "src"
        if not ws.is_dir():
            raise HTTPException(status_code=404, detail="workspace not found")
        with tempfile.TemporaryDirectory(prefix=f"scan-{req.run_id}-") as tmp:
            try:
                outcomes = execute_tools(ws, Path(tmp), req.timeout_s, key=str(req.run_id))
            except proc.ProcCancelled:
                return JSONResponse({"error": "cancelled"}, status_code=409)
        return {"outcomes": [asdict(o) for o in outcomes]}

    @app.post("/cancel/{run_id}")
    def cancel(run_id: int):
        return {"killed": proc.cancel(str(run_id))}

    return app
