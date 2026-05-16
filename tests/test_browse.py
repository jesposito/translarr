"""Tests for the file browser endpoint."""

from __future__ import annotations

import tempfile
from pathlib import Path

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
def client(_tmp_db, tmp_path):
    from server.config import settings

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(settings, "media_root", tmp_path)
    from server.main import app

    app.router.lifespan_context = None
    yield TestClient(app)
    monkeypatch.undo()


def _make_media(base: Path, structure: dict) -> None:
    """Create a fake media tree from a dict.

    Keys are names. Values:
    - None → directory (empty)
    - dict → directory with contents
    - str ending in .mkv/.mp4 → create fake media file
    - str ending in .translarr.srt → create fake translation
    - other str → create file with that content
    """
    for name, contents in structure.items():
        p = base / name
        if contents is None:
            p.mkdir(parents=True, exist_ok=True)
        elif isinstance(contents, dict):
            p.mkdir(parents=True, exist_ok=True)
            _make_media(p, contents)
        elif isinstance(contents, str):
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(contents)


class TestBrowse:
    def test_browse_root(self, client, tmp_path):
        _make_media(tmp_path, {
            "Movies": None,
            "TV": None,
            "readme.txt": "hello",
        })
        r = client.get("/browse")
        assert r.status_code == 200
        body = r.json()
        assert body["path"] == "/"
        assert body["parent"] is None
        dir_names = {d["name"] for d in body["dirs"]}
        assert "Movies" in dir_names
        assert "TV" in dir_names
        # .txt is not a media file — should not appear in files.
        assert body["total_files"] == 0

    def test_browse_with_media_files(self, client, tmp_path):
        _make_media(tmp_path, {
            "Movies": {
                "Movie (2025)": {
                    "movie.2025.mkv": "fake",
                },
            },
        })
        r = client.get("/browse", params={"path": "Movies/Movie (2025)"})
        assert r.status_code == 200
        body = r.json()
        assert body["total_files"] == 1
        assert body["files"][0]["name"] == "movie.2025.mkv"
        assert body["files"][0]["translated"] is False
        assert body["files"][0]["translations"] == []
        # Parent should be Movies.
        assert body["parent"] == "Movies"

    def test_browse_detects_translations(self, client, tmp_path):
        _make_media(tmp_path, {
            "Movies": {
                "Test (2025)": {
                    "test.2025.mkv": "fake",
                    "test.2025.en.translarr.srt": "1\n00:00:01,000 --> 00:00:02,000\nHello",
                },
            },
        })
        r = client.get("/browse", params={"path": "Movies/Test (2025)"})
        body = r.json()
        assert body["total_files"] == 1
        assert body["translated_files"] == 1
        f = body["files"][0]
        assert f["translated"] is True
        assert len(f["translations"]) == 1
        assert f["translations"][0]["lang"] == "en"

    def test_browse_multiple_translations(self, client, tmp_path):
        _make_media(tmp_path, {
            "TV": {
                "Show": {
                    "s01e01.mkv": "fake",
                    "s01e01.en.translarr.srt": "sub",
                    "s01e01.es.translarr.srt": "sub",
                },
            },
        })
        r = client.get("/browse", params={"path": "TV/Show"})
        body = r.json()
        f = body["files"][0]
        assert f["translated"] is True
        langs = {t["lang"] for t in f["translations"]}
        assert langs == {"en", "es"}

    def test_browse_path_traversal_blocked(self, client, tmp_path):
        r = client.get("/browse", params={"path": "../../../etc"})
        assert r.status_code == 200
        body = r.json()
        assert "error" in body

    def test_browse_nonexistent_dir(self, client, tmp_path):
        r = client.get("/browse", params={"path": "nonexistent"})
        assert r.status_code == 200
        body = r.json()
        assert "error" in body

    def test_browse_dir_has_media_flag(self, client, tmp_path):
        _make_media(tmp_path, {
            "HasMedia": {
                "movie.mkv": "fake",
            },
            "EmptyDir": {
                "notes.txt": "nothing here",
            },
        })
        r = client.get("/browse")
        body = r.json()
        dirs = {d["name"]: d for d in body["dirs"]}
        assert dirs["HasMedia"]["has_media"] is True
        assert dirs["EmptyDir"]["has_media"] is False

    def test_browse_skips_hidden_files(self, client, tmp_path):
        _make_media(tmp_path, {
            ".DS_Store": "junk",
            "._resource": "junk",
            "movie.mkv": "fake",
        })
        r = client.get("/browse")
        body = r.json()
        assert body["total_files"] == 1
        dir_names = {d["name"] for d in body["dirs"]}
        assert ".DS_Store" not in dir_names

    def test_browse_nested_series_structure(self, client, tmp_path):
        _make_media(tmp_path, {
            "TV": {
                "Anime Show": {
                    "Season 1": {
                        "s01e01.mkv": "fake",
                        "s01e01.en.translarr.srt": "sub",
                    },
                    "Season 2": {
                        "s02e01.mkv": "fake",
                    },
                },
            },
        })
        # Browse the show level — should see Season dirs.
        r = client.get("/browse", params={"path": "TV/Anime Show"})
        body = r.json()
        assert len(body["dirs"]) == 2
        assert body["total_files"] == 0

        # Browse Season 1 — should see files.
        r = client.get("/browse", params={"path": "TV/Anime Show/Season 1"})
        body = r.json()
        assert body["total_files"] == 1
        assert body["translated_files"] == 1
