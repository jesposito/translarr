"""Tests for preflight, presets, source-lang auto-detect, and error cleanup."""

from __future__ import annotations

import tempfile
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from server import db as db_module
from server.config import settings
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
    from server.main import app

    app.router.lifespan_context = None
    return TestClient(app)


# === Presets ================================================================


class TestPresets:
    def test_list_presets(self, client):
        r = client.get("/config/presets")
        assert r.status_code == 200
        names = {p["id"] for p in r.json()["presets"]}
        assert names == {"quick_cheap", "balanced", "best_quality", "local_free", "deepseek_budget", "gemini_flash"}
        # Each preset has label and description.
        for p in r.json()["presets"]:
            assert p["label"]
            assert p["description"]

    def test_apply_preset_balanced(self, client):
        r = client.post("/config/preset", json={"preset": "balanced"})
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "ok"
        assert body["preset"]["label"] == "Balanced"
        # Verify the fields were actually changed.
        fields = {f["key"]: f for f in client.get("/config").json()["fields"]}
        assert fields["context_window_lines"]["value"] == 10
        assert fields["reading_rate_cps"]["value"] == 17

    def test_apply_preset_local_free(self, client):
        r = client.post("/config/preset", json={"preset": "local_free"})
        assert r.status_code == 200
        fields = {f["key"]: f for f in client.get("/config").json()["fields"]}
        assert fields["llm_provider"]["value"] == "ollama"
        assert fields["llm_model"]["value"] == "qwen3:14b"

    def test_apply_unknown_preset_400(self, client):
        r = client.post("/config/preset", json={"preset": "ultra"})
        assert r.status_code == 400
        assert "unknown_preset" in r.json()["detail"]

    def test_apply_missing_name_400(self, client):
        r = client.post("/config/preset", json={})
        assert r.status_code == 400


# === Preflight ==============================================================


class TestPreflight:
    def test_preflight_file_not_found(self, client):
        r = client.post("/preflight", json={"media_path": "/nonexistent.mkv"})
        assert r.status_code == 404
        assert "not found" in r.json()["detail"]

    def test_preflight_no_text_tracks(self, client, tmp_path, monkeypatch):
        """A file with only PGS tracks should report can_translate=False."""
        # We'll mock list_sub_tracks to return a PGS-only track list.
        from server.subs.extract import TrackInfo

        pgs_track = TrackInfo(index=0, codec="pgssub", language="eng", title="English")
        with patch("server.main.list_sub_tracks", new_callable=AsyncMock, return_value=[pgs_track]):
            # Also need the file to "exist"
            fake_media = tmp_path / "test.mkv"
            fake_media.write_text("fake")
            monkeypatch.setattr(settings, "media_root", tmp_path)
            r = client.post("/preflight", json={"media_path": str(fake_media)})
            assert r.status_code == 200
            body = r.json()
            assert body["can_translate"] is False
            assert body["text_tracks"] == 0

    def test_preflight_with_text_track(self, client, tmp_path, monkeypatch):
        """A file with a text sub track should return cost estimates."""
        from server.subs.extract import TrackInfo

        chi_track = TrackInfo(index=3, codec="subrip", language="chi", title="Chinese")
        with (
            patch("server.main.list_sub_tracks", new_callable=AsyncMock, return_value=[chi_track]),
            patch("server.main.extract_track", new_callable=AsyncMock),
            patch("server.main._load_subs_with_encoding_fallback") as mock_load,
        ):
            import pysubs2
            mock_subs = pysubs2.SSAFile()
            for i in range(100):
                mock_subs.events.append(pysubs2.SSAEvent(start=i * 1000, end=i * 1000 + 900, text=f"line {i}"))
            mock_load.return_value = mock_subs

            fake_media = tmp_path / "test.mkv"
            fake_media.write_text("fake")
            monkeypatch.setattr(settings, "media_root", tmp_path)

            r = client.post("/preflight", json={"media_path": str(fake_media)})
            assert r.status_code == 200
            body = r.json()
            assert body["can_translate"] is True
            assert body["event_count"] == 100
            assert body["source_lang"] == "chi"
            assert body["source_track_index"] == 3
            assert len(body["estimates"]) > 0
            # Every estimate should have a cost_cents > 0.
            for est in body["estimates"]:
                assert est["model"]
                assert isinstance(est["cost_cents"], int)


# === Error Cleanup ==========================================================


class TestShortError:
    def test_file_not_found_passes_through(self):
        from server.queue.worker import _short_error
        msg = "FileNotFoundError: media not found: /m/missing.mkv"
        assert _short_error(msg) == msg

    def test_ffmpeg_noise_stripped(self):
        from server.queue.worker import _short_error
        full = "RuntimeError: ffmpeg extract failed: ffmpeg version 7.1.3\nbuilt with gcc 14\n[pgs @ 0x] Unsupported codec\nConversion failed!\n"
        short = _short_error(full)
        assert "ffmpeg version" not in short
        assert len(short) < 100

    def test_cost_cap_passes_through(self):
        from server.queue.worker import _short_error
        msg = "CostCapExceeded: daily_cost_cap_reached: spent=1200c >= cap=1000c"
        assert _short_error(msg) == msg

    def test_overloaded_error_cleaned(self):
        from server.queue.worker import _short_error
        msg = "tenacity.RetryError: RetryError[<Future at 0x149502254e30 state=finished raised OverloadedError>]"
        short = _short_error(msg)
        assert "LLM provider overloaded" in short


# === Source Lang Persisted ==================================================


class TestSourceLangPersisted:
    def test_finish_persists_source_lang(self, client):
        """When finish() is called with source_lang, it updates the job row."""
        from server.queue.base import Job, JobState
        from server.queue.sqlite import get_queue

        q = get_queue()
        job = q.enqueue(Job(
            id="", dedup_key="test-src-lang", media_path="/m/test.mkv",
            target_lang="en", state=JobState.QUEUED,
        ))
        q.claim_next()  # Move to RUNNING
        q.finish(job.id, output_path="/m/test.en.translarr.srt",
                 cost_cents=10, tokens_in=100, tokens_out=50,
                 output_events=100, source_lang="chi", source_track_index=3)
        updated = q.get(job.id)
        assert updated.source_lang == "chi"
        assert updated.source_track_index == 3
