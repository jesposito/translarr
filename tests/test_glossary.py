"""Tests for the glossary API and persistence."""

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


class TestGlossaryAPI:
    def test_list_empty(self, client):
        r = client.get("/glossaries")
        assert r.status_code == 200
        assert r.json()["glossaries"] == []

    def test_create_and_list(self, client):
        r = client.post("/glossaries/anime-show", json={
            "source_term": "タンジロウ",
            "translation": "Tanjiro",
            "target_lang": "en",
            "notes": "Main character",
        })
        assert r.status_code == 200

        r = client.get("/glossaries")
        g = r.json()["glossaries"]
        assert len(g) == 1
        assert g[0]["id"] == "anime-show"
        assert g[0]["entry_count"] == 1

    def test_get_entries(self, client):
        # Create two entries.
        client.post("/glossaries/anime-show", json={
            "source_term": "タンジロウ",
            "translation": "Tanjiro",
        })
        client.post("/glossaries/anime-show", json={
            "source_term": "禰豆子",
            "translation": "Nezuko",
        })

        r = client.get("/glossaries/anime-show")
        body = r.json()
        assert body["id"] == "anime-show"
        assert len(body["entries"]) == 2
        terms = {e["source_term"] for e in body["entries"]}
        assert terms == {"タンジロウ", "禰豆子"}

    def test_upsert_updates_existing(self, client):
        client.post("/glossaries/show", json={
            "source_term": "kami",
            "translation": "god",
        })
        client.post("/glossaries/show", json={
            "source_term": "kami",
            "translation": "spirit",
        })
        r = client.get("/glossaries/show")
        assert len(r.json()["entries"]) == 1
        assert r.json()["entries"][0]["translation"] == "spirit"

    def test_delete_entry(self, client):
        client.post("/glossaries/show", json={
            "source_term": "kami",
            "translation": "spirit",
        })
        r = client.delete("/glossaries/show/kami")
        assert r.status_code == 200
        r = client.get("/glossaries/show")
        assert len(r.json()["entries"]) == 0

    def test_delete_entry_not_found(self, client):
        r = client.delete("/glossaries/nope/missing")
        assert r.status_code == 404

    def test_delete_glossary(self, client):
        client.post("/glossaries/show", json={
            "source_term": "kami",
            "translation": "spirit",
        })
        r = client.delete("/glossaries/show")
        assert r.status_code == 200
        assert r.json()["deleted"] == 1

    def test_bulk_import(self, client):
        r = client.post("/glossaries/anime/import", json={
            "entries": [
                {"source_term": "忍者", "translation": "ninja"},
                {"source_term": "侍", "translation": "samurai"},
                {"source_term": "主君", "translation": "lord", "notes": "respectful"},
                {"source_term": "", "translation": "skip me"},  # Empty source → skipped
            ],
        })
        assert r.status_code == 200
        assert r.json()["imported"] == 3

        r = client.get("/glossaries/anime")
        assert len(r.json()["entries"]) == 3

    def test_missing_fields_rejected(self, client):
        r = client.post("/glossaries/test", json={"source_term": "hello"})
        assert r.status_code == 400

    def test_different_target_langs(self, client):
        """Same term, different target languages → separate entries."""
        client.post("/glossaries/show", json={
            "source_term": "hello",
            "translation": "hola",
            "target_lang": "es",
        })
        client.post("/glossaries/show", json={
            "source_term": "hello",
            "translation": "bonjour",
            "target_lang": "fr",
        })
        r = client.get("/glossaries/show")
        assert len(r.json()["entries"]) == 2


class TestGlossaryMap:
    def test_get_glossary_map_for_pipeline(self, client):
        """The pipeline gets a flat dict from get_glossary_map()."""
        from server.glossary import get_glossary_map

        client.post("/glossaries/show", json={
            "source_term": "忍者",
            "translation": "ninja",
            "target_lang": "en",
        })
        client.post("/glossaries/show", json={
            "source_term": "侍",
            "translation": "samurai",
            "target_lang": "en",
        })
        # Different target lang — should not appear.
        client.post("/glossaries/show", json={
            "source_term": "忍者",
            "translation": "ninja (ES)",
            "target_lang": "es",
        })

        m = get_glossary_map("show", "en")
        assert m == {"忍者": "ninja", "侍": "samurai"}
