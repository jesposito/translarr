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


def test_plex_multipart_form_payload_parsed():
    # Plex posts multipart/form-data with a JSON "payload" field.
    resp = client.post(
        "/webhooks/plex",
        files={"payload": (None, '{"event": "media.pause"}')},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "ignored"


def test_plex_bad_json_bodies_do_not_500():
    resp = client.post(
        "/webhooks/plex", content=b"not json", headers={"content-type": "application/json"}
    )
    assert resp.json()["status"] == "invalid_payload"
    resp = client.post("/webhooks/plex", json=["a list"])
    assert resp.status_code == 400


def test_emby_string_item_is_no_path():
    resp = client.post("/webhooks/emby", json={"Event": "library.new", "Item": "oops"})
    assert resp.status_code == 200
    assert resp.json()["status"] == "no_path"


def test_radarr_relative_path_joined_onto_folder(monkeypatch):
    from server.webhooks import radarr

    seen = []

    async def fake_enqueue(path):
        seen.append(path)
        return "job"

    monkeypatch.setattr(radarr, "enqueue", fake_enqueue)
    resp = client.post(
        "/webhooks/radarr",
        json={
            "eventType": "Download",
            "movie": {"tags": ["radarr_translate"], "folderPath": "/movies/Alien (1979)"},
            "movieFile": {"relativePath": "Alien.mkv"},
        },
    )
    assert resp.json()["status"] == "queued"
    assert str(seen[0]) == "/movies/Alien (1979)/Alien.mkv"


def test_done_job_blocks_only_while_output_exists(tmp_path):
    from server.queue.base import Job, JobState
    from server.webhooks.queue import blocks_new_job

    out = tmp_path / "x.en.translarr.srt"
    job = Job(id="1", dedup_key="k", media_path="x.mkv", target_lang="en",
              state=JobState.DONE, output_path=str(out))
    assert blocks_new_job(job) is False  # output deleted -> allow a new job
    out.write_text("sub")
    assert blocks_new_job(job) is True
    job.output_path = None  # no-subs skip stays deduped
    assert blocks_new_job(job) is True
