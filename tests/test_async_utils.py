"""Shared fire-and-forget task helper.

The pipeline kicks off library refreshes that must outlive the request
handler, and notifications spawns NTFY posts after the worker has moved
on. Both used to inline the "strong-ref + done-callback" pattern.
Centralised in async_utils.fire_and_forget.
"""

from __future__ import annotations

import asyncio

import pytest

from server.async_utils import fire_and_forget, pending_count


async def _no_op():
    """Trivial coroutine for tests."""
    return None


async def _raises():
    raise RuntimeError("expected — task error path")


async def _slow():
    await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_returns_running_task():
    task = fire_and_forget(_no_op())
    assert isinstance(task, asyncio.Task)
    await task


@pytest.mark.asyncio
async def test_task_completes_without_being_awaited():
    """The whole point: spawn it and forget. Loop must still run it."""
    completed = False

    async def _set_flag():
        nonlocal completed
        completed = True

    fire_and_forget(_set_flag())
    # Yield so the loop can run pending tasks.
    await asyncio.sleep(0)
    assert completed is True


@pytest.mark.asyncio
async def test_pending_count_drops_after_completion():
    """Once a task finishes the done-callback must remove its strong ref
    so the anchor set doesn't grow without bound."""
    before = pending_count()
    fire_and_forget(_no_op())
    # Right after spawn the task is still pending.
    assert pending_count() == before + 1
    # Yield so the task can complete and the done-callback can fire.
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert pending_count() == before


@pytest.mark.asyncio
async def test_exception_in_task_does_not_break_anchor():
    """An exception inside a fire-and-forget task should not prevent the
    done-callback from removing its anchor ref."""
    before = pending_count()
    task = fire_and_forget(_raises())
    # Drain the task — pytest's asyncio loop won't surface the exception
    # from the orphan task unless we explicitly await it.
    with pytest.raises(RuntimeError, match="expected"):
        await task
    # And the anchor must still drop the ref.
    await asyncio.sleep(0)
    assert pending_count() == before


@pytest.mark.asyncio
async def test_multiple_concurrent_tasks_tracked():
    before = pending_count()
    tasks = [fire_and_forget(_slow()) for _ in range(5)]
    assert pending_count() == before + 5
    await asyncio.gather(*tasks)
    await asyncio.sleep(0)
    assert pending_count() == before
