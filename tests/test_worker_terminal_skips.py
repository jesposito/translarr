"""Worker terminal-skip behavior (TR-2yt).

The translate pipeline raises two non-retryable signals:

- :class:`AlreadyTranslated` — output already exists; idempotent done.
- :class:`NoSourceSubtitles` — nothing to translate (ffprobe found no
  embedded tracks, no usable sidecar). Re-running with the same inputs
  cannot succeed, so the worker must NOT eat retry attempts.

This matters most for the on-demand playback.start path, where Emby will
fire on every Play — including English-only items that should silently
no-op rather than retry-loop and pollute the failed-jobs UI.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from server import db as db_module
from server.queue import sqlite as sqlite_q
from server.queue.base import Job, JobState
from server.queue.sqlite import get_queue
from server.subs.pipeline import AlreadyTranslated, NoSourceSubtitles


@pytest.fixture(autouse=True)
def _tmp_db(monkeypatch):
    tmp = tempfile.mkdtemp()
    monkeypatch.setenv("TRANSLARR_DATA_DIR", tmp)
    db_module.close_for_tests()
    sqlite_q.reset_for_tests()
    yield
    db_module.close_for_tests()
    sqlite_q.reset_for_tests()


def _enqueue_job(media_path: str = "/media/show/ep.mkv") -> Job:
    q = get_queue()
    job = Job(
        id="",
        dedup_key=f"k:{media_path}",
        media_path=media_path,
        target_lang="en",
        state=JobState.QUEUED,
    )
    return q.enqueue(job)


@pytest.mark.asyncio
async def test_no_source_subtitles_marks_done_zero_cost():
    """Worker must finalize as DONE with $0, not retry, not failed."""
    from server.queue.worker import _run_job

    job = _enqueue_job()
    # claim_next transitions QUEUED -> RUNNING + bumps attempts. Mirror
    # what the worker loop does so _run_job sees a realistic Job row.
    claimed = get_queue().claim_next()
    assert claimed is not None and claimed.id == job.id

    with patch(
        "server.subs.pipeline.translate_media",
        side_effect=NoSourceSubtitles("nothing to translate"),
    ):
        await _run_job(claimed)

    after = get_queue().get(job.id)
    assert after is not None
    assert after.state == JobState.DONE
    assert after.cost_cents == 0
    assert after.output_path in (None, "")


@pytest.mark.asyncio
async def test_already_translated_marks_done_with_path():
    """AlreadyTranslated stays a $0 done — regression guard."""
    from server.queue.worker import _run_job

    existing = "/media/show/ep.en.translarr.srt"
    job = _enqueue_job()
    claimed = get_queue().claim_next()
    assert claimed is not None

    with patch(
        "server.subs.pipeline.translate_media",
        side_effect=AlreadyTranslated(Path(existing)),
    ):
        await _run_job(claimed)

    after = get_queue().get(job.id)
    assert after is not None
    assert after.state == JobState.DONE
    assert after.cost_cents == 0
    assert after.output_path == existing


@pytest.mark.asyncio
async def test_file_not_found_marks_done_zero_cost():
    """Retrying a missing file just re-fails the same way. Treat as $0 skip."""
    from server.queue.worker import _run_job

    job = _enqueue_job()
    claimed = get_queue().claim_next()
    assert claimed is not None

    with patch(
        "server.subs.pipeline.translate_media",
        side_effect=FileNotFoundError("media not found: /m/missing.mkv"),
    ):
        await _run_job(claimed)

    after = get_queue().get(job.id)
    assert after is not None
    assert after.state == JobState.DONE
    assert after.cost_cents == 0


@pytest.mark.asyncio
async def test_unknown_exception_still_retries_then_fails():
    """Anything OTHER than the two terminal-skip signals stays retry-eligible."""
    from server.queue.worker import _run_job

    job = _enqueue_job()
    # Exhaust retries.
    for _ in range(3):
        claimed = get_queue().claim_next()
        if claimed is None:
            # After retries are exhausted, claim_next no longer returns it
            # if state already FAILED — break.
            break
        with patch(
            "server.subs.pipeline.translate_media",
            side_effect=RuntimeError("transient"),
        ):
            await _run_job(claimed)
        # Re-queue if still in retrying state by simulating the retry pickup.
        row = get_queue().get(job.id)
        if row and row.state == JobState.RETRYING:
            # Promote retrying -> queued so claim_next picks it up again.
            get_queue().update_state(job.id, JobState.QUEUED)

    final = get_queue().get(job.id)
    assert final is not None
    assert final.state == JobState.FAILED
    assert final.error and "RuntimeError" in final.error
