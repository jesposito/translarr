from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    llm_provider: Literal["anthropic", "openai", "ollama", "deepseek", "gemini"] = "anthropic"
    llm_model: str = "claude-sonnet-4-6"

    anthropic_api_key: str | None = None
    openai_api_key: str | None = None
    deepseek_api_key: str | None = None
    gemini_api_key: str | None = None
    ollama_host: str = "http://ollama:11434"

    media_root: Path = Path("/media")

    target_lang: str = "en"
    reading_rate_cps: int = 17
    max_concurrent: int = 2
    context_window_lines: int = 10

    webhook_secret: str | None = None

    log_level: str = "INFO"

    max_cost_cents_per_day: int = 1000
    max_cost_cents_per_job: int = 500
    job_timeout_seconds: int = 1800

    radarr_translate_tag: str = "radarr_translate"
    sonarr_translate_tag: str = "sonarr_translate"

    # Play-triggered translation. When TRUE, Translarr's /webhooks/emby endpoint
    # responds to Emby `playback.start` events by enqueueing a translation for
    # the now-playing item (subject to existing dedup + cost caps + the
    # NoSourceSubtitles short-circuit). DEFAULT FALSE — explicit opt-in only.
    # Rationale: playback events fire MUCH more often than library scans;
    # accidentally leaving this on with a wide-open `max_cost_cents_per_day`
    # could chew API budget. See TR-2yt.
    auto_translate_on_playback: bool = False

    # Push notifications. When NTFY_URL is set, Translarr POSTs a short
    # message to it whenever a translation job reaches a terminal state.
    # Works with ntfy.sh (self-hosted or public) and any HTTP endpoint
    # that accepts a JSON body of {"title":..., "message":...}.
    # Example: https://ntfy.sh/translarr-<random-token>
    ntfy_url: str | None = None
    # Granularity knobs — flip individually if "every event" is too noisy.
    ntfy_on_success: bool = True
    ntfy_on_failure: bool = True
    # Skipped jobs (already-translated, no-source-subtitles) are quiet by
    # default because they fire on every playback.start of an English-only
    # item when AUTO_TRANSLATE_ON_PLAYBACK is on.
    ntfy_on_skip: bool = False
    ntfy_timeout_seconds: int = 10

    # Library refresh hooks — fire after a translation completes so Emby/Jellyfin
    # pick up the new .srt without a full scan. All optional; leave blank to skip.
    emby_url: str | None = None              # e.g. http://emby:8096
    emby_api_key: str | None = None
    jellyfin_url: str | None = None          # e.g. http://jellyfin:8096
    jellyfin_api_key: str | None = None
    library_refresh_timeout_seconds: int = 10


settings = Settings()
