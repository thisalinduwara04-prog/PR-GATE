"""B1: on startup, queue open PRs whose current head was never reviewed (webhooks missed while offline)."""

import logging
from typing import Callable

import httpx

from app.config import Settings
from app.contracts.models import RunRequest
from app.db import Database
from app.pipeline.publish import GITHUB_HEADERS

log = logging.getLogger("pr-gate.catchup")
MAX_PAGES = 20


def list_open_prs(client: httpx.Client, api_url: str, token: str, repo: str, per_page: int = 100) -> list[dict]:
    headers = {**GITHUB_HEADERS, "Authorization": f"Bearer {token}"}
    prs: list[dict] = []
    for page in range(1, MAX_PAGES + 1):
        resp = client.get(f"{api_url.rstrip('/')}/repos/{repo}/pulls", headers=headers, timeout=30,
                          params={"state": "open", "per_page": per_page, "page": page})
        resp.raise_for_status()
        batch = resp.json()
        prs.extend(batch)
        if len(batch) < per_page:
            break
    return prs


def catch_up(db: Database, settings: Settings, client: httpx.Client, enqueue: Callable[[int], None]) -> list[int]:
    created: list[int] = []
    for repo in sorted(settings.watched_repo_set):
        try:
            prs = list_open_prs(client, settings.github_api_url, settings.github_token, repo)
        except (httpx.HTTPError, ValueError) as e:
            log.warning("catch-up: could not list open PRs for %s: %s", repo, e)
            continue
        for pr in prs:
            try:
                req = RunRequest(
                    repo=pr["base"]["repo"]["full_name"],  # canonical casing, as webhooks report it
                    pr=int(pr["number"]),
                    head_ref=pr["head"]["ref"],
                    base_sha=pr["base"]["sha"],
                    head_sha=pr["head"]["sha"],
                )
            except (KeyError, TypeError, ValueError):
                log.warning("catch-up: skipping malformed PR entry in %s", repo)
                continue
            db.upsert_pr_latest(req.repo, req.pr, req.head_sha)
            if db.run_exists(req.repo, req.pr, req.head_sha):
                continue
            run_id = db.create_run(req)
            enqueue(run_id)
            created.append(run_id)
            log.info("catch-up: queued run %s for %s#%s at %s", run_id, req.repo, req.pr, req.head_sha[:7])
    return created
