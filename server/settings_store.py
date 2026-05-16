"""Runtime-mutable settings store.

Lives in a SQLite ``app_settings`` table (key/value, JSON-encoded). At
process startup we load every row and apply it to ``server.config.settings``
via setattr, so every existing ``settings.foo`` read keeps working without
audit. PATCH /config writes a new row + re-applies, so users can flip
``auto_translate_on_playback`` or bump the daily cost cap without
recreating the container.

What lives here vs ``server.config``:

- ``server.config.Settings`` is the **schema** + **env-baseline**. Defaults,
  type hints, env loading via pydantic-settings. Loaded once at import.
- ``settings_store`` is the **runtime override layer**. Rows here win over
  env values. Empty store == pure env behavior.

Each key has a metadata entry in :data:`REGISTRY` describing its type,
validation rules, whether it can mutate at runtime, and a human-facing
help string consumed by the Settings page. Keys not in the registry are
rejected by PATCH /config (defense against typos or accidental exposure
of private fields).
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal

import structlog

from server.config import settings
from server.db import get_conn

log = structlog.get_logger()


SettingType = Literal["str", "int", "bool", "float", "url", "secret"]


@dataclass
class SettingMeta:
    """Per-key metadata. ``mutable=False`` means the value can be displayed
    but writing it via PATCH /config is rejected (typically because the
    code reads it once at startup, e.g. ``llm_provider``)."""

    key: str
    type: SettingType
    description: str
    section: str            # UI grouping: "llm" | "translation" | "cost" | "playback" | "ntfy" | "arr"
    mutable: bool = True
    restart_required: bool = False
    default_label: str = ""  # e.g. "$10.00" so UI can show "default $10.00"
    min: int | float | None = None
    max: int | float | None = None
    choices: list[str] | None = None
    pattern: str | None = None
    # Optional async-safe validator. Raise ValueError on bad input.
    validate: Callable[[Any], None] | None = None
    # Secret values are never returned in /config — only a boolean "set?".
    is_secret: bool = False
    # Optional help link / sub-hint shown in UI.
    hint: str = ""


def _validate_iso639(v: Any) -> None:
    s = str(v).strip().lower()
    if not (2 <= len(s) <= 3 and s.isalpha()):
        raise ValueError(f"invalid_lang_code: {v!r}; want 2-3 lowercase letters (e.g. en, eng)")


def _validate_http_url(v: Any) -> None:
    s = str(v).strip()
    if not s:
        return  # empty string clears the override; allowed
    if not (s.startswith("http://") or s.startswith("https://")):
        raise ValueError(f"invalid_url: {v!r}; must start with http:// or https://")


# --- Per-key registry -----------------------------------------------------
# The order here is also the order the UI presents fields within a section.
# Adding a new tunable: append here, add the field to server.config.Settings,
# done — UI is data-driven.

REGISTRY: dict[str, SettingMeta] = {
    # === LLM provider ===
    "llm_provider": SettingMeta(
        key="llm_provider",
        type="str",
        section="llm",
        description="Which LLM service to use for translation.",
        choices=["anthropic", "openai", "ollama", "deepseek", "gemini"],
        mutable=True,
        hint="Takes effect on the next job. Anthropic gives the best translation quality; Ollama is free + local; DeepSeek is cheap with good quality; Gemini Flash is fast and cheap.",
    ),
    "llm_model": SettingMeta(
        key="llm_model",
        type="str",
        section="llm",
        description="Specific model identifier from the chosen provider.",
        mutable=True,
        hint="Takes effect on the next job. e.g. claude-sonnet-4-6, gpt-4o-mini, qwen3:14b.",
    ),
    # === Translation defaults ===
    "target_lang": SettingMeta(
        key="target_lang",
        type="str",
        section="translation",
        description="Default target language for new translations when none specified.",
        default_label="en",
        validate=_validate_iso639,
        hint="ISO 639-1 (en) or 639-2 (eng) code.",
    ),
    "reading_rate_cps": SettingMeta(
        key="reading_rate_cps",
        type="int",
        section="translation",
        description="Maximum characters-per-second for an on-screen subtitle line. Lines that exceed this get auto-split to keep them readable.",
        min=5, max=50,
        default_label="17 (English industry standard)",
        hint="Higher = faster scrolling; lower = easier to read. 17 is the BBC/Netflix standard.",
    ),
    "context_window_lines": SettingMeta(
        key="context_window_lines",
        type="int",
        section="translation",
        description="Prior translated lines fed to the LLM as context for tone consistency.",
        min=0, max=50,
        default_label="10",
        hint="Higher = better continuity, more tokens spent per line. 10 is a good balance.",
    ),
    # === Cost guards ===
    "max_cost_cents_per_day": SettingMeta(
        key="max_cost_cents_per_day",
        type="int",
        section="cost",
        description="Hard daily cap (in cents) on total LLM spend. Jobs that would push the day's total over this stop with HTTP 429.",
        min=0, max=100_000,
        default_label="$10.00",
        hint="Resets at UTC midnight. 0 disables the cap.",
    ),
    "max_cost_cents_per_job": SettingMeta(
        key="max_cost_cents_per_job",
        type="int",
        section="cost",
        description="Hard per-job cap (in cents). A single translation that goes over this is killed mid-batch.",
        min=0, max=10_000,
        default_label="$5.00",
        hint="Catches runaway translations. A typical 25-min episode costs ~$0.30.",
    ),
    "job_timeout_seconds": SettingMeta(
        key="job_timeout_seconds",
        type="int",
        section="cost",
        description="Maximum wall-clock seconds a translation job can run before the worker kills it.",
        min=60, max=7200,
        default_label="1800 (30 min)",
        hint="Long episodes can legitimately take a few minutes; 30 min is a generous safety net.",
    ),
    "max_concurrent": SettingMeta(
        key="max_concurrent",
        type="int",
        section="cost",
        description="Number of translation jobs to run in parallel.",
        min=1, max=10,
        default_label="2",
        mutable=True,
        hint="Takes effect immediately. Extra workers exit after their current job; new ones spawn on demand.",
    ),
    # === On-demand translation ===
    "auto_translate_on_playback": SettingMeta(
        key="auto_translate_on_playback",
        type="bool",
        section="playback",
        description="When ON, pressing Play on a foreign-subs item fires an immediate translation. The new subtitle track appears in the player ~1-2 minutes into playback.",
        default_label="off",
        hint="Off by default because playback events fire much more often than library scans.",
    ),
    "emby_provider_mode": SettingMeta(
        key="emby_provider_mode",
        type="str",
        section="playback",
        description="How /translate/sync responds to the Emby subtitle-provider plugin. 'smart' (default) ffprobes first and returns 404 when there is nothing to translate, avoiding noisy $0 job rows from Emby's bulk scan. 'off' returns 404 for every request. 'always' enqueues every request (original v0.1 behavior).",
        choices=["off", "smart", "always"],
        mutable=True,
        hint="If Emby's 'Download missing subtitles' scheduled task is flooding the dashboard with $0 jobs, leave on 'smart' (default).",
    ),
    # === Push notifications ===
    "ntfy_url": SettingMeta(
        key="ntfy_url",
        type="url",
        section="ntfy",
        description="ntfy.sh-compatible endpoint. Translarr POSTs a short push notification here after every terminal job state.",
        validate=_validate_http_url,
        hint="Use https://ntfy.sh/<random-topic> or a self-hosted endpoint. Empty to disable all push.",
    ),
    "ntfy_on_success": SettingMeta(
        key="ntfy_on_success",
        type="bool",
        section="ntfy",
        description="Send a push when a translation completes successfully.",
        default_label="on",
    ),
    "ntfy_on_failure": SettingMeta(
        key="ntfy_on_failure",
        type="bool",
        section="ntfy",
        description="Send a push when a translation fails after exhausting retries.",
        default_label="on",
    ),
    "ntfy_on_skip": SettingMeta(
        key="ntfy_on_skip",
        type="bool",
        section="ntfy",
        description="Send a push when a translation is skipped (already translated / no source subs / file not found).",
        default_label="off",
        hint="Chatty under on-demand mode; off by default.",
    ),
    # === arr-stack integration ===
    "radarr_translate_tag": SettingMeta(
        key="radarr_translate_tag",
        type="str",
        section="arr",
        description="Radarr tag that opts a movie into auto-translation. Imports from a Radarr OnDownload webhook are only enqueued if the movie carries this tag.",
        default_label="radarr_translate",
        hint="Apply the tag in Radarr's UI to mark a movie for translation.",
    ),
    "sonarr_translate_tag": SettingMeta(
        key="sonarr_translate_tag",
        type="str",
        section="arr",
        description="Sonarr tag that opts a series into auto-translation.",
        default_label="sonarr_translate",
        hint="Apply the tag at the series level; every new episode inherits it.",
    ),
    "webhook_secret": SettingMeta(
        key="webhook_secret",
        type="secret",
        section="arr",
        description="Shared secret required on the X-Translarr-Secret header for /webhooks/* and Emby /webhooks/emby.",
        is_secret=True,
        hint="Leave empty to disable secret enforcement (LAN-only deployments). Strongly recommended on public hosts.",
    ),
    "emby_url": SettingMeta(
        key="emby_url",
        type="url",
        section="arr",
        description="Emby server URL for library refresh hooks after a translation completes.",
        validate=_validate_http_url,
        hint="e.g. http://emby:8096",
    ),
    "emby_api_key": SettingMeta(
        key="emby_api_key",
        type="secret",
        section="arr",
        description="Emby API key used to trigger the post-translation library refresh.",
        is_secret=True,
    ),
    "jellyfin_url": SettingMeta(
        key="jellyfin_url",
        type="url",
        section="arr",
        description="Jellyfin server URL for library refresh hooks.",
        validate=_validate_http_url,
    ),
    "jellyfin_api_key": SettingMeta(
        key="jellyfin_api_key",
        type="secret",
        section="arr",
        description="Jellyfin API token (X-Emby-Token header).",
        is_secret=True,
    ),
}


# --- Presets ---------------------------------------------------------------

PRESETS: dict[str, dict[str, Any]] = {
    "quick_cheap": {
        "label": "Quick & Cheap",
        "description": "Fast translations at the lowest cost. Good enough for most content.",
        "fields": {
            "llm_model": "claude-haiku-4-5-20251001",
            "reading_rate_cps": 17,
            "context_window_lines": 5,
            "max_concurrent": 1,
        },
    },
    "balanced": {
        "label": "Balanced",
        "description": "Good quality at a reasonable price. Recommended for most users.",
        "fields": {
            "llm_model": "claude-sonnet-4-6",
            "reading_rate_cps": 17,
            "context_window_lines": 10,
            "max_concurrent": 2,
        },
    },
    "best_quality": {
        "label": "Best Quality",
        "description": "Maximum consistency and quality. Slower and more expensive.",
        "fields": {
            "llm_model": "claude-sonnet-4-6",
            "reading_rate_cps": 15,
            "context_window_lines": 20,
            "max_concurrent": 2,
        },
    },
    "local_free": {
        "label": "Local & Free",
        "description": "Uses Ollama on your hardware. Zero API cost. Quality depends on your model.",
        "fields": {
            "llm_provider": "ollama",
            "llm_model": "qwen3:14b",
            "reading_rate_cps": 17,
            "context_window_lines": 10,
            "max_concurrent": 1,
        },
    },
    "deepseek_budget": {
        "label": "DeepSeek Budget",
        "description": "DeepSeek V3 — excellent quality at a fraction of the cost. ~$0.10 per episode.",
        "fields": {
            "llm_provider": "deepseek",
            "llm_model": "deepseek-chat",
            "reading_rate_cps": 17,
            "context_window_lines": 10,
            "max_concurrent": 2,
        },
    },
    "gemini_flash": {
        "label": "Gemini Flash",
        "description": "Google Gemini 2.5 Flash — fast and cheap with good translation quality. ~$0.08 per episode.",
        "fields": {
            "llm_provider": "gemini",
            "llm_model": "gemini-2.5-flash",
            "reading_rate_cps": 17,
            "context_window_lines": 10,
            "max_concurrent": 2,
        },
    },
}


def apply_preset(preset_name: str) -> dict[str, Any]:
    """Apply a named preset: writes overrides for every field in the preset.

    Returns the preset metadata (label, description, applied fields).
    Raises SettingValidationError if the preset name is unknown.
    """
    preset = PRESETS.get(preset_name)
    if preset is None:
        raise SettingValidationError(f"unknown_preset: {preset_name!r}")
    applied = {}
    for key, value in preset["fields"].items():
        try:
            set_override(key, value)
            applied[key] = value
        except SettingValidationError:
            pass  # Skip immutable fields (e.g. llm_provider if it requires restart)
    return {"preset": preset_name, "label": preset["label"], "applied": applied}


# --- Storage ops ----------------------------------------------------------

def _row_to_value(row_value: str, target_type: SettingType) -> Any:
    """Decode a stored JSON value back to the appropriate Python type."""
    decoded = json.loads(row_value)
    if target_type == "int":
        return int(decoded)
    if target_type == "float":
        return float(decoded)
    if target_type == "bool":
        return bool(decoded)
    return decoded  # str/url/secret


def get_overrides() -> dict[str, Any]:
    """Return all current DB overrides keyed by setting name."""
    conn = get_conn()
    rows = conn.execute("SELECT key, value FROM app_settings").fetchall()
    out: dict[str, Any] = {}
    for r in rows:
        meta = REGISTRY.get(r["key"])
        if not meta:
            # Skip orphaned keys (likely from a removed setting).
            continue
        try:
            out[r["key"]] = _row_to_value(r["value"], meta.type)
        except Exception as e:
            log.warning("settings_store_decode_failed", key=r["key"], error=str(e))
    return out


def apply_overrides_to_settings() -> int:
    """Read overrides from DB and setattr() them onto ``settings``.

    Called at startup (lifespan) and after every PATCH /config so the
    rest of the codebase keeps reading ``settings.X`` without caring
    that it's been overridden.

    Returns the number of overrides applied.
    """
    overrides = get_overrides()
    for k, v in overrides.items():
        try:
            setattr(settings, k, v)
        except Exception as e:
            log.warning("settings_store_apply_failed", key=k, error=str(e))
    if overrides:
        log.info("settings_store_applied", count=len(overrides), keys=list(overrides))
    return len(overrides)


class SettingValidationError(ValueError):
    """Raised by ``set_override`` when input fails registry validation."""


def _coerce_and_validate(meta: SettingMeta, raw: Any) -> Any:
    """Convert a JSON-y input to the target type, then run the validator.

    Inputs come from JSON request bodies, so an int may arrive as a
    string when the user types into a number input. Coerce defensively
    before the type-specific validator runs.
    """
    if meta.type == "int":
        try:
            v: Any = int(raw)
        except (TypeError, ValueError) as e:
            raise SettingValidationError(f"{meta.key}: expected int, got {raw!r}") from e
        if meta.min is not None and v < meta.min:
            raise SettingValidationError(
                f"{meta.key}: {v} below minimum {meta.min}"
            )
        if meta.max is not None and v > meta.max:
            raise SettingValidationError(
                f"{meta.key}: {v} above maximum {meta.max}"
            )
    elif meta.type == "float":
        try:
            v = float(raw)
        except (TypeError, ValueError) as e:
            raise SettingValidationError(f"{meta.key}: expected float, got {raw!r}") from e
    elif meta.type == "bool":
        if isinstance(raw, bool):
            v = raw
        elif isinstance(raw, str):
            low = raw.strip().lower()
            if low in {"true", "1", "yes", "on"}:
                v = True
            elif low in {"false", "0", "no", "off", ""}:
                v = False
            else:
                raise SettingValidationError(f"{meta.key}: not a boolean: {raw!r}")
        else:
            raise SettingValidationError(f"{meta.key}: not a boolean: {raw!r}")
    else:  # str / url / secret
        v = "" if raw is None else str(raw).strip()
        if meta.choices and v not in meta.choices:
            raise SettingValidationError(
                f"{meta.key}: {v!r} not in {meta.choices}"
            )

    if meta.validate is not None:
        try:
            meta.validate(v)
        except ValueError as e:
            raise SettingValidationError(str(e)) from e
    return v


def set_override(key: str, raw_value: Any) -> Any:
    """Validate + persist an override; return the coerced value.

    Raises :class:`SettingValidationError` for an unknown key, an
    immutable key, or a value that fails validation.
    """
    meta = REGISTRY.get(key)
    if meta is None:
        raise SettingValidationError(f"unknown_setting: {key!r}")
    if not meta.mutable:
        raise SettingValidationError(
            f"setting_immutable: {key} requires a container restart to change "
            "(see RESTART-only section in the Settings page)."
        )
    value = _coerce_and_validate(meta, raw_value)

    conn = get_conn()
    conn.execute(
        """
        INSERT INTO app_settings (key, value, updated_at)
        VALUES (?, ?, ?)
        ON CONFLICT(key) DO UPDATE SET
            value = excluded.value,
            updated_at = excluded.updated_at
        """,
        (key, json.dumps(value), int(time.time())),
    )

    # Apply immediately to the live ``settings`` object so the next
    # request sees the change without restart.
    try:
        setattr(settings, key, value)
    except Exception as e:
        log.warning("set_override_apply_failed", key=key, error=str(e))

    # Side-effect hooks: some settings need immediate action beyond setattr.
    if key == "max_concurrent":
        from server.queue.worker import adjust_pool
        adjust_pool()

    log.info("setting_override_set", key=key, has_value=value not in (None, "", False))
    return value


def clear_override(key: str) -> None:
    """Delete the override row; revert to the env-baseline.

    Re-fetches the env-baseline from a fresh ``Settings()`` so the live
    object snaps back. Useful for "reset to default" affordances in UI.
    """
    meta = REGISTRY.get(key)
    if meta is None:
        raise SettingValidationError(f"unknown_setting: {key!r}")
    conn = get_conn()
    conn.execute("DELETE FROM app_settings WHERE key = ?", (key,))

    # Pull a fresh copy of the field default from a re-instantiated
    # Settings so any env-baseline still applies (the original .env value
    # is still authoritative if no override exists).
    from server.config import Settings as _S
    baseline = _S()
    try:
        setattr(settings, key, getattr(baseline, key))
    except Exception as e:
        log.warning("clear_override_apply_failed", key=key, error=str(e))
    log.info("setting_override_cleared", key=key)


def get_effective_with_source(key: str) -> tuple[Any, Literal["db", "env"]]:
    """Return ``(value, source)`` for one key — the value the rest of
    the app currently sees plus whether it came from the DB store or the
    env-baseline. Used by GET /config to drive UI "Saved · 2s ago" labels."""
    conn = get_conn()
    row = conn.execute(
        "SELECT value FROM app_settings WHERE key = ?", (key,)
    ).fetchone()
    if row is not None:
        meta = REGISTRY.get(key)
        if meta is not None:
            try:
                return _row_to_value(row["value"], meta.type), "db"
            except Exception:
                pass
    return getattr(settings, key, None), "env"


def reset_settings_for_tests() -> None:
    """Test-only: reset every Settings field back to the env-baseline.

    Pytest reuses the same module-level ``settings`` instance across
    tests in a session. Without this hook, a test that calls
    :func:`set_override` leaks the override into every subsequent test
    in the same pytest run (and the new tmp DB has no row to undo it).
    The fixture in :file:`tests/conftest.py`-style autouse blocks calls
    this in teardown.
    """
    from server.config import Settings as _S

    baseline = _S()
    import contextlib
    for k in REGISTRY:
        with contextlib.suppress(Exception):
            setattr(settings, k, getattr(baseline, k))


def get_override_timestamp(key: str) -> int | None:
    """Return the updated_at epoch seconds for an override, or None."""
    conn = get_conn()
    row = conn.execute(
        "SELECT updated_at FROM app_settings WHERE key = ?", (key,)
    ).fetchone()
    return int(row["updated_at"]) if row else None
