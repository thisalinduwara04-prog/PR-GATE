"""Single background worker: one run at a time, fed by an asyncio.Queue."""

import asyncio
import logging
from typing import Callable

from app.db import Database, now_iso

log = logging.getLogger("pr-gate.worker")


def recover_on_startup(db: Database, queue: asyncio.Queue) -> None:
    """Fail runs cut off by a restart and re-queue runs that never started."""
    for run_id in db.run_ids_with_status("running"):
        db.update_run(run_id, status="failed", error="interrupted by restart", finished_at=now_iso())
    for run_id in db.run_ids_with_status("queued"):
        queue.put_nowait(run_id)


async def worker_loop(queue: asyncio.Queue, process: Callable[[int], None]) -> None:
    while True:
        run_id = await queue.get()
        try:
            await asyncio.to_thread(process, run_id)
        except Exception:
            log.exception("worker: run %s raised", run_id)
        finally:
            queue.task_done()


def start_workers(queue: asyncio.Queue, process: Callable[[int], None], count: int) -> list[asyncio.Task]:
    """B6: `count` workers share one queue, so up to `count` runs execute at the same time."""
    return [asyncio.create_task(worker_loop(queue, process)) for _ in range(max(1, count))]
