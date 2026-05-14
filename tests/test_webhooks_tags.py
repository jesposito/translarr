from fastapi.testclient import TestClient

from server.main import app

client = TestClient(app)


def _radarr_payload(event: str, tags: list, path: str = "/movies/x.mkv") -> dict:
    return {
        "eventType": event,
        "movie": {"id": 1, "title": "X", "tags": tags},
        "movieFile": {"path": path},
    }


def _sonarr_payload(event: str, tags: list, path: str = "/tv/x.mkv") -> dict:
    return {
        "eventType": event,
        "series": {"id": 1, "title": "X", "tags": tags},
        "episodeFile": {"path": path},
    }


def test_radarr_without_translate_tag_ignored():
    r = client.post("/webhooks/radarr", json=_radarr_payload("Download", tags=[]))
    assert r.status_code == 200
    assert r.json() == {"status": "ignored", "reason": "no_translate_tag"}


def test_radarr_with_translate_tag_string_form_queued():
    r = client.post(
        "/webhooks/radarr", json=_radarr_payload("Download", tags=["radarr_translate"])
    )
    assert r.status_code == 200
    assert r.json()["status"] == "queued"


def test_radarr_with_translate_tag_dict_form_queued():
    r = client.post(
        "/webhooks/radarr",
        json=_radarr_payload("Download", tags=[{"id": 5, "label": "radarr_translate"}]),
    )
    assert r.status_code == 200
    assert r.json()["status"] == "queued"


def test_radarr_test_event_responds_ok():
    r = client.post("/webhooks/radarr", json={"eventType": "Test"})
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_sonarr_without_translate_tag_ignored():
    r = client.post("/webhooks/sonarr", json=_sonarr_payload("Download", tags=[]))
    assert r.status_code == 200
    assert r.json()["status"] == "ignored"


def test_sonarr_with_translate_tag_queued():
    r = client.post(
        "/webhooks/sonarr", json=_sonarr_payload("Download", tags=["sonarr_translate"])
    )
    assert r.status_code == 200
    assert r.json()["status"] == "queued"
