import tempfile

import pytest
from fastapi.testclient import TestClient

from server import db as db_module
from server.queue import sqlite as sqlite_q


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
    # Import app inside fixture so lifespan/env are fresh per test.
    from server.main import app

    # Disable the lifespan worker for unit tests — we'll exercise queue directly.
    app.router.lifespan_context = None
    return TestClient(app)


def test_post_translate_returns_job_id(client):
    r = client.post(
        "/translate",
        json={"media_path": "/movies/x.mkv", "source_lang": "ru", "target_lang": "en"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "queued"
    assert body["job_id"]


def test_post_translate_dedup_returns_existing(client):
    r1 = client.post(
        "/translate", json={"media_path": "/movies/x.mkv", "target_lang": "en"}
    ).json()
    r2 = client.post(
        "/translate", json={"media_path": "/movies/x.mkv", "target_lang": "en"}
    ).json()
    assert r2["status"] == "dedup"
    assert r2["job_id"] == r1["job_id"]


def test_post_translate_different_target_lang_not_deduped(client):
    en = client.post("/translate", json={"media_path": "/movies/x.mkv", "target_lang": "en"}).json()
    es = client.post("/translate", json={"media_path": "/movies/x.mkv", "target_lang": "es"}).json()
    assert en["job_id"] != es["job_id"]
    assert en["status"] == "queued"
    assert es["status"] == "queued"


def test_get_jobs_returns_job_state(client):
    enq = client.post("/translate", json={"media_path": "/movies/x.mkv", "target_lang": "en"}).json()
    r = client.get(f"/jobs/{enq['job_id']}")
    assert r.status_code == 200
    body = r.json()
    assert body["id"] == enq["job_id"]
    assert body["state"] == "queued"


def test_get_jobs_404_for_unknown(client):
    r = client.get("/jobs/does-not-exist")
    assert r.status_code == 404


def test_translate_unaffected_when_emby_configured_but_unreachable(client, monkeypatch):
    """Library-refresh hook must not break /translate when Emby is unreachable.

    The hook is fire-and-forget; even if Emby's host doesn't resolve, the queue
    should still accept the job. We only exercise the request path here — the
    refresh would only run after a successful translation completes in a worker.
    """
    from server.config import settings

    monkeypatch.setattr(settings, "emby_url", "http://nonexistent-host.invalid:8096")
    monkeypatch.setattr(settings, "emby_api_key", "x")

    r = client.post(
        "/translate",
        json={"media_path": "/movies/refresh-test.mkv", "target_lang": "en"},
    )
    assert r.status_code == 200
    assert r.json()["status"] == "queued"


def test_delete_jobs_cancels_pending(client):
    enq = client.post("/translate", json={"media_path": "/movies/x.mkv", "target_lang": "en"}).json()
    r = client.delete(f"/jobs/{enq['job_id']}")
    assert r.status_code == 200
    assert r.json()["status"] == "cancelled"
    # State now cancelled.
    r2 = client.get(f"/jobs/{enq['job_id']}")
    assert r2.json()["state"] == "cancelled"
