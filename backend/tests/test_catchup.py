import json

import httpx
import pytest

from app.catchup import catch_up, list_open_prs
from app.contracts.models import RunRequest
from app.db import Database


def pr(number, head, base="b0", repo="Alice/Demo"):
    return {"number": number, "head": {"sha": head, "ref": f"branch-{number}"},
            "base": {"sha": base, "repo": {"full_name": repo}}}


class FakePulls:
    def __init__(self, pages):
        self.pages, self.requests = pages, []

    def handler(self, request: httpx.Request):
        self.requests.append(request)
        page = int(request.url.params.get("page", "1"))
        return httpx.Response(200, json=self.pages[page - 1] if page <= len(self.pages) else [])


@pytest.fixture
def db(tmp_path):
    d = Database(str(tmp_path / "t.db"))
    d.init()
    return d


def test_list_open_prs_paginates():
    fake = FakePulls([[pr(1, "a")], [pr(2, "b")]])
    client = httpx.Client(transport=httpx.MockTransport(fake.handler))
    prs = list_open_prs(client, "https://api.github.test", "tok", "alice/demo", per_page=1)
    assert [p["number"] for p in prs] == [1, 2]
    first = fake.requests[0]
    assert first.url.path == "/repos/alice/demo/pulls" and first.url.params["state"] == "open"
    assert first.headers["authorization"] == "Bearer tok"


def test_catch_up_queues_only_unreviewed_heads(db, settings):
    settings.watched_repos = "alice/demo"
    db.create_run(RunRequest(repo="Alice/Demo", pr=1, head_ref="x", base_sha="b0", head_sha="seen"))
    fake = FakePulls([[pr(1, "seen"), pr(2, "new")]])
    client = httpx.Client(transport=httpx.MockTransport(fake.handler))
    queued = []
    created = catch_up(db, settings, client, queued.append)
    assert queued == created and len(created) == 1
    run = db.get_run(created[0])
    assert (run["repo"], run["pr"], run["head_sha"], run["status"]) == ("Alice/Demo", 2, "new", "queued")
    assert db.get_pr_latest("Alice/Demo", 2) == "new" and db.get_pr_latest("Alice/Demo", 1) == "seen"


def test_catch_up_survives_api_errors(db, settings):
    settings.watched_repos = "alice/demo,bob/other"

    def handler(request):
        if "bob" in request.url.path:
            return httpx.Response(200, json=[pr(5, "h5", repo="bob/other")])
        return httpx.Response(401, json={"message": "Bad credentials"})

    queued = []
    created = catch_up(db, settings, httpx.Client(transport=httpx.MockTransport(handler)), queued.append)
    assert len(created) == 1 and db.get_run(created[0])["repo"] == "bob/other"
