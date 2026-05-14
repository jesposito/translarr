"""Regression test: ``retrying`` jobs must be claimable by the worker.

Pre-2026-05-15 bug: ``claim_next`` filtered on ``state='queued'`` only,
so any job ``mark_failed`` had transitioned to RETRYING after a
recoverable error was stranded forever (visible in the UI as a
permanently-retrying job that nothing ever attempted again).

The fix expands the eligibility set to ``{queued, retrying}`` while
keeping the atomic-claim guard so two workers can't double-claim.
"""

from __future__ import annotations

import tempfile

import pytest

from server import db as db_module
from server.queue import sqlite as sqlite_q
from server.queue.base import Job, JobState
from server.queue.sqlite import get_queue


@pytest.fixture(autouse=True)
def _tmp_db(monkeypatch):
    tmp = tempfile.mkdtemp()
    monkeypatch.setenv("TRANSLARR_DATA_DIR", tmp)
    db_module.close_for_tests()
    sqlite_q.reset_for_tests()
    yield
    db_module.close_for_tests()
    sqlite_q.reset_for_tests()


def _enqueue(media: str = "/m/x.mkv") -> Job:
    return get_queue().enqueue(
        Job(
            id="",
            dedup_key=f"k:{media}",
            media_path=media,
            target_lang="en",
            state=JobState.QUEUED,
        )
    )


def test_claim_picks_retrying_state():
    """Retrying job is claimable on subsequent worker pickup."""
    q = get_queue()
    job = _enqueue()
    # Simulate worker run + recoverable failure.
    claimed_1 = q.claim_next()
    assert claimed_1 is not None and claimed_1.id == job.id
    q.mark_failed(job.id, "RuntimeError: transient")
    after_fail = q.get(job.id)
    assert after_fail is not None
    assert after_fail.state == JobState.RETRYING

    # Worker should be able to claim it again — that was the bug.
    claimed_2 = q.claim_next()
    assert claimed_2 is not None
    assert claimed_2.id == job.id
    assert claimed_2.state == JobState.RUNNING


def test_solo_retrying_job_is_claimable():
    """If the only eligible job is retrying, claim_next must still pick it."""
    q = get_queue()
    job = _enqueue("/m/solo.mkv")

    c = q.claim_next()
    assert c is not None and c.id == job.id
    q.mark_failed(job.id, "transient")
    assert q.get(job.id).state == JobState.RETRYING

    # No other QUEUED work — pre-fix this returned None. Post-fix picks
    # the retrying row.
    nxt = q.claim_next()
    assert nxt is not None
    assert nxt.id == job.id
    assert nxt.state == JobState.RUNNING


def test_terminal_failed_job_not_reclaimed():
    """Once attempts hit max, mark_failed flips to FAILED — not eligible."""
    q = get_queue()
    job = _enqueue()
    for _ in range(3):
        c = q.claim_next()
        if c is None:
            break
        q.mark_failed(job.id, "RuntimeError: still broken")
    final = q.get(job.id)
    assert final is not None
    assert final.state == JobState.FAILED
    # claim_next returns None — no eligible work left.
    assert q.claim_next() is None
