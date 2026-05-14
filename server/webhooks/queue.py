"""Webhook helper: enqueue a translation job into the SQLite queue.

v0.1.5 replaces the in-memory dedup set with SQLite-backed dedup via Queue.find_by_dedup.
"""

from __future__ import annotations

from pathlib import Path

import structlog

from server.config import settings
from server.queue.base import Job, JobState, compute_dedup_key
from server.queue.sqlite import get_queue

log = structlog.get_logger()


async def enqueue(path: Path, target_lang: str | None = None) -> str | None:
    """Enqueue a translation job for the given media path.

    Returns the job id, or None if a dedup hit silently skipped.
    """
    lang = target_lang or settings.target_lang
    media_path = str(path)
    dedup = compute_dedup_key(media_path, None, lang)

    q = get_queue()
    existing = q.find_by_dedup(dedup)
    if existing and existing.state in {JobState.QUEUED, JobState.RUNNING, JobState.RETRYING, JobState.DONE}:
        log.info("webhook_dedup_skip", media_path=media_path, existing_job=existing.id, state=existing.state.value)
        return None

    job = Job(
        id="",
        dedup_key=dedup,
        media_path=media_path,
        target_lang=lang,
        state=JobState.QUEUED,
    )
    persisted = q.enqueue(job)
    return persisted.id
