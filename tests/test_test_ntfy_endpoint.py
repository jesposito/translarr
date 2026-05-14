"""/test/ntfy — Settings page test-push button (UX polish slice)."""

from __future__ import annotations

import tempfile

import httpx
import pytest
import respx
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


def test_test_ntfy_400_when_unconfigured(client, monkeypatch):
    monkeypatch.setattr(settings, "ntfy_url", None)
    r = client.post("/test/ntfy")
    assert r.status_code == 400
    assert r.json()["detail"] == "ntfy_not_configured"


def test_test_ntfy_success(client, monkeypatch):
    monkeypatch.setattr(settings, "ntfy_url", "https://ntfy.local/topic")
    with respx.mock(base_url="https://ntfy.local") as mock:
        mock.post("/topic").mock(return_value=httpx.Response(200))
        r = client.post("/test/ntfy")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["ntfy_response_status"] == 200


def test_test_ntfy_propagates_upstream_error(client, monkeypatch):
    monkeypatch.setattr(settings, "ntfy_url", "https://ntfy.local/topic")
    with respx.mock(base_url="https://ntfy.local") as mock:
        mock.post("/topic").mock(return_value=httpx.Response(503, text="upstream down"))
        r = client.post("/test/ntfy")
    assert r.status_code == 502
    assert "503" in r.json()["detail"]


def test_test_ntfy_handles_network_error(client, monkeypatch):
    monkeypatch.setattr(settings, "ntfy_url", "https://ntfy.local/topic")
    with respx.mock(base_url="https://ntfy.local") as mock:
        mock.post("/topic").mock(side_effect=httpx.ConnectError("dns refused"))
        r = client.post("/test/ntfy")
    assert r.status_code == 502
    assert "request failed" in r.json()["detail"]
