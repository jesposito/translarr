from fastapi.testclient import TestClient

from server.main import app

client = TestClient(app)


def test_radarr_ignores_unknown_event():
    resp = client.post("/webhooks/radarr", json={"eventType": "Grab"})
    assert resp.status_code == 200
    assert resp.json()["status"] == "ignored"


def test_sonarr_ignores_unknown_event():
    resp = client.post("/webhooks/sonarr", json={"eventType": "Grab"})
    assert resp.status_code == 200
    assert resp.json()["status"] == "ignored"


def test_emby_handles_test_event():
    resp = client.post("/webhooks/emby", json={"Event": "Test"})
    assert resp.status_code == 200


def test_jellyfin_handles_test_event():
    resp = client.post("/webhooks/jellyfin", json={"NotificationType": "Test"})
    assert resp.status_code == 200
