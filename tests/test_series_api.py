"""HTTP-level tests for /series and /series/lookup endpoints.

Complements test_series_config.py (which exercises the data layer)
with end-to-end API tests covering route ordering, Pydantic validation,
and the "longest prefix match" semantics under real HTTP requests.
"""

from __future__ import annotations

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
    from server.main import app

    app.router.lifespan_context = None
    return TestClient(app)


class TestSeriesEndpoints:
    def test_empty_list(self, client):
        r = client.get("/series")
        assert r.status_code == 200
        assert r.json() == {"series": []}

    def test_create_and_get(self, client):
        r = client.put("/series/anime", json={"source_lang": "ja", "target_lang": "en"})
        assert r.status_code == 200
        r = client.get("/series/anime")
        assert r.status_code == 200
        body = r.json()
        assert body["id"] == "anime"
        assert body["source_lang"] == "ja"
        assert body["target_lang"] == "en"

    def test_get_missing_returns_404(self, client):
        r = client.get("/series/nonexistent")
        assert r.status_code == 404
        assert r.json()["detail"] == "series not found"

    def test_delete_missing_returns_404(self, client):
        r = client.delete("/series/nonexistent")
        assert r.status_code == 404

    def test_delete_existing(self, client):
        client.put("/series/foo", json={"target_lang": "de"})
        r = client.delete("/series/foo")
        assert r.status_code == 200
        assert client.get("/series/foo").status_code == 404

    def test_lookup_route_does_not_collide_with_path_id(self, client):
        """Regression: /series/lookup once matched the {series_id} path so
        the lookup handler was never reached. The route ordering MUST keep
        the static path before the dynamic one."""
        client.put("/series/anime", json={
            "source_lang": "ja",
            "target_lang": "en",
            "path_prefix": "Anime/",
        })
        r = client.get("/series/lookup", params={"path": "Anime/Show/ep01.mkv"})
        assert r.status_code == 200
        assert r.json()["match"]["id"] == "anime"

    def test_lookup_no_match(self, client):
        r = client.get("/series/lookup", params={"path": "unrelated/path.mkv"})
        assert r.status_code == 200
        assert r.json() == {"match": None}

    def test_lookup_longest_prefix_wins(self, client):
        client.put("/series/general-tv", json={"path_prefix": "TV/", "target_lang": "en"})
        client.put("/series/specific-show", json={"path_prefix": "TV/MyShow/", "target_lang": "de"})
        r = client.get("/series/lookup", params={"path": "TV/MyShow/ep01.mkv"})
        assert r.json()["match"]["id"] == "specific-show"

    def test_invalid_lang_code_rejected(self, client):
        # source_lang min_length=2 — single char fails Pydantic validation.
        r = client.put("/series/short", json={"source_lang": "x"})
        assert r.status_code == 422

    def test_upsert_overwrites_existing(self, client):
        client.put("/series/show", json={"target_lang": "en"})
        client.put("/series/show", json={"target_lang": "de"})
        r = client.get("/series/show")
        assert r.json()["target_lang"] == "de"

    def test_auto_translate_flag(self, client):
        client.put("/series/auto", json={"auto_translate": True, "target_lang": "en"})
        r = client.get("/series/auto")
        assert r.json()["auto_translate"] == 1
