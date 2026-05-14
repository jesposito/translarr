"""Tests for the Emby `playback.start` webhook path (TR-2yt).

The endpoint is the only on-demand auto-translate surface: when a user
presses Play on an item that needs translation, Emby fires
`playback.start`, Translarr enqueues, and ~1-2 minutes later the target-
language track appears in the player.

The path is gated behind ``AUTO_TRANSLATE_ON_PLAYBACK`` (default OFF).
These tests cover:

- the flag-off default (returns ``playback_disabled``, no enqueue);
- the flag-on happy path (returns ``queued``, persists a job);
- dedup against a repeat-fire from the same item;
- both event-name shapes Emby emits across versions.
"""

from __future__ import annotations

import tempfile

import pytest
from fastapi.testclient import TestClient

from server import db as db_module
from server.config import settings
from server.queue import sqlite as sqlite_q
from server.queue.base import JobState
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


def _playback_payload(path: str, event: str = "playback.start") -> dict:
    return {"Event": event, "Item": {"Path": path}}


def test_playback_disabled_by_default(client, monkeypatch):
    monkeypatch.setattr(settings, "auto_translate_on_playback", False)
    resp = client.post("/webhooks/emby", json=_playback_payload("/media/x.mkv"))
    assert resp.status_code == 200
    assert resp.json()["status"] == "playback_disabled"
    # No job was persisted.
    total, _ = get_queue().list_jobs(None, 50, 0)
    assert total == 0


def test_playback_enqueues_when_enabled(client, monkeypatch):
    monkeypatch.setattr(settings, "auto_translate_on_playback", True)
    resp = client.post("/webhooks/emby", json=_playback_payload("/media/Show/S01E01.mkv"))
    assert resp.status_code == 200
    assert resp.json()["status"] == "queued"
    total, jobs = get_queue().list_jobs(None, 50, 0)
    assert total == 1
    assert jobs[0].media_path == "/media/Show/S01E01.mkv"
    assert jobs[0].state == JobState.QUEUED


def test_playback_dedup_blocks_repeat_fires(client, monkeypatch):
    """Emby may fire playback.start multiple times for the same session
    (skip-back, scrub, client switch). Dedup must keep job count = 1."""
    monkeypatch.setattr(settings, "auto_translate_on_playback", True)
    body = _playback_payload("/media/Show/S01E01.mkv")
    first = client.post("/webhooks/emby", json=body)
    second = client.post("/webhooks/emby", json=body)
    assert first.json()["status"] == "queued"
    assert second.json()["status"] == "dedup"
    total, _ = get_queue().list_jobs(None, 50, 0)
    assert total == 1


def test_playback_accepts_legacy_event_name(client, monkeypatch):
    """Older Emby builds + the dotnet plugin use `PlaybackStart` (camel)."""
    monkeypatch.setattr(settings, "auto_translate_on_playback", True)
    resp = client.post(
        "/webhooks/emby", json=_playback_payload("/media/x.mkv", event="PlaybackStart")
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "queued"


def test_playback_no_path_returns_no_path(client, monkeypatch):
    monkeypatch.setattr(settings, "auto_translate_on_playback", True)
    resp = client.post("/webhooks/emby", json={"Event": "playback.start", "Item": {}})
    assert resp.status_code == 200
    assert resp.json()["status"] == "no_path"
    total, _ = get_queue().list_jobs(None, 50, 0)
    assert total == 0


def test_library_event_path_unchanged_by_playback_flag(client, monkeypatch):
    """Flipping the playback flag must not regress library-event handling."""
    monkeypatch.setattr(settings, "auto_translate_on_playback", False)
    resp = client.post(
        "/webhooks/emby",
        json={"Event": "library.new", "Item": {"Path": "/media/Show/S01E01.mkv"}},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "queued"
