"""POST /webhook/github: signature-checked entry point for GitHub pull_request events."""

import asyncio
import hashlib
import hmac
import json

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.contracts.models import RunRequest

router = APIRouter()

ACCEPTED_ACTIONS = {"opened", "synchronize", "reopened"}


class BodyTooLarge(Exception):
    pass


async def read_capped(request: Request, limit: int) -> bytes:
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > limit:
        raise BodyTooLarge
    chunks, size = [], 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > limit:
            raise BodyTooLarge
        chunks.append(chunk)
    return b"".join(chunks)


def signature_valid(secret: str, body: bytes, header: str | None) -> bool:
    if not secret or not header or not header.startswith("sha256="):
        return False
    expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header)


def ignored(reason: str) -> JSONResponse:
    return JSONResponse({"ignored": reason}, status_code=200)


@router.post("/webhook/github")
async def github_webhook(request: Request):
    settings = request.app.state.settings
    try:
        body = await read_capped(request, settings.max_body_bytes)
    except BodyTooLarge:
        return JSONResponse({"error": "payload too large"}, status_code=413)

    if not signature_valid(settings.github_webhook_secret, body, request.headers.get("x-hub-signature-256")):
        return JSONResponse({"error": "invalid signature"}, status_code=401)

    event = request.headers.get("x-github-event", "")
    if event == "ping":
        return JSONResponse({"msg": "pong"}, status_code=200)
    if event != "pull_request":
        return ignored(f"event {event!r}")

    delivery_id = request.headers.get("x-github-delivery")
    if delivery_id and not request.app.state.db.record_delivery(delivery_id):
        return ignored("duplicate delivery")

    try:
        payload = json.loads(body)
        action = payload["action"]
        repo = payload["repository"]["full_name"]
        pr = payload["pull_request"]
        req = RunRequest(
            repo=repo,
            pr=int(pr["number"]),
            head_ref=pr["head"]["ref"],
            base_sha=pr["base"]["sha"],
            head_sha=pr["head"]["sha"],
        )
    except (ValueError, KeyError, TypeError):
        return JSONResponse({"error": "malformed payload"}, status_code=400)

    if action not in ACCEPTED_ACTIONS:
        return ignored(f"action {action!r}")
    if repo.lower() not in settings.watched_repo_set:
        return ignored("repository not watched")

    db = request.app.state.db
    db.upsert_pr_latest(req.repo, req.pr, req.head_sha)
    # B6: stop older runs of this PR that are still working, instead of letting them finish for nothing.
    cancel_run = getattr(request.app.state, "cancel_run", None)
    if cancel_run:
        for old in db.running_runs_for_pr(req.repo, req.pr, req.head_sha):
            asyncio.get_running_loop().run_in_executor(None, cancel_run, old)
    run_id = db.create_run(req)
    request.app.state.queue.put_nowait(run_id)
    return JSONResponse({"run_id": run_id}, status_code=202)
