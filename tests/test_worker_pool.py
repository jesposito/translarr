"""Worker pool resizing: growing after a shrink must reuse free slot ids."""

import asyncio

from server.config import settings
from server.queue import worker


async def test_regrow_after_shrink_reuses_low_ids(monkeypatch):
    monkeypatch.setattr(settings, "max_concurrent", 2)

    async def idle(stop_event, worker_id):
        await stop_event.wait()

    monkeypatch.setattr(worker, "worker_loop", idle)
    stop = asyncio.Event()
    tasks = await worker.start_workers(stop)
    try:
        # Worker 1 exits after a shrink to 1...
        tasks[1].cancel()
        await asyncio.sleep(0)
        # ...then the pool grows back to 2.
        monkeypatch.setattr(settings, "max_concurrent", 2)
        worker.adjust_pool()
        ids = sorted(worker._pool.task_ids.values())
        assert ids == [0, 1]
    finally:
        stop.set()
        await asyncio.gather(*worker._pool.tasks, return_exceptions=True)
        worker.shutdown_pool()
