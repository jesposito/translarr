from pathlib import Path

from pydantic import BaseModel, Field


class TranslateRequest(BaseModel):
    """Direct translation request. Path is relative to MEDIA_ROOT."""

    media_path: Path
    source_track_index: int | None = Field(
        default=None, description="ffmpeg stream index of the sub track. Auto-detected if omitted."
    )
    source_lang: str | None = Field(
        default=None, description="ISO 639-1 source language. Auto-detected from track metadata if omitted."
    )
    target_lang: str | None = Field(
        default=None, description="Override TARGET_LANG for this request."
    )
    glossary_id: str | None = Field(
        default=None, description="Glossary scope (series id, etc.)."
    )
    force: bool = Field(
        default=False,
        description="Bypass output-collision policy; back up existing output and re-translate.",
    )


class TranslateResponse(BaseModel):
    output_path: Path
    source_events: int
    output_events: int
    duration_seconds: float
    model: str
    provider: str
    cost_cents: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    source_lang: str = ""  # Detected or provided source language (ISO code)
    source_track_index: int | None = None  # Track index that was translated
    # Backwards-compat field — equals source_events for v0.1 consumers reading this name
    lines_translated: int = 0


class HealthResponse(BaseModel):
    status: str = "ok"
    version: str
    llm_provider: str
    llm_model: str


# subgen-compatible /asr request body (multipart-form; this is the JSON-extras schema)
class AsrParams(BaseModel):
    task: str = "transcribe"  # or "translate"
    language: str | None = None
    encode: bool = True
    output: str = "srt"


class SeriesConfigRequest(BaseModel):
    """Body for PUT /series/{id} — create or update a series config.

    All fields are optional so partial configs (e.g. just target_lang) are
    valid. The Web UI's per-series form sends every field every time, but
    API callers can patch individual fields by omitting the rest.
    """

    source_lang: str | None = Field(default=None, min_length=2, max_length=10)
    target_lang: str | None = Field(default=None, min_length=2, max_length=10)
    llm_provider: str | None = Field(default=None, min_length=1)
    llm_model: str | None = Field(default=None, min_length=1)
    path_prefix: str | None = Field(default=None, min_length=1)
    auto_translate: bool = False
