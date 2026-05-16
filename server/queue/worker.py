"""Background worker loop. Polls the queue, runs jobs through the translate pipeline.

v0.1.5: single-process, multiple asyncio worker tasks. v0.6+ may move to multi-process.

Worker pool state lives on a single ``_Pool`` instance so the start/adjust/shutdown
lifecycle is explicit and testable. ``start_workers()`` resets the pool every call,
``adjust_pool()`` reads from the same instance, and ``shutdown_pool()`` clears it
again so test fixtures don't see stale tasks across runs.
"""

from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass, field
from pathlib import Path

import structlog

from server import notifications
from server.config import settings
from server.models import TranslateRequest
from server.queue.base import Job
from server.queue.sqlite import get_queue

log = structlog.get_logger()

_POLL_INTERVAL_SECONDS = 1.0


def _short_error(error: str) -> str:
    """Strip ffmpeg noise, return a human-readable error message.

    Preserves the full error in structured logs but surfaces the short
    version in the job row so the UI shows something actionable.
    """
    # FileNotFoundError — already clean.
    if error.startswith("media not found:"):
        return error

    # ffmpeg extract failure — strip the full ffmpeg version/config dump.
    if "ffmpeg extract failed:" in error:
        # The actual reason is usually on the last line or two.
        lines = error.split("\n")
        for line in reversed(lines):
            line = line.strip()
            if line and not line.startswith(("ffmpeg version", "built with", "configuration:", "lib", "Input #", "  ", "Stream #", "Duration:", "Chapter", "  Metadata", "Stream mapping")):
                return f"ffmpeg: {line}"[:200]
        return "ffmpeg extraction failed"

    # ffprobe failure.
    if "ffprobe failed:" in error or "ffprobe timed out" in error:
        return error[:150]

    # Cost cap.
    if "cost_cap" in error.lower():
        return error  # already human-readable

    # LLM overload / retry exhaustion.
    if "RetryError" in error and "Overloaded" in error:
        return "LLM provider overloaded — all retry attempts failed. Will retry on next attempt."

    # Generic — first line, truncated.
    first_line = error.split("\n")[0]
    return first_line[:200]


@dataclass
class _Pool:
    """Holds the live worker-pool state.

    A single module-level instance acts as the singleton; tests can reset
    by calling ``shutdown_pool()`` between cases.
    """

    stop_event: asyncio.Event | None = None
    tasks: list[asyncio.Task] = field(default_factory=list)
    next_worker_id: int = 0

    def reset(self) -> None:
        self.stop_event = None
        self.tasks = []
        self.next_worker_id = 0


_pool = _Pool()


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
            source_lang=result.source_lang or None,
            source_track_index=result.source_track_index,
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
    except FileNotFoundError as e:
        # Terminal skip — retrying with the same media_path will hit the
        # same FileNotFoundError. Treat as $0 done so a typo'd webhook
        # payload or stale Emby item doesn't churn the retry queue. The
        # error string is preserved on the job row for the UI to surface.
        q.finish(
            job.id,
            output_path=None,
            cost_cents=0,
            tokens_in=0,
            tokens_out=0,
            output_events=0,
        )
        log.info("job_skipped_file_not_found", job_id=job.id, error=str(e))
        notifications.notify_skip(
            media_path=job.media_path,
            target_lang=job.target_lang,
            reason="media path not found",
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
        full_error = f"{type(e).__name__}: {e}"
        q.mark_failed(job.id, _short_error(full_error))
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
        # Self-termination: if pool shrunk, workers with high IDs exit.
        if _should_exit(worker_id):
            log.info("worker_self_terminating", worker_id=worker_id,
                     max_concurrent=settings.max_concurrent)
            break
        # Global pause: when settings.translations_paused is ON, sit idle
        # and re-check on the next poll. Jobs continue to queue, they
        # just don't get claimed. Setting is live-mutable from the UI so
        # flipping it back to OFF resumes processing without restart.
        if settings.translations_paused:
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(stop_event.wait(), timeout=_POLL_INTERVAL_SECONDS)
            continue
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


def _should_exit(worker_id: int) -> bool:
    """True when the pool target has shrunk below this worker's slot."""
    # Workers 0..(target-1) stay alive; higher-numbered ones exit.
    return worker_id >= settings.max_concurrent


def adjust_pool() -> None:
    """Grow or shrink the worker pool to match ``settings.max_concurrent``.

    Called from the settings store after a PATCH to ``max_concurrent``.
    Growth is immediate (new tasks spawn). Shrinking is graceful — excess
    workers notice on their next loop iteration and self-terminate.
    """
    if _pool.stop_event is None:
        return  # Pool not started yet (shouldn't happen in prod).

    # Prune completed tasks first so the live count is accurate.
    _pool.tasks = [t for t in _pool.tasks if not t.done()]
    target = settings.max_concurrent
    current = len(_pool.tasks)

    if target > current:
        for _ in range(target - current):
            tid = _pool.next_worker_id
            _pool.next_worker_id += 1
            task = asyncio.ensure_future(worker_loop(_pool.stop_event, tid))
            _pool.tasks.append(task)
        log.info("worker_pool_grew", spawned=target - current, total=target)
    elif target < current:
        # Excess workers self-terminate on their next loop iteration.
        log.info("worker_pool_shrinking", current=current, target=target)
    # else: already at target, nothing to do.


async def start_workers(stop_event: asyncio.Event) -> list[asyncio.Task]:
    """Spawn N worker tasks; return handles so caller can await on shutdown.

    Resets pool state on every call so re-initialisation (e.g. in tests
    or after a hot reload) doesn't accumulate stale task references.
    """
    _pool.reset()
    _pool.stop_event = stop_event
    n = settings.max_concurrent
    tasks = [asyncio.create_task(worker_loop(stop_event, i)) for i in range(n)]
    _pool.next_worker_id = n
    _pool.tasks = list(tasks)
    return tasks


def shutdown_pool() -> None:
    """Clear all pool state. Tests call this in teardown."""
    _pool.reset()
