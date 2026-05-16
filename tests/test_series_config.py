"""Tests for per-series language override config."""

from __future__ import annotations

import pytest

from server import db as db_module
from server.series_config import (
    delete_series,
    get_series,
    list_series,
    lookup_by_path,
    resolve_overrides,
    upsert_series,
)


@pytest.fixture(autouse=True)
def _fresh_db(tmp_path, monkeypatch):
    """Use a temp DB for every test."""
    monkeypatch.setattr(db_module, "_conn", None)
    monkeypatch.setenv("TRANSLARR_DATA_DIR", str(tmp_path))
    db_module.get_conn()
    yield
    db_module.close_for_tests()


def test_list_empty():
    assert list_series() == []


def test_upsert_and_get():
    upsert_series(
        "demon-slayer",
        source_lang="ja",
        target_lang="en",
        path_prefix="TV/Demon Slayer",
        auto_translate=True,
    )
    cfg = get_series("demon-slayer")
    assert cfg is not None
    assert cfg["source_lang"] == "ja"
    assert cfg["target_lang"] == "en"
    assert cfg["path_prefix"] == "TV/Demon Slayer"
    assert cfg["auto_translate"] == 1
    assert cfg["id"] == "demon-slayer"


def test_upsert_updates_existing():
    upsert_series("test-series", source_lang="ru")
    upsert_series("test-series", source_lang="ko", target_lang="en")
    cfg = get_series("test-series")
    assert cfg["source_lang"] == "ko"
    assert cfg["target_lang"] == "en"


def test_delete_series():
    upsert_series("to-delete")
    assert delete_series("to-delete") is True
    assert delete_series("nonexistent") is False
    assert get_series("to-delete") is None


def test_lookup_by_path_basic():
    upsert_series("show-a", source_lang="ja", path_prefix="TV/Show A")
    upsert_series("show-b", source_lang="ko", path_prefix="TV/Show B")

    match = lookup_by_path("TV/Show A/Season 1/ep01.mkv")
    assert match is not None
    assert match["id"] == "show-a"
    assert match["source_lang"] == "ja"


def test_lookup_by_path_longest_prefix():
    """Two prefixes match; the longest one wins."""
    upsert_series("anime-general", source_lang="ja", path_prefix="TV/Anime")
    upsert_series("demon-slayer", source_lang="ja", target_lang="de", path_prefix="TV/Anime/Demon Slayer")

    match = lookup_by_path("TV/Anime/Demon Slayer/S01/ep01.mkv")
    assert match is not None
    assert match["id"] == "demon-slayer"
    assert match["target_lang"] == "de"


def test_lookup_by_path_no_match():
    upsert_series("show-a", path_prefix="Movies/Action")
    assert lookup_by_path("TV/Comedy/show.mkv") is None


def test_lookup_by_path_empty_prefix():
    upsert_series("global", source_lang="auto")
    # path_prefix is NULL — should not match.
    assert lookup_by_path("anything.mkv") is None


def test_list_series_ordered():
    upsert_series("z-series")
    upsert_series("a-series")
    result = list_series()
    assert [r["id"] for r in result] == ["a-series", "z-series"]


def test_series_auto_translate_flag():
    upsert_series("auto-on", auto_translate=True)
    upsert_series("auto-off", auto_translate=False)
    assert get_series("auto-on")["auto_translate"] == 1
    assert get_series("auto-off")["auto_translate"] == 0


class TestResolveOverrides:
    """resolve_overrides centralises the override-precedence logic that used
    to be duplicated between /translate and /translate/sync."""

    def test_no_series_match_returns_defaults(self):
        target, source, glossary, matched = resolve_overrides(
            "unmatched/path.mkv",
            explicit_target_lang=None,
            explicit_source_lang=None,
            explicit_glossary_id=None,
            default_target_lang="en",
        )
        assert target == "en"
        assert source is None
        assert glossary is None
        assert matched is None

    def test_series_match_fills_unset_values(self):
        upsert_series(
            "anime",
            source_lang="ja",
            target_lang="de",
            path_prefix="TV/Anime",
        )
        target, source, glossary, matched = resolve_overrides(
            "TV/Anime/Show/ep01.mkv",
            explicit_target_lang=None,
            explicit_source_lang=None,
            explicit_glossary_id=None,
            default_target_lang="en",
        )
        assert target == "de"
        assert source == "ja"
        assert glossary == "anime"
        assert matched is not None and matched["id"] == "anime"

    def test_explicit_caller_values_override_series(self):
        upsert_series(
            "anime",
            source_lang="ja",
            target_lang="de",
            path_prefix="TV/Anime",
        )
        target, source, glossary, _ = resolve_overrides(
            "TV/Anime/Show/ep01.mkv",
            explicit_target_lang="fr",
            explicit_source_lang="ru",
            explicit_glossary_id="custom-glossary",
            default_target_lang="en",
        )
        assert target == "fr"
        assert source == "ru"
        assert glossary == "custom-glossary"

    def test_series_with_only_target_lang_set(self):
        # Partial series config: only target_lang is set.
        upsert_series("movies", target_lang="es", path_prefix="Movies/")
        target, source, glossary, _ = resolve_overrides(
            "Movies/Film.mkv",
            explicit_target_lang=None,
            explicit_source_lang=None,
            explicit_glossary_id=None,
            default_target_lang="en",
        )
        assert target == "es"
        assert source is None
        assert glossary == "movies"  # Glossary id always defaults to series id when matched.

    def test_default_target_lang_used_when_nothing_else_set(self):
        # Series matches but has no target_lang.
        upsert_series("partial", source_lang="ja", path_prefix="TV/")
        target, source, _, _ = resolve_overrides(
            "TV/Show/ep.mkv",
            explicit_target_lang=None,
            explicit_source_lang=None,
            explicit_glossary_id=None,
            default_target_lang="en",
        )
        assert target == "en"
        assert source == "ja"
