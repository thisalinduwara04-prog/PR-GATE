import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app import api, evaluation, webhook
from app.catchup import catch_up
from app.config import Settings
from app.db import Database
from app.pipeline.run import process_run
from app.pipeline.steps import build_steps, make_canceller
from app.worker import recover_on_startup, start_workers

API_ALLOWED_HOSTS = {"localhost", "localhost:8000", "127.0.0.1", "127.0.0.1:8000"}
DASHBOARD_ORIGIN = "http://localhost:5173"

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def create_app(settings: Settings | None = None, start_worker: bool = True,
               http_transport: httpx.BaseTransport | None = None) -> FastAPI:
    settings = settings or Settings()
    db = Database(settings.db_path)
    db.init()
    Path(settings.workspaces_dir).mkdir(parents=True, exist_ok=True)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        tasks = []
        http = httpx.Client(transport=http_transport)
        app.state.cancel_run = make_canceller(settings, http)
        if start_worker:
            steps = build_steps(settings, db, http)
            recover_on_startup(db, app.state.queue)
            if settings.catchup_on_startup:
                loop = asyncio.get_running_loop()
                enqueue = lambda run_id: loop.call_soon_threadsafe(app.state.queue.put_nowait, run_id)
                app.state.catchup = asyncio.create_task(asyncio.to_thread(catch_up, db, settings, http, enqueue))
            tasks = start_workers(app.state.queue,
                                  lambda run_id: process_run(run_id, db, steps, Path(settings.workspaces_dir)),
                                  settings.max_concurrent_runs)
        try:
            yield
        finally:
            for t in tasks:
                t.cancel()
            http.close()

    app = FastAPI(title="PR-Gate", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.settings = settings
    app.state.db = db
    app.state.queue = asyncio.Queue()

    @app.middleware("http")
    async def api_localhost_only(request: Request, call_next):
        if request.url.path.startswith("/api"):
            if request.headers.get("host", "").lower() not in API_ALLOWED_HOSTS:
                return JSONResponse({"error": "forbidden"}, status_code=403)
        return await call_next(request)

    app.add_middleware(CORSMiddleware, allow_origins=[DASHBOARD_ORIGIN], allow_methods=["GET", "POST"],
                       allow_headers=["Content-Type"])

    app.include_router(webhook.router)
    app.include_router(api.router)
    app.include_router(evaluation.router)

    @app.get("/api/health")
    def health():
        return {"status": "ok"}

    return app
