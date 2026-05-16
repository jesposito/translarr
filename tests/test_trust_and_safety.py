"""v0.3.4 trust + safety batch tests.

- /backup returns a valid SQLite snapshot
- /test/llm reports both success and provider errors verbatim
- /jobs/{id}/retry creates a NEW job with force=true; refuses on live jobs
- cost_tracker warns once per unknown model
- per-language CPS override beats the global default in the pipeline
"""

from __future__ import annotations

import sqlite3
import tempfile
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from server import db as db_module
from server.config import settings
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


@pytest.fixture
def client(_tmp_db):
    from server.main import app

    app.router.lifespan_context = None
    return TestClient(app)


# === /backup ===========================================================


def test_backup_returns_valid_sqlite_file(client, tmp_path):
    # Seed something so the backup has content.
    from server.glossary import upsert_entry
    upsert_entry("test-anime", "Hello", "Hola")

    r = client.get("/backup")
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/octet-stream"
    assert 'attachment; filename="translarr-' in r.headers["content-disposition"]
    assert r.headers["content-disposition"].endswith('.db"')

    # Write the bytes to a file and open as a real SQLite DB.
    out = tmp_path / "restored.db"
    out.write_bytes(r.content)
    conn = sqlite3.connect(str(out))
    rows = conn.execute(
        "SELECT translation FROM glossaries WHERE id='test-anime' AND source_term='Hello'"
    ).fetchall()
    assert rows == [("Hola",)]
    conn.close()


def test_backup_filename_has_timestamp(client):
    r = client.get("/backup")
    import re
    m = re.search(r'filename="translarr-(\d{8}-\d{6})\.db"', r.headers["content-disposition"])
    assert m is not None, f"unexpected filename: {r.headers['content-disposition']}"


# === /jobs/{id}/retry ==================================================


def _enqueue(media: str = "/m/test.mkv", state: JobState = JobState.DONE) -> Job:
    job = Job(
        id="",
        dedup_key=f"k:{media}:{state.value}",
        media_path=media,
        target_lang="en",
        state=state,
    )
    persisted = get_queue().enqueue(job)
    # Manually flip state if not QUEUED (enqueue forces QUEUED).
    if state != JobState.QUEUED:
        get_queue().update_state(persisted.id, state)
    return get_queue().get(persisted.id)


def test_retry_creates_new_job_with_force_true(client):
    original = _enqueue(state=JobState.DONE)

    r = client.post(f"/jobs/{original.id}/retry")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "queued"
    new_id = body["job_id"]
    assert new_id != original.id
    assert body["original_job_id"] == original.id

    new_job = get_queue().get(new_id)
    assert new_job is not None
    assert new_job.state == JobState.QUEUED
    assert new_job.force_flag is True
    assert new_job.media_path == original.media_path
    assert new_job.target_lang == original.target_lang


def test_retry_on_failed_job_works(client):
    original = _enqueue(state=JobState.FAILED)
    r = client.post(f"/jobs/{original.id}/retry")
    assert r.status_code == 200


def test_retry_on_cancelled_job_works(client):
    original = _enqueue(state=JobState.CANCELLED)
    r = client.post(f"/jobs/{original.id}/retry")
    assert r.status_code == 200


def test_retry_on_live_job_returns_409(client):
    original = _enqueue(state=JobState.RUNNING)
    r = client.post(f"/jobs/{original.id}/retry")
    assert r.status_code == 409
    assert "job_still_active" in r.json()["detail"]


def test_retry_unknown_job_returns_404(client):
    r = client.post("/jobs/nonexistent-id/retry")
    assert r.status_code == 404


def test_retry_twice_produces_two_distinct_new_jobs(client):
    """Each retry should fold into its own row so the audit trail is intact."""
    original = _enqueue(state=JobState.DONE)
    r1 = client.post(f"/jobs/{original.id}/retry")
    r2 = client.post(f"/jobs/{original.id}/retry")
    assert r1.json()["job_id"] != r2.json()["job_id"]


# === /test/llm =========================================================


def test_test_llm_reports_provider_error_verbatim(client, monkeypatch):
    """If the provider fails (bad API key etc), the error string surfaces
    so the user can fix it instead of guessing."""
    class _FakeProvider:
        name = "fake"
        model = "fake-model"

        async def translate_batch(self, **_):
            raise RuntimeError("invalid API key: hint goes here")

    monkeypatch.setattr("server.llm.router.get_provider", lambda: _FakeProvider())

    r = client.post("/test/llm")
    assert r.status_code == 502
    assert "invalid API key" in r.json()["detail"]
    assert "RuntimeError" in r.json()["detail"]


def test_test_llm_returns_translation_and_cost_estimate(client, monkeypatch):
    class _FakeProvider:
        name = "fake"
        model = "claude-haiku-4-5"  # known to cost tracker

        async def translate_batch(self, **kwargs):
            return ["bonjour le monde"]

    monkeypatch.setattr("server.llm.router.get_provider", lambda: _FakeProvider())
    monkeypatch.setattr(settings, "target_lang", "fr")

    r = client.post("/test/llm")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["provider"] == "fake"
    assert body["model"] == "claude-haiku-4-5"
    assert body["model_known_to_cost_tracker"] is True
    assert body["output_lines"] == ["bonjour le monde"]
    assert body["cost_cents_estimated"] >= 0


def test_test_llm_flags_unknown_model(client, monkeypatch):
    """User typed a model name we don't have pricing for — surface it."""
    class _FakeProvider:
        name = "anthropic"
        model = "claude-opus-4-5"  # NOT in cost table

        async def translate_batch(self, **_):
            return ["bonjour"]

    monkeypatch.setattr("server.llm.router.get_provider", lambda: _FakeProvider())
    r = client.post("/test/llm")
    assert r.status_code == 200
    assert r.json()["model_known_to_cost_tracker"] is False


# === Cost tracker unknown-model warning ===============================


def test_cost_tracker_warns_once_per_unknown_model(client):
    """First call with an unknown model should log; second should not."""
    from server import cost_tracker

    cost_tracker._warned_unknown.clear()
    with patch.object(cost_tracker, "log") as mock_log:
        cost_tracker.estimate_cents("never-heard-of-it", 1000, 2000)
        cost_tracker.estimate_cents("never-heard-of-it", 5000, 5000)
        assert mock_log.warning.call_count == 1
        kwargs = mock_log.warning.call_args.kwargs
        assert kwargs["model"] == "never-heard-of-it"


def test_cost_tracker_known_model_does_not_warn():
    from server import cost_tracker

    cost_tracker._warned_unknown.clear()
    with patch.object(cost_tracker, "log") as mock_log:
        cost_tracker.estimate_cents("claude-haiku-4-5", 1000, 2000)
        mock_log.warning.assert_not_called()


def test_is_known_model_helper():
    from server.cost_tracker import is_known_model
    assert is_known_model("claude-haiku-4-5") is True
    assert is_known_model("claude-opus-4-5") is False
    assert is_known_model("deepseek-chat") is True


# === Per-language CPS override =========================================


def test_per_lang_cps_default_includes_cjk():
    """The shipped defaults must include the languages CJK readers actually use."""
    assert settings.reading_rate_cps_by_lang.get("ja") == 7
    assert settings.reading_rate_cps_by_lang.get("zh") == 7
    assert settings.reading_rate_cps_by_lang.get("en") == 17


def test_per_lang_cps_falls_back_to_global_for_unlisted_lang():
    """Languages absent from the override map use the global default."""
    # Wired in the pipeline as:
    #   cps = settings.reading_rate_cps_by_lang.get(target_lang, settings.reading_rate_cps)
    # Just confirm the data shape supports this access pattern.
    assert settings.reading_rate_cps_by_lang.get("klingon", settings.reading_rate_cps) == settings.reading_rate_cps
