"""Agent service (B5/B7): runs the Claude Code CLI on a run's checkout.

Runs in its own container as its own unprivileged user. It holds only the Claude token, mounts the
workspaces volume read-only, and cannot reach the database, the GitHub token or the webhook secret.
The backend builds the prompt and validates the answer; this service only executes the sandboxed CLI.
"""

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.config import Settings
from app.pipeline import proc
from app.pipeline.verify import (
    DEFAULT_MAX_TURNS,
    VerifyCancelled,
    VerifyError,
    VerifyTimeout,
    claude_env,
    run_claude,
)

MAX_PROMPT_CHARS = 2_000_000


class VerifyRequest(BaseModel):
    run_id: int = Field(ge=1)
    prompt: str = Field(max_length=MAX_PROMPT_CHARS)
    timeout_s: float = Field(gt=0, le=3600)
    max_turns: int = Field(default=DEFAULT_MAX_TURNS, ge=1, le=100)


def create_agent_app(workspaces_dir: str | None = None, claude_bin: str | None = None,
                     oauth_token: str | None = None) -> FastAPI:
    settings = Settings()
    workspaces = Path(workspaces_dir or settings.workspaces_dir)
    claude_bin = claude_bin or settings.claude_bin
    oauth_token = settings.claude_code_oauth_token if oauth_token is None else oauth_token
    app = FastAPI(title="PR-Gate agent", docs_url=None, redoc_url=None, openapi_url=None)

    @app.get("/health")
    def health():
        return {"status": "ok"}

    @app.post("/verify")
    def verify(req: VerifyRequest):
        if not oauth_token:
            return JSONResponse({"error": "CLAUDE_CODE_OAUTH_TOKEN is not set in the agent container"},
                                status_code=503)
        ws = workspaces / str(req.run_id) / "src"
        if not ws.is_dir():
            raise HTTPException(status_code=404, detail="workspace not found")
        try:
            stdout = run_claude(claude_bin, req.max_turns, ws, claude_env(oauth_token), req.timeout_s,
                                req.prompt, key=str(req.run_id))
        except VerifyTimeout as e:
            return JSONResponse({"error": str(e)}, status_code=504)
        except VerifyCancelled as e:
            return JSONResponse({"error": str(e)}, status_code=409)
        except VerifyError as e:
            return JSONResponse({"error": str(e)}, status_code=502)
        return {"stdout": stdout}

    @app.post("/cancel/{run_id}")
    def cancel(run_id: int):
        return {"killed": proc.cancel(str(run_id))}

    return app
