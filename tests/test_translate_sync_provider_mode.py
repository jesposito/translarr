"""/translate/sync provider-mode gating.

When Emby's "Download missing subtitles" scheduled task fires, it can call
/translate/sync for every file in every library — including files that have
no foreign-language source track to translate from. The provider-mode
setting controls how the endpoint reacts:

  off    — 404 immediately, no work done.
  smart  — ffprobe first; 404 if there is no non-target text track.
  always — original v0.1 behavior: enqueue every request, let the worker
           detect-and-skip.

These tests stub list_sub_tracks so we don't need real media files.
"""

from __future__ import annotations

import tempfile

import pytest
from fastapi.testclient import TestClient

from server import db as db_module
from server.config import settings
from server.queue import sqlite as sqlite_q
from server.subs.extract import TrackInfo


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
def client(_tmp_db, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "media_root", tmp_path)
    from server.main import app

    app.router.lifespan_context = None
    return TestClient(app)


def _make_media(tmp_path, name="movie.mkv"):
    p = tmp_path / name
    p.write_bytes(b"fake-mkv")
    return p


def test_mode_off_returns_404(client, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "emby_provider_mode", "off")
    _make_media(tmp_path)

    r = client.post("/translate/sync", json={"media_path": "movie.mkv"})
    assert r.status_code == 404
    assert r.json()["detail"] == "emby_provider_mode_off"


def test_mode_smart_skips_when_only_target_lang_tracks(client, tmp_path, monkeypatch):
    """Library scan hits an English-only file; smart mode 404s without enqueueing."""
    monkeypatch.setattr(settings, "emby_provider_mode", "smart")
    monkeypatch.setattr(settings, "target_lang", "en")
    _make_media(tmp_path)

    async def _fake_tracks(path):
        return [TrackInfo(index=2, codec="subrip", language="eng", title="English")]

    monkeypatch.setattr("server.main.list_sub_tracks", _fake_tracks)

    r = client.post("/translate/sync", json={"media_path": "movie.mkv"})
    assert r.status_code == 404
    assert r.json()["detail"] == "no_translateable_source_track"


def test_mode_smart_skips_when_only_bitmap_tracks(client, tmp_path, monkeypatch):
    """PGS/VOBSUB-only files have no text track at all; smart mode 404s."""
    monkeypatch.setattr(settings, "emby_provider_mode", "smart")
    monkeypatch.setattr(settings, "target_lang", "en")
    _make_media(tmp_path)

    async def _fake_tracks(path):
        return [TrackInfo(index=2, codec="hdmv_pgs_subtitle", language="rus", title=None)]

    monkeypatch.setattr("server.main.list_sub_tracks", _fake_tracks)

    r = client.post("/translate/sync", json={"media_path": "movie.mkv"})
    assert r.status_code == 404


def test_mode_smart_accepts_foreign_text_track(client, tmp_path, monkeypatch):
    """A non-target text track means we should proceed (queue the job).

    No workers run in tests (lifespan disabled), so the short-poll
    eventually returns 202. SYNC_POLL_TIMEOUT is patched to 1 s to keep
    the test fast.
    """
    monkeypatch.setattr(settings, "emby_provider_mode", "smart")
    monkeypatch.setattr(settings, "target_lang", "en")
    monkeypatch.setattr("server.main.SYNC_POLL_TIMEOUT", 1)
    _make_media(tmp_path)

    async def _fake_tracks(path):
        return [TrackInfo(index=2, codec="subrip", language="rus", title="Russian")]

    monkeypatch.setattr("server.main.list_sub_tracks", _fake_tracks)

    r = client.post("/translate/sync", json={"media_path": "movie.mkv"})
    # Smart mode proceeded past the gate. With no worker the poll times
    # out and we get 202 "translation_in_progress" — proves we enqueued.
    assert r.status_code == 202


def test_mode_smart_accepts_unknown_lang_track(client, tmp_path, monkeypatch):
    """A text track with no language tag is treated as translateable — we
    can't tell what language it is without extracting, so let the worker
    figure it out."""
    monkeypatch.setattr(settings, "emby_provider_mode", "smart")
    monkeypatch.setattr(settings, "target_lang", "en")
    monkeypatch.setattr("server.main.SYNC_POLL_TIMEOUT", 1)
    _make_media(tmp_path)

    async def _fake_tracks(path):
        return [TrackInfo(index=2, codec="subrip", language=None, title=None)]

    monkeypatch.setattr("server.main.list_sub_tracks", _fake_tracks)
    r = client.post("/translate/sync", json={"media_path": "movie.mkv"})
    assert r.status_code == 202


def test_mode_always_enqueues_even_target_only(client, tmp_path, monkeypatch):
    """Original v0.1 behavior — always mode enqueues regardless."""
    monkeypatch.setattr(settings, "emby_provider_mode", "always")
    monkeypatch.setattr(settings, "target_lang", "en")
    monkeypatch.setattr("server.main.SYNC_POLL_TIMEOUT", 1)
    _make_media(tmp_path)

    async def _fake_tracks(path):
        return [TrackInfo(index=2, codec="subrip", language="eng", title="English")]

    monkeypatch.setattr("server.main.list_sub_tracks", _fake_tracks)

    r = client.post("/translate/sync", json={"media_path": "movie.mkv"})
    # In always mode the pre-check is skipped — request enqueues and
    # short-polls. Without workers we get 202.
    assert r.status_code == 202


def test_smart_mode_fast_path_when_already_translated(client, tmp_path, monkeypatch):
    """If the .translarr.srt already exists, return it instantly — the
    smart pre-check fires only after the fast path."""
    monkeypatch.setattr(settings, "emby_provider_mode", "smart")
    monkeypatch.setattr(settings, "target_lang", "en")
    media = _make_media(tmp_path)
    out = media.parent / f"{media.stem}.en.translarr.srt"
    out.write_text("1\n00:00:00,000 --> 00:00:02,000\nhello\n")

    r = client.post("/translate/sync", json={"media_path": "movie.mkv"})
    assert r.status_code == 200
    assert r.json()["output_path"] == str(out)
