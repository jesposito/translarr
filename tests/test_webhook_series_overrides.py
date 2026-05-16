"""Regression: webhook-initiated jobs must apply per-series overrides.

Before this was wired into ``webhooks.queue.enqueue``, a Sonarr or
Radarr webhook for a media file under a configured series silently
fell back to the global TARGET_LANG. The per-series feature only worked
for jobs initiated through the /translate or /translate/sync endpoints.

These tests pin the contract:
- a webhook for a path matching a series_config's path_prefix gets
  the series' target_lang, source_lang, and glossary_id baked into
  the Job row.
- when no series matches, the global default is used unchanged.
"""

from __future__ import annotations

import tempfile

import pytest

from server import db as db_module
from server.config import settings
from server.queue import sqlite as sqlite_q
from server.queue.sqlite import get_queue
from server.series_config import upsert_series
from server.webhooks.queue import enqueue


@pytest.fixture(autouse=True)
def _tmp_db(monkeypatch):
    tmp = tempfile.mkdtemp()
    monkeypatch.setenv("TRANSLARR_DATA_DIR", tmp)
    db_module.close_for_tests()
    sqlite_q.reset_for_tests()
    yield
    db_module.close_for_tests()
    sqlite_q.reset_for_tests()


@pytest.mark.asyncio
async def test_webhook_applies_series_target_lang(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "target_lang", "en")
    upsert_series(
        "anime",
        source_lang="ja",
        target_lang="de",
        path_prefix=str(tmp_path / "Anime"),
    )

    media = tmp_path / "Anime/Show/ep01.mkv"
    job_id = await enqueue(media)
    assert job_id is not None

    job = get_queue().get(job_id)
    assert job is not None
    assert job.target_lang == "de"
    assert job.source_lang == "ja"
    assert job.glossary_id == "anime"


@pytest.mark.asyncio
async def test_webhook_falls_back_to_default_when_no_series(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "target_lang", "en")
    media = tmp_path / "Movies/film.mkv"
    job_id = await enqueue(media)

    job = get_queue().get(job_id)
    assert job is not None
    assert job.target_lang == "en"
    assert job.source_lang is None
    assert job.glossary_id is None


@pytest.mark.asyncio
async def test_webhook_explicit_target_lang_wins_over_series(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "target_lang", "en")
    upsert_series(
        "anime",
        target_lang="de",
        path_prefix=str(tmp_path / "Anime"),
    )

    media = tmp_path / "Anime/Show/ep01.mkv"
    job_id = await enqueue(media, target_lang="fr")

    job = get_queue().get(job_id)
    assert job is not None
    assert job.target_lang == "fr"  # explicit override beats series


@pytest.mark.asyncio
async def test_webhook_dedup_keys_match_series_resolved_target(monkeypatch, tmp_path):
    """Two webhooks for the same media must dedup, even though the dedup
    key is now built from the series-resolved target_lang."""
    monkeypatch.setattr(settings, "target_lang", "en")
    upsert_series(
        "anime",
        target_lang="de",
        path_prefix=str(tmp_path / "Anime"),
    )

    media = tmp_path / "Anime/Show/ep01.mkv"
    first = await enqueue(media)
    second = await enqueue(media)
    assert first is not None
    assert second is None  # Dedup hit silently skipped.
