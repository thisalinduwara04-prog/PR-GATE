import asyncio

import pytest

from app.contracts.models import RunRequest
from app.db import Database
from app.worker import recover_on_startup, worker_loop


@pytest.fixture
def db(tmp_path):
    d = Database(str(tmp_path / "t.db"))
    d.init()
    return d


def new_run(db):
    return db.create_run(RunRequest(repo="alice/demo", pr=1, head_ref="f", base_sha="b", head_sha="h"))


def test_recover_requeues_queued_and_fails_interrupted(db):
    a, b, c = new_run(db), new_run(db), new_run(db)
    db.update_run(b, status="running")
    db.update_run(c, status="completed")
    q = asyncio.Queue()
    recover_on_startup(db, q)
    assert [q.get_nowait() for _ in range(q.qsize())] == [a]
    run_b = db.get_run(b)
    assert run_b["status"] == "failed" and "restart" in run_b["error"]
    assert db.get_run(c)["status"] == "completed"


async def test_worker_processes_one_at_a_time(db):
    q = asyncio.Queue()
    active, max_active, done = 0, 0, []

    def process(run_id):
        nonlocal active, max_active
        active += 1
        max_active = max(max_active, active)
        import time
        time.sleep(0.05)
        done.append(run_id)
        active -= 1
        if run_id == 2:
            raise RuntimeError("worker must survive this")

    for i in (1, 2, 3):
        q.put_nowait(i)
    task = asyncio.create_task(worker_loop(q, process))
    await asyncio.wait_for(q.join(), timeout=5)
    task.cancel()
    assert done == [1, 2, 3] and max_active == 1


async def test_worker_pool_runs_in_parallel(db):
    from app.worker import start_workers
    q = asyncio.Queue()
    active, max_active = 0, 0
    import threading
    lock = threading.Lock()

    def process(run_id):
        nonlocal active, max_active
        with lock:
            active += 1
            max_active = max(max_active, active)
        import time
        time.sleep(0.3)
        with lock:
            active -= 1

    for i in range(4):
        q.put_nowait(i)
    tasks = start_workers(q, process, 2)
    await asyncio.wait_for(q.join(), timeout=10)
    for t in tasks:
        t.cancel()
    assert max_active == 2
