from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    llm_provider: Literal["anthropic", "openai", "ollama"] = "anthropic"
    llm_model: str = "claude-sonnet-4-6"

    anthropic_api_key: str | None = None
    openai_api_key: str | None = None
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

    # Library refresh hooks — fire after a translation completes so Emby/Jellyfin
    # pick up the new .srt without a full scan. All optional; leave blank to skip.
    emby_url: str | None = None              # e.g. http://AnsibleMedia:8096
    emby_api_key: str | None = None
    jellyfin_url: str | None = None          # e.g. http://jellyfin:8096
    jellyfin_api_key: str | None = None
    library_refresh_timeout_seconds: int = 10


settings = Settings()
