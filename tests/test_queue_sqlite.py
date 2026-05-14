import os
import tempfile

import pytest

from server import db as db_module
from server.queue import sqlite as sqlite_q
from server.queue.base import Job, JobState, compute_dedup_key


@pytest.fixture(autouse=True)
def _tmp_db(monkeypatch):
    """Each test gets a fresh DB in a tmpdir."""
    tmp = tempfile.mkdtemp()
    monkeypatch.setenv("TRANSLARR_DATA_DIR", tmp)
    db_module.close_for_tests()
    sqlite_q.reset_for_tests()
    yield
    db_module.close_for_tests()
    sqlite_q.reset_for_tests()


def _make_job(media: str = "/m/x.mkv", lang: str = "en") -> Job:
    return Job(
        id="",
        dedup_key=compute_dedup_key(media, None, lang),
        media_path=media,
        target_lang=lang,
        state=JobState.QUEUED,
    )


def test_enqueue_assigns_id_and_persists():
    q = sqlite_q.get_queue()
    job = q.enqueue(_make_job())
    assert job.id
    assert q.get(job.id) is not None


def test_find_by_dedup_returns_existing():
    q = sqlite_q.get_queue()
    a = q.enqueue(_make_job(media="/m/A.mkv"))
    b = q.find_by_dedup(a.dedup_key)
    assert b is not None
    assert b.id == a.id


def test_claim_next_transitions_to_running():
    q = sqlite_q.get_queue()
    q.enqueue(_make_job())
    claimed = q.claim_next()
    assert claimed is not None
    assert claimed.state == JobState.RUNNING
    assert claimed.attempts == 1
    # No more queued.
    assert q.claim_next() is None


def test_claim_race_only_one_wins():
    """Two simultaneous claims should yield one job and one None."""
    q = sqlite_q.get_queue()
    q.enqueue(_make_job())
    a = q.claim_next()
    b = q.claim_next()
    assert (a is not None) != (b is not None)


def test_checkpoint_accumulates_counters():
    q = sqlite_q.get_queue()
    job = q.enqueue(_make_job())
    q.claim_next()
    q.checkpoint(job.id, last_completed_line=10, cost_cents_delta=5, tokens_in_delta=100)
    q.checkpoint(job.id, last_completed_line=20, cost_cents_delta=7, tokens_in_delta=200)
    got = q.get(job.id)
    assert got.checkpoint_line == 20
    assert got.cost_cents == 12
    assert got.tokens_in == 300


def test_finish_marks_done():
    q = sqlite_q.get_queue()
    job = q.enqueue(_make_job())
    q.claim_next()
    q.finish(job.id, output_path="/out/x.srt", cost_cents=42, tokens_in=10, tokens_out=20, output_events=99)
    got = q.get(job.id)
    assert got.state == JobState.DONE
    assert got.output_path == "/out/x.srt"
    assert got.cost_cents == 42
    assert got.finished_at is not None


def test_mark_failed_retries_when_attempts_below_max():
    q = sqlite_q.get_queue()
    job = q.enqueue(_make_job())
    q.claim_next()  # attempts -> 1
    q.mark_failed(job.id, "transient error")
    got = q.get(job.id)
    assert got.state == JobState.RETRYING


def test_mark_failed_terminal_at_max_attempts():
    q = sqlite_q.get_queue()
    job = q.enqueue(_make_job())
    # Burn all attempts.
    for _ in range(3):
        q.claim_next()  # 1, 2, 3
        # Force re-queue by resetting state for next claim.
        q.update_state(job.id, JobState.QUEUED) if _ < 2 else None
    q.mark_failed(job.id, "final error")
    got = q.get(job.id)
    assert got.state == JobState.FAILED
    assert got.finished_at is not None


def test_reset_orphaned_running_jobs():
    q = sqlite_q.get_queue()
    job = q.enqueue(_make_job())
    q.claim_next()
    assert q.get(job.id).state == JobState.RUNNING
    # Simulate restart: reset_orphaned on fresh queue.
    sqlite_q.reset_for_tests()
    q2 = sqlite_q.get_queue()  # singleton calls reset_orphaned on init
    assert q2.get(job.id).state == JobState.QUEUED


def test_dedup_key_stable_across_calls():
    k1 = compute_dedup_key("/m/x.mkv", 2, "en")
    k2 = compute_dedup_key("/m/x.mkv", 2, "en")
    assert k1 == k2


def test_dedup_key_differs_on_different_target():
    k1 = compute_dedup_key("/m/x.mkv", None, "en")
    k2 = compute_dedup_key("/m/x.mkv", None, "es")
    assert k1 != k2
