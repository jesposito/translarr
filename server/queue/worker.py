"""Background worker loop. Polls the queue, runs jobs through the translate pipeline.

v0.1.5: single-process, multiple asyncio worker tasks. v0.6+ may move to multi-process.
"""

from __future__ import annotations

import asyncio
import contextlib
from pathlib import Path

import structlog

from server import notifications
from server.config import settings
from server.models import TranslateRequest
from server.queue.base import Job
from server.queue.sqlite import get_queue

log = structlog.get_logger()

_POLL_INTERVAL_SECONDS = 1.0


def _job_to_request(job: Job) -> TranslateRequest:
    return TranslateRequest(
        media_path=Path(job.media_path),
        source_track_index=job.source_track_index,
        source_lang=job.source_lang,
        target_lang=job.target_lang,
        glossary_id=job.glossary_id,
        force=job.force_flag,
    )


async def _run_job(job: Job) -> None:
    from server.subs.pipeline import AlreadyTranslated, NoSourceSubtitles, translate_media

    q = get_queue()
    try:
        result = await translate_media(_job_to_request(job))
        q.finish(
            job.id,
            output_path=str(result.output_path),
            cost_cents=result.cost_cents,
            tokens_in=result.tokens_in,
            tokens_out=result.tokens_out,
            output_events=result.output_events,
        )
        log.info("job_done", job_id=job.id, output=str(result.output_path))
        notifications.notify_success(
            media_path=job.media_path,
            target_lang=job.target_lang,
            cost_cents=result.cost_cents,
            duration_s=result.duration_seconds,
        )
    except AlreadyTranslated as e:
        # Idempotent skip — mark done with existing output, no error.
        q.finish(
            job.id,
            output_path=str(e.path),
            cost_cents=0,
            tokens_in=0,
            tokens_out=0,
            output_events=0,
        )
        log.info("job_skipped_already_translated", job_id=job.id, output=str(e.path))
        notifications.notify_skip(
            media_path=job.media_path,
            target_lang=job.target_lang,
            reason="already translated",
        )
    except NoSourceSubtitles as e:
        # Terminal skip — retrying re-runs ffprobe with the same inputs and
        # cannot succeed. Mark done with $0 cost so playback.start triggers
        # against English-only items don't churn the retry queue.
        q.finish(
            job.id,
            output_path=None,
            cost_cents=0,
            tokens_in=0,
            tokens_out=0,
            output_events=0,
        )
        log.info("job_skipped_no_source_subtitles", job_id=job.id, reason=str(e))
        notifications.notify_skip(
            media_path=job.media_path,
            target_lang=job.target_lang,
            reason="no source subtitles",
        )
    except Exception as e:
        log.exception("job_failed", job_id=job.id)
        q.mark_failed(job.id, f"{type(e).__name__}: {e}")
        # Only fire ntfy on the FINAL failure (attempts exhausted),
        # not on each retry — mark_failed itself decides retry vs
        # terminal based on attempts < max_attempts, so re-check the
        # row state here.
        row = q.get(job.id)
        if row is not None and row.state.value == "failed":
            notifications.notify_failure(
                media_path=job.media_path,
                target_lang=job.target_lang,
                error=f"{type(e).__name__}: {e}",
            )


async def worker_loop(stop_event: asyncio.Event, worker_id: int) -> None:
    """One asyncio worker. Pulls from the queue and runs jobs until stop_event."""
    q = get_queue()
    log.info("worker_started", worker_id=worker_id)
    while not stop_event.is_set():
        try:
            job = q.claim_next()
        except Exception:
            log.exception("worker_claim_error", worker_id=worker_id)
            await asyncio.sleep(_POLL_INTERVAL_SECONDS * 2)
            continue
        if job is None:
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(stop_event.wait(), timeout=_POLL_INTERVAL_SECONDS)
            continue
        log.info("worker_claimed", worker_id=worker_id, job_id=job.id)
        await _run_job(job)
    log.info("worker_stopped", worker_id=worker_id)


async def start_workers(stop_event: asyncio.Event) -> list[asyncio.Task]:
    """Spawn N worker tasks; return handles so caller can await on shutdown."""
    n = settings.max_concurrent
    return [asyncio.create_task(worker_loop(stop_event, i)) for i in range(n)]
