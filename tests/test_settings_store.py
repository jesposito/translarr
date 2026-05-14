"""Persistent settings overrides — DB layer + PATCH /config + DELETE /config.

The Settings page is auto-save; these tests cover the contract it
relies on: writes round-trip through the DB, validation rejects bad
input, immutable fields are gated, and DELETE reverts to the env-
baseline.
"""

from __future__ import annotations

import tempfile

import pytest
from fastapi.testclient import TestClient

from server import db as db_module
from server.config import settings
from server.queue import sqlite as sqlite_q
from server.settings_store import SettingValidationError, set_override


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


# === Store unit tests ======================================================


def test_set_override_persists_and_applies(client):
    """Writing a setting both stores it and mutates the live settings."""
    original = settings.target_lang
    try:
        set_override("target_lang", "es")
        assert settings.target_lang == "es"
        # Persisted: a fresh GET /config reflects the change with source=db.
        fields = {f["key"]: f for f in client.get("/config").json()["fields"]}
        assert fields["target_lang"]["value"] == "es"
        assert fields["target_lang"]["source"] == "db"
    finally:
        settings.target_lang = original


def test_set_override_validates_iso639(client):
    with pytest.raises(SettingValidationError, match="invalid_lang_code"):
        set_override("target_lang", "english")  # not a code


def test_set_override_validates_int_range(client):
    with pytest.raises(SettingValidationError, match="below minimum"):
        set_override("reading_rate_cps", 0)
    with pytest.raises(SettingValidationError, match="above maximum"):
        set_override("reading_rate_cps", 9999)


def test_set_override_coerces_string_input(client):
    """JSON bodies may arrive with stringified numbers from <input type=number>."""
    set_override("reading_rate_cps", "20")
    assert settings.reading_rate_cps == 20
    assert isinstance(settings.reading_rate_cps, int)


def test_set_override_rejects_immutable(client):
    with pytest.raises(SettingValidationError, match="setting_immutable"):
        set_override("llm_provider", "openai")


def test_set_override_rejects_unknown_key(client):
    with pytest.raises(SettingValidationError, match="unknown_setting"):
        set_override("hax", "ohno")


def test_set_override_url_validation(client):
    with pytest.raises(SettingValidationError, match="invalid_url"):
        set_override("ntfy_url", "not-a-url")
    # Empty clears the override; valid http(s) accepted.
    set_override("ntfy_url", "https://ntfy.sh/topic")
    assert settings.ntfy_url == "https://ntfy.sh/topic"


# === HTTP endpoint tests ===================================================


def test_patch_config_happy_path(client):
    r = client.patch(
        "/config", json={"key": "reading_rate_cps", "value": 22}
    )
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["field"]["key"] == "reading_rate_cps"
    assert body["field"]["value"] == 22
    assert body["field"]["source"] == "db"
    # Next GET /config reflects the override.
    fields = {f["key"]: f for f in client.get("/config").json()["fields"]}
    assert fields["reading_rate_cps"]["value"] == 22


def test_patch_config_validation_failure_returns_400(client):
    r = client.patch("/config", json={"key": "reading_rate_cps", "value": -1})
    assert r.status_code == 400
    assert "below minimum" in r.json()["detail"]


def test_patch_config_missing_key_400(client):
    r = client.patch("/config", json={"value": 5})
    assert r.status_code == 400
    assert r.json()["detail"] == "missing_key"


def test_patch_config_missing_value_400(client):
    r = client.patch("/config", json={"key": "target_lang"})
    assert r.status_code == 400
    assert r.json()["detail"] == "missing_value"


def test_patch_config_immutable_400(client):
    r = client.patch("/config", json={"key": "llm_provider", "value": "openai"})
    assert r.status_code == 400
    assert "immutable" in r.json()["detail"]


def test_patch_config_unknown_key_400(client):
    r = client.patch("/config", json={"key": "hax", "value": 1})
    assert r.status_code == 400


def test_delete_config_reverts_to_env(client):
    set_override("target_lang", "es")
    fields = {f["key"]: f for f in client.get("/config").json()["fields"]}
    assert fields["target_lang"]["source"] == "db"

    r = client.delete("/config/target_lang")
    assert r.status_code == 200
    fields = {f["key"]: f for f in client.get("/config").json()["fields"]}
    assert fields["target_lang"]["source"] == "env"


def test_patch_secret_field_does_not_leak_in_response(client):
    """PATCH-ing a secret accepts the write but the response still hides it."""
    r = client.patch("/config", json={"key": "webhook_secret", "value": "shh"})
    assert r.status_code == 200
    field = r.json()["field"]
    assert field["is_secret"] is True
    assert field["set"] is True
    assert "value" not in field
    # And the literal secret never appears in the response body text.
    assert "shh" not in r.text


def test_overrides_survive_restart_via_apply_overrides(client, monkeypatch):
    """After set_override, a 'restart' (close + reopen DB) still applies it."""
    set_override("target_lang", "de")
    assert settings.target_lang == "de"
    # Reset settings to baseline then re-apply.
    from server.config import Settings as _S

    baseline = _S()
    settings.target_lang = baseline.target_lang
    # Simulate restart: call apply_overrides_to_settings as the lifespan would.
    from server.settings_store import apply_overrides_to_settings

    n = apply_overrides_to_settings()
    assert n >= 1
    assert settings.target_lang == "de"
