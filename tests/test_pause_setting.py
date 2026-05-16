"""Global ``translations_paused`` pause toggle.

When ON, workers stop claiming queued jobs. New requests still enqueue
(visible in the dashboard) but don't start running until the toggle
flips OFF. Running jobs are not interrupted.
"""

from __future__ import annotations

import asyncio
import tempfile
from unittest.mock import patch

import pytest

from server import db as db_module
from server.config import settings
from server.queue import sqlite as sqlite_q
from server.queue.base import Job, JobState
from server.queue.sqlite import get_queue
from server.queue.worker import worker_loop


@pytest.fixture(autouse=True)
def _tmp_db(monkeypatch):
    tmp = tempfile.mkdtemp()
    monkeypatch.setenv("TRANSLARR_DATA_DIR", tmp)
    db_module.close_for_tests()
    sqlite_q.reset_for_tests()
    yield
    db_module.close_for_tests()
    sqlite_q.reset_for_tests()


def _enqueue() -> Job:
    q = get_queue()
    return q.enqueue(
        Job(
            id="",
            dedup_key="k:test",
            media_path="/m/test.mkv",
            target_lang="en",
            state=JobState.QUEUED,
        )
    )


@pytest.mark.asyncio
async def test_worker_does_not_claim_when_paused(monkeypatch):
    """With translations_paused=True, the worker sees the queued job but
    leaves it in QUEUED state instead of claiming."""
    monkeypatch.setattr(settings, "translations_paused", True)
    monkeypatch.setattr(settings, "max_concurrent", 1)
    job = _enqueue()

    # Patch _run_job so any accidental claim would surface as a test
    # failure rather than try to translate a fake file.
    with patch("server.queue.worker._run_job") as mock_run:
        stop = asyncio.Event()
        task = asyncio.create_task(worker_loop(stop, worker_id=0))
        # Give the worker a couple of poll cycles to maybe claim.
        await asyncio.sleep(0.05)
        stop.set()
        await task
        assert mock_run.call_count == 0

    after = get_queue().get(job.id)
    assert after is not None
    assert after.state == JobState.QUEUED, (
        f"paused worker claimed the job — state is {after.state}"
    )
    assert after.attempts == 0


@pytest.mark.asyncio
async def test_worker_resumes_when_unpaused(monkeypatch):
    """Toggling translations_paused back to OFF lets the worker drain."""
    monkeypatch.setattr(settings, "translations_paused", True)
    monkeypatch.setattr(settings, "max_concurrent", 1)
    job = _enqueue()

    with patch("server.queue.worker._run_job") as mock_run:
        # Make _run_job a tiny no-op coroutine.
        async def _noop(j):
            return None
        mock_run.side_effect = _noop

        stop = asyncio.Event()
        task = asyncio.create_task(worker_loop(stop, worker_id=0))
        await asyncio.sleep(0.05)
        # Now resume.
        monkeypatch.setattr(settings, "translations_paused", False)
        # Worker polls every 1s — give it time to wake and claim.
        await asyncio.sleep(1.2)
        stop.set()
        await task
        assert mock_run.call_count == 1
        # The job mock_run got handed should be our job.
        assert mock_run.call_args[0][0].id == job.id


@pytest.mark.asyncio
async def test_pause_does_not_drop_queued_jobs(monkeypatch):
    """Enqueueing while paused is fine — the row sits in QUEUED state."""
    monkeypatch.setattr(settings, "translations_paused", True)
    job_a = _enqueue()
    # Second enqueue with different dedup key.
    q = get_queue()
    job_b = q.enqueue(Job(
        id="", dedup_key="k:b", media_path="/m/b.mkv",
        target_lang="en", state=JobState.QUEUED,
    ))
    assert get_queue().get(job_a.id).state == JobState.QUEUED
    assert get_queue().get(job_b.id).state == JobState.QUEUED
