"""Webhook helper: enqueue a translation job into the SQLite queue.

The enqueue path applies per-series language overrides BEFORE building
the dedup key, so a Sonarr/Radarr/Emby webhook for a media file under a
configured series picks up that series' target_lang, source_lang, and
glossary just like a direct POST to /translate would. Without this step
webhook-initiated jobs always ran against the global TARGET_LANG and the
per-series feature only worked for manual translations.
"""

from __future__ import annotations

from pathlib import Path

import structlog

from server.config import settings
from server.queue.base import Job, JobState, compute_dedup_key
from server.queue.sqlite import get_queue
from server.series_config import resolve_overrides

log = structlog.get_logger()


async def enqueue(path: Path, target_lang: str | None = None) -> str | None:
    """Enqueue a translation job for the given media path.

    ``target_lang`` is treated as an explicit caller override — if None,
    the per-series config wins; otherwise the caller value is used. The
    resolved (target_lang, source_lang, glossary_id) values are written
    into the Job row so the worker has the fully-bound parameters.

    Returns the job id, or None if a dedup hit silently skipped.
    """
    media_path = str(path)
    lang, source_lang, glossary_id, _series = resolve_overrides(
        media_path,
        explicit_target_lang=target_lang,
        explicit_source_lang=None,
        explicit_glossary_id=None,
        default_target_lang=settings.target_lang,
    )
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
        source_lang=source_lang,
        glossary_id=glossary_id,
        state=JobState.QUEUED,
    )
    persisted = q.enqueue(job)
    return persisted.id
