"""Tests for GET /jobs (list) and GET /stats (v0.6.5 Web UI dashboard)."""

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


# --- /jobs (list) -----------------------------------------------------------


def test_list_jobs_empty(client):
    r = client.get("/jobs")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 0
    assert body["jobs"] == []
    assert body["limit"] == 50
    assert body["offset"] == 0


def test_list_jobs_ordered_desc_by_created(client):
    # Enqueue 3 jobs; the most-recent should appear first.
    ids = []
    for i in range(3):
        r = client.post(
            "/translate",
            json={"media_path": f"/movies/x{i}.mkv", "target_lang": "en"},
        ).json()
        ids.append(r["job_id"])

    r = client.get("/jobs")
    body = r.json()
    assert body["total"] == 3
    assert len(body["jobs"]) == 3
    # Most-recent first -> reverse of insertion order.
    returned_ids = [j["id"] for j in body["jobs"]]
    assert returned_ids == list(reversed(ids))
    # Spot-check the shape.
    first = body["jobs"][0]
    for key in (
        "id",
        "state",
        "media_path",
        "target_lang",
        "output_path",
        "attempts",
        "cost_cents",
        "error",
        "created_at",
        "updated_at",
        "finished_at",
    ):
        assert key in first


def test_list_jobs_filter_by_state(client):
    # Enqueue 2 jobs, then transition one to running.
    a = client.post("/translate", json={"media_path": "/m/a.mkv", "target_lang": "en"}).json()
    b = client.post("/translate", json={"media_path": "/m/b.mkv", "target_lang": "en"}).json()

    from server.queue.base import JobState
    from server.queue.sqlite import get_queue

    get_queue().update_state(a["job_id"], JobState.RUNNING)

    r_queued = client.get("/jobs?state=queued").json()
    assert r_queued["total"] == 1
    assert r_queued["jobs"][0]["id"] == b["job_id"]

    r_running = client.get("/jobs?state=running").json()
    assert r_running["total"] == 1
    assert r_running["jobs"][0]["id"] == a["job_id"]


def test_list_jobs_invalid_state_returns_400(client):
    r = client.get("/jobs?state=not_a_state")
    assert r.status_code == 400


def test_list_jobs_pagination_keeps_total(client):
    for i in range(3):
        client.post("/translate", json={"media_path": f"/m/{i}.mkv", "target_lang": "en"})

    r = client.get("/jobs?limit=2&offset=1").json()
    assert r["total"] == 3
    assert r["limit"] == 2
    assert r["offset"] == 1
    assert len(r["jobs"]) == 2


def test_list_jobs_limit_bounded_at_200(client):
    # Asking for limit=500 should be rejected by FastAPI Query validation.
    r = client.get("/jobs?limit=500")
    assert r.status_code == 422


# --- /stats -----------------------------------------------------------------


def test_stats_empty_db_returns_zeros(client):
    r = client.get("/stats")
    assert r.status_code == 200
    body = r.json()
    assert body["today"]["cost_cents"] == 0
    assert body["today"]["jobs_count"] == 0
    assert body["today"]["jobs_done"] == 0
    assert body["today"]["jobs_failed"] == 0
    assert body["today"]["jobs_in_flight"] == 0
    assert body["all_time"]["cost_cents"] == 0
    assert body["all_time"]["jobs_count"] == 0
    assert body["queue"] == {"queued": 0, "running": 0, "retrying": 0}
    # Date present + ISO-formatted.
    assert "date" in body["today"]
    assert len(body["today"]["date"]) == 10
    # Budget block — shipped with cost-cap headroom polish slice.
    assert body["budget"]["spent_cents"] == 0
    assert body["budget"]["pct_used"] == 0.0
    assert body["budget"]["daily_cap_cents"] > 0
    assert body["budget"]["remaining_cents"] == body["budget"]["daily_cap_cents"]


def test_stats_after_cost_recorded(client):
    from server import cost_tracker

    cost_tracker.record(84)
    r = client.get("/stats").json()
    assert r["today"]["cost_cents"] == 84


def test_stats_queue_counts_match_enqueued(client):
    # Enqueue 3, transition one to running and one to retrying.
    j1 = client.post("/translate", json={"media_path": "/m/1.mkv", "target_lang": "en"}).json()
    j2 = client.post("/translate", json={"media_path": "/m/2.mkv", "target_lang": "en"}).json()
    client.post("/translate", json={"media_path": "/m/3.mkv", "target_lang": "en"}).json()

    from server.queue.base import JobState
    from server.queue.sqlite import get_queue

    get_queue().update_state(j1["job_id"], JobState.RUNNING)
    get_queue().update_state(j2["job_id"], JobState.RETRYING)

    r = client.get("/stats").json()
    assert r["queue"]["queued"] == 1
    assert r["queue"]["running"] == 1
    assert r["queue"]["retrying"] == 1
    assert r["today"]["jobs_count"] == 3
    assert r["today"]["jobs_in_flight"] == 3
    assert r["all_time"]["jobs_count"] == 3


# --- /config (sanitized server configuration) ------------------------------


def _fields_by_key(body: dict) -> dict[str, dict]:
    return {f["key"]: f for f in body["fields"]}


def test_config_returns_expected_keys(client):
    r = client.get("/config")
    assert r.status_code == 200
    body = r.json()
    assert "version" in body
    fields = _fields_by_key(body)
    expected = {
        "llm_provider", "llm_model", "target_lang", "reading_rate_cps",
        "max_concurrent", "context_window_lines", "max_cost_cents_per_day",
        "max_cost_cents_per_job", "job_timeout_seconds",
        "radarr_translate_tag", "sonarr_translate_tag", "webhook_secret",
        "auto_translate_on_playback", "emby_provider_mode",
        "ntfy_url", "ntfy_on_success", "ntfy_on_failure", "ntfy_on_skip",
        "emby_url", "emby_api_key", "jellyfin_url", "jellyfin_api_key",
    }
    assert set(fields.keys()) == expected
    # Each field carries the canonical metadata shape.
    sample = fields["reading_rate_cps"]
    for required in (
        "section", "type", "description", "hint", "mutable",
        "restart_required", "source", "default_label", "value",
    ):
        assert required in sample, f"missing {required} on reading_rate_cps"
    assert sample["type"] == "int"
    assert sample["mutable"] is True
    # All fields are now live-mutable (no restart-only fields remain).
    assert fields["llm_provider"]["mutable"] is True
    assert fields["llm_provider"]["restart_required"] is False
    assert fields["max_concurrent"]["mutable"] is True
    assert fields["max_concurrent"]["restart_required"] is False


def test_config_never_returns_secrets(client, monkeypatch):
    """Secrets emit a `set: bool` flag, never the value or any key
    matching a secret-y name."""
    monkeypatch.setattr("server.main.settings.webhook_secret", "super-secret-value")
    monkeypatch.setattr("server.main.settings.anthropic_api_key", "sk-ant-leak-me")
    monkeypatch.setattr("server.main.settings.openai_api_key", "sk-leak-me-too")
    r = client.get("/config")
    assert r.status_code == 200
    fields = _fields_by_key(r.json())
    # Webhook secret is in the registry as a secret — emits `set` not `value`.
    assert fields["webhook_secret"]["is_secret"] is True
    assert fields["webhook_secret"]["set"] is True
    assert "value" not in fields["webhook_secret"]
    # Defensive: literal secret strings never appear in the response.
    blob = r.text
    assert "super-secret-value" not in blob
    assert "sk-ant-leak-me" not in blob
    assert "sk-leak-me-too" not in blob


def test_config_webhook_secret_set_false_when_unset(client, monkeypatch):
    monkeypatch.setattr("server.main.settings.webhook_secret", None)
    fields = _fields_by_key(client.get("/config").json())
    assert fields["webhook_secret"]["set"] is False
