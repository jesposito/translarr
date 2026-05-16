"""Tests for the glossary picker's candidate enumeration.

The picker walks MEDIA_ROOT at depth 1 and 2, supports server-side
substring search, ranks prefix-match before contains-match, and
annotates each result with glossary / series_config status.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from server import candidates as cand
from server import db as db_module
from server.config import settings
from server.glossary import upsert_entry
from server.queue import sqlite as sqlite_q
from server.series_config import upsert_series


@pytest.fixture(autouse=True)
def _tmp_db(monkeypatch):
    tmp = tempfile.mkdtemp()
    monkeypatch.setenv("TRANSLARR_DATA_DIR", tmp)
    db_module.close_for_tests()
    sqlite_q.reset_for_tests()
    cand.invalidate_cache()
    yield
    db_module.close_for_tests()
    sqlite_q.reset_for_tests()
    cand.invalidate_cache()


def _make_lib(tmp_path: Path, layout: dict) -> Path:
    """Build a fake media library from a nested dict."""
    for name, sub in layout.items():
        p = tmp_path / name
        if isinstance(sub, dict):
            p.mkdir(parents=True, exist_ok=True)
            _make_lib(p, sub)
        else:
            p.write_text(sub or "")
    return tmp_path


def test_returns_empty_for_empty_root(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "media_root", tmp_path)
    assert cand.search_candidates("") == []


def test_finds_depth_2_dirs_under_movies_and_tv(tmp_path, monkeypatch):
    _make_lib(tmp_path, {
        "Movies": {
            "Demon Slayer (2025)": {"movie.mkv": ""},
            "The Brutalist (2024)": {"movie.mkv": ""},
        },
        "TV": {
            "Twin Peaks": {"Season 1": {"ep01.mkv": ""}},
        },
    })
    monkeypatch.setattr(settings, "media_root", tmp_path)
    cand.invalidate_cache()
    results = cand.search_candidates("", limit=10)
    names = [r["name"] for r in results]
    assert "Demon Slayer (2025)" in names
    assert "The Brutalist (2024)" in names
    assert "Twin Peaks" in names
    # Top-level Movies/TV should NOT appear as candidates — they're
    # categories, not series.
    assert "Movies" not in names
    assert "TV" not in names


def test_kind_carries_parent_category(tmp_path, monkeypatch):
    _make_lib(tmp_path, {"Movies": {"Foo": {"f.mkv": ""}}, "TV": {"Bar": {"e.mkv": ""}}})
    monkeypatch.setattr(settings, "media_root", tmp_path)
    cand.invalidate_cache()
    results = cand.search_candidates("")
    by_name = {r["name"]: r["kind"] for r in results}
    assert by_name["Foo"] == "Movies"
    assert by_name["Bar"] == "TV"


def test_flat_layout_treats_top_dir_as_candidate(tmp_path, monkeypatch):
    """When a top-level dir has no subdirs (just media files), it IS
    the candidate."""
    _make_lib(tmp_path, {
        "Loose Movie (2024)": {"movie.mkv": ""},
        "Movies": {"NestedFilm": {"m.mkv": ""}},
    })
    monkeypatch.setattr(settings, "media_root", tmp_path)
    cand.invalidate_cache()
    names = [r["name"] for r in cand.search_candidates("")]
    assert "Loose Movie (2024)" in names
    assert "NestedFilm" in names


def test_substring_filter_case_insensitive(tmp_path, monkeypatch):
    _make_lib(tmp_path, {"Movies": {
        "Demon Slayer": {"m.mkv": ""},
        "The Brutalist": {"m.mkv": ""},
        "Twin Peaks": {"m.mkv": ""},
    }})
    monkeypatch.setattr(settings, "media_root", tmp_path)
    cand.invalidate_cache()
    names = [r["name"] for r in cand.search_candidates("demon")]
    assert names == ["Demon Slayer"]
    names_upper = [r["name"] for r in cand.search_candidates("DEMON")]
    assert names_upper == ["Demon Slayer"]


def test_prefix_match_ranks_ahead_of_contains_match(tmp_path, monkeypatch):
    """'demon' should hit 'Demon Slayer' before 'My Demon'."""
    _make_lib(tmp_path, {"Movies": {
        "My Demon": {"m.mkv": ""},
        "Demon Slayer": {"m.mkv": ""},
    }})
    monkeypatch.setattr(settings, "media_root", tmp_path)
    cand.invalidate_cache()
    results = cand.search_candidates("demon")
    assert results[0]["name"] == "Demon Slayer"
    assert results[1]["name"] == "My Demon"


def test_limit_caps_results(tmp_path, monkeypatch):
    _make_lib(tmp_path, {"Movies": {f"Film{i:03d}": {"m.mkv": ""} for i in range(20)}})
    monkeypatch.setattr(settings, "media_root", tmp_path)
    cand.invalidate_cache()
    assert len(cand.search_candidates("", limit=5)) == 5
    assert len(cand.search_candidates("Film", limit=10)) == 10


def test_glossary_count_annotated(tmp_path, monkeypatch):
    _make_lib(tmp_path, {"Movies": {"Demon Slayer": {"m.mkv": ""}}})
    monkeypatch.setattr(settings, "media_root", tmp_path)
    # Add some glossary entries for the slugified id.
    upsert_entry("demon-slayer", "Tanjiro", "Tanjiro Kamado")
    upsert_entry("demon-slayer", "Nezuko", "Nezuko Kamado")
    cand.invalidate_cache()
    results = cand.search_candidates("demon")
    assert results[0]["glossary_id"] == "demon-slayer"
    assert results[0]["glossary_entry_count"] == 2
    assert results[0]["has_series_config"] is False


def test_series_config_flag(tmp_path, monkeypatch):
    _make_lib(tmp_path, {"TV": {"Demon Slayer": {"m.mkv": ""}}})
    monkeypatch.setattr(settings, "media_root", tmp_path)
    upsert_series("demon-slayer", target_lang="en", path_prefix="TV/Demon Slayer")
    cand.invalidate_cache()
    results = cand.search_candidates("")
    by_name = {r["name"]: r for r in results}
    assert by_name["Demon Slayer"]["has_series_config"] is True


def test_skip_hidden_and_system_dirs(tmp_path, monkeypatch):
    _make_lib(tmp_path, {
        "Movies": {"RealFilm": {"m.mkv": ""}, ".hidden": {"m.mkv": ""}},
        ".trash": {"junk": ""},
        "@eaDir": {"weird": ""},
    })
    monkeypatch.setattr(settings, "media_root", tmp_path)
    cand.invalidate_cache()
    names = [r["name"] for r in cand.search_candidates("")]
    assert ".hidden" not in names
    assert ".trash" not in names
    assert "@eaDir" not in names
    assert "RealFilm" in names


def test_cache_invalidates_with_helper(tmp_path, monkeypatch):
    _make_lib(tmp_path, {"Movies": {"First": {"m.mkv": ""}}})
    monkeypatch.setattr(settings, "media_root", tmp_path)
    cand.invalidate_cache()
    first = [r["name"] for r in cand.search_candidates("")]
    assert "First" in first
    # Add a new film, but DO NOT invalidate — cache hides it.
    (tmp_path / "Movies" / "Second").mkdir()
    (tmp_path / "Movies" / "Second" / "m.mkv").write_text("")
    still_cached = [r["name"] for r in cand.search_candidates("")]
    assert "Second" not in still_cached
    # Now invalidate and re-query.
    cand.invalidate_cache()
    after_invalidate = [r["name"] for r in cand.search_candidates("")]
    assert "Second" in after_invalidate


def test_blank_query_returns_alphabetical_when_no_user_data(tmp_path, monkeypatch):
    """Empty query, no glossaries / configs: pure alphabetical sort."""
    _make_lib(tmp_path, {"Movies": {
        "Zebra": {"m.mkv": ""},
        "Apple": {"m.mkv": ""},
        "Banana": {"m.mkv": ""},
    }})
    monkeypatch.setattr(settings, "media_root", tmp_path)
    cand.invalidate_cache()
    names = [r["name"] for r in cand.search_candidates("")]
    assert names == ["Apple", "Banana", "Zebra"]


def test_blank_query_ranks_user_touched_items_first(tmp_path, monkeypatch):
    """With a large library, items that already have a glossary should
    rise above the alphabetical tail so the user's own work is at the
    top of the picker."""
    _make_lib(tmp_path, {
        "Books": {f"Author{i:03d}": {"b.epub": ""} for i in range(10)},
        "Movies": {
            "Apple Film": {"m.mkv": ""},
            "Demon Slayer (2025)": {"m.mkv": ""},
            "Zebra Doc": {"m.mkv": ""},
        },
    })
    monkeypatch.setattr(settings, "media_root", tmp_path)
    # User has worked on Demon Slayer.
    upsert_entry("demon-slayer-2025", "Tanjiro", "Tanjiro Kamado")
    cand.invalidate_cache()

    names = [r["name"] for r in cand.search_candidates("", limit=5)]
    # Demon Slayer (has glossary) MUST be first.
    assert names[0] == "Demon Slayer (2025)"
    # Rest fall back to alphabetical.
    assert "Apple Film" in names


def test_blank_query_ranks_series_config_above_unconfigured(tmp_path, monkeypatch):
    """Items with a series_config but no glossary still outrank
    untouched items."""
    _make_lib(tmp_path, {"Movies": {
        "Apple Film": {"m.mkv": ""},
        "Banana Film": {"m.mkv": ""},
        "Zebra Film": {"m.mkv": ""},
    }})
    monkeypatch.setattr(settings, "media_root", tmp_path)
    upsert_series("zebra-film", target_lang="en", path_prefix="Movies/Zebra Film")
    cand.invalidate_cache()
    names = [r["name"] for r in cand.search_candidates("")]
    # Zebra Film (configured) ranks ahead of Apple/Banana even though
    # alphabetically it's last.
    assert names[0] == "Zebra Film"


def test_skip_lost_and_found(tmp_path, monkeypatch):
    _make_lib(tmp_path, {
        "lost+found": {"junk": ""},
        "Movies": {"Real": {"m.mkv": ""}},
    })
    monkeypatch.setattr(settings, "media_root", tmp_path)
    cand.invalidate_cache()
    names = [r["name"] for r in cand.search_candidates("")]
    assert "lost+found" not in names
    assert "Real" in names
