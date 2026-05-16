"""Async utilities shared across modules.

Currently just the "fire and forget" task pattern, which two modules
implemented inline before this was extracted. Both need a strong
reference to keep the task alive until completion (asyncio holds only a
weak ref, so the task can be GC'd mid-flight without one), and a
done-callback that drops the reference once the task finishes.

Usage:

    from server.async_utils import fire_and_forget

    fire_and_forget(library_refresh.refresh_libraries_after(path))
"""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from typing import Any

# Module-level anchor: every spawned task is added here so the event loop
# keeps a strong reference. The done-callback below removes the entry
# after the task completes (success, failure, or cancellation) to prevent
# unbounded growth.
_PENDING: set[asyncio.Task[Any]] = set()


def fire_and_forget(coro: Coroutine[Any, Any, Any]) -> asyncio.Task[Any]:
    """Schedule ``coro`` on the running loop without waiting for it.

    Returns the task handle for callers who want to attach further
    callbacks, but the common case is to ignore the return.
    """
    task = asyncio.create_task(coro)
    _PENDING.add(task)
    task.add_done_callback(_PENDING.discard)
    return task


def pending_count() -> int:
    """Diagnostic accessor — number of in-flight fire-and-forget tasks."""
    return len(_PENDING)
