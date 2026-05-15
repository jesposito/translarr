import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

import structlog
from fastapi import FastAPI, HTTPException, Query, Response
from fastapi.staticfiles import StaticFiles

from server import __version__, settings_store
from server.config import settings
from server.cost_tracker import COST_TABLE_CENTS_PER_MTOK
from server.models import HealthResponse, TranslateRequest
from server.queue.base import Job, JobState, compute_dedup_key
from server.queue.sqlite import get_queue
from server.queue.worker import start_workers
from server.settings_store import PRESETS, REGISTRY, SettingValidationError
from server.subs.extract import _normalize_lang, extract_track, list_sub_tracks, pick_source_track
from server.subs.pipeline import (
    _load_subs_with_encoding_fallback,
    _output_path,
)
from server.webhooks import emby, jellyfin, radarr, sonarr

logging.basicConfig(level=settings.log_level)
log = structlog.get_logger()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Apply persistent DB overrides BEFORE workers start, so the live
    # settings object reflects user-edited values from the prior session.
    applied = settings_store.apply_overrides_to_settings()
    stop_event = asyncio.Event()
    worker_tasks = await start_workers(stop_event)
    log.info("lifespan_startup", workers=len(worker_tasks), settings_overrides=applied)
    try:
        yield
    finally:
        stop_event.set()
        for t in worker_tasks:
            t.cancel()
        await asyncio.gather(*worker_tasks, return_exceptions=True)
        log.info("lifespan_shutdown")


app = FastAPI(
    title="Translarr",
    version=__version__,
    description="AI-powered subtitle translation for the arr stack.",
    lifespan=lifespan,
)

app.include_router(radarr.router, prefix="/webhooks", tags=["webhooks"])
app.include_router(sonarr.router, prefix="/webhooks", tags=["webhooks"])
app.include_router(emby.router, prefix="/webhooks", tags=["webhooks"])
app.include_router(jellyfin.router, prefix="/webhooks", tags=["webhooks"])


@app.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    return HealthResponse(
        version=__version__,
        llm_provider=settings.llm_provider,
        llm_model=settings.llm_model,
    )


@app.post("/preflight")
async def preflight(req: TranslateRequest) -> dict:
    """Read-only cost estimate before committing to a translation.

    Runs ffprobe to list subtitle tracks, picks the best source,
    counts events, and returns a cost estimate for every known model.
    No LLM call, no queue — purely informational.
    """
    media = settings.media_root / req.media_path if not req.media_path.is_absolute() else req.media_path
    if not media.exists():
        raise HTTPException(status_code=404, detail=f"media not found: {req.media_path}")

    target_lang = req.target_lang or settings.target_lang

    # Check for existing translation.
    out_path = _output_path(media, target_lang)
    already_done = out_path.exists()

    # Detect subtitle tracks.
    try:
        tracks = await list_sub_tracks(media)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"ffprobe failed: {e}") from e

    text_tracks = [t for t in tracks if t.is_text]
    if not text_tracks:
        return {
            "media_path": str(req.media_path),
            "target_lang": target_lang,
            "tracks_found": len(tracks),
            "text_tracks": 0,
            "can_translate": False,
            "reason": "no text-based subtitle tracks (only bitmap formats like PGS/VOBSUB)",
            "already_done": already_done,
            "estimates": [],
        }

    # Pick the source track.
    try:
        source = pick_source_track(tracks, req.source_track_index, target_lang)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    source_lang = req.source_lang or source.language or "auto"
    source_lang_display = _normalize_lang(source.language) or source.language or "unknown"

    # Count events by extracting and parsing (fast — no LLM).
    import shutil
    import tempfile

    workdir = Path(tempfile.mkdtemp())
    try:
        ext = ".ass" if source.codec in {"ass", "ssa"} else ".srt"
        raw_path = workdir / f"source.{source.index}{ext}"
        await extract_track(media, source.index, raw_path)
        subs = _load_subs_with_encoding_fallback(raw_path)
        event_count = len([e for e in subs.events if e.text.strip()])
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"extraction failed: {e}") from e
    finally:
        shutil.rmtree(workdir, ignore_errors=True)

    # Estimate tokens and cost per model.
    # Rough: avg subtitle line is 40 chars ≈ 10 tokens input, 12 tokens output.
    est_tokens_in = event_count * 10 + 300  # 300 for system prompt per batch
    est_tokens_out = event_count * 12
    batches = (event_count + 29) // 30
    est_tokens_in += batches * 200  # context window overhead

    estimates = []
    for model, (in_rate, out_rate) in COST_TABLE_CENTS_PER_MTOK.items():
        cost = (est_tokens_in * in_rate + est_tokens_out * out_rate) // 1_000_000
        estimates.append({"model": model, "cost_cents": cost, "cost_display": f"${cost / 100:.2f}"})

    return {
        "media_path": str(req.media_path),
        "target_lang": target_lang,
        "source_track_index": source.index,
        "source_track_codec": source.codec,
        "source_lang": source_lang,
        "source_lang_display": source_lang_display,
        "event_count": event_count,
        "batches": batches,
        "can_translate": True,
        "already_done": already_done,
        "estimates": estimates,
    }


@app.post("/translate")
async def translate(req: TranslateRequest) -> dict:
    """v0.1.5: async — returns immediately with job_id; client polls GET /jobs/{id}.

    Dedup: in-flight or completed job with same (media_path, source_track_index, target_lang)
    returns {status:'dedup', job_id} without enqueueing.
    """
    target_lang = req.target_lang or settings.target_lang
    media_path_str = str(req.media_path)
    dedup_key = compute_dedup_key(media_path_str, req.source_track_index, target_lang)

    q = get_queue()
    existing = q.find_by_dedup(dedup_key)
    if (
        existing
        and existing.state in {JobState.QUEUED, JobState.RUNNING, JobState.RETRYING, JobState.DONE}
        and not req.force
    ):
        return {"status": "dedup", "job_id": existing.id, "state": existing.state.value}
    # Force re-translate or no existing: fall through to enqueue.

    job = Job(
        id="",
        dedup_key=dedup_key,
        media_path=media_path_str,
        target_lang=target_lang,
        state=JobState.QUEUED,
        source_track_index=req.source_track_index,
        source_lang=req.source_lang,
        force_flag=req.force,
        glossary_id=req.glossary_id,
    )
    persisted = q.enqueue(job)
    return {"status": "queued", "job_id": persisted.id}


@app.get("/jobs")
async def list_jobs(
    state: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> dict:
    """List jobs with optional state filter and pagination (v0.6.5 Web UI).

    Ordered by created_at DESC. `limit` is bounded server-side at 200.
    """
    state_filter: JobState | None = None
    if state is not None:
        try:
            state_filter = JobState(state)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=f"invalid_state: {state}") from e

    total, jobs = get_queue().list_jobs(state_filter, limit, offset)
    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "jobs": [
            {
                "id": j.id,
                "state": j.state.value,
                "media_path": j.media_path,
                "target_lang": j.target_lang,
                "output_path": j.output_path,
                "attempts": j.attempts,
                "cost_cents": j.cost_cents,
                "error": j.error,
                "created_at": j.created_at,
                "updated_at": j.updated_at,
                "finished_at": j.finished_at,
            }
            for j in jobs
        ],
    }


@app.get("/stats")
async def get_stats() -> dict:
    """Aggregate stats for the v0.6.5 Web UI dashboard.

    Returns {today, all_time, queue} — all single-query reads, no N+1.
    """
    return get_queue().aggregate_stats()


def _serialize_field(key: str) -> dict:
    """Per-field payload shape for GET /config and PATCH /config responses.

    Secrets never leak their value — only a ``set`` boolean. Everything
    else returns the effective live value plus its source (``db`` if
    overridden, ``env`` otherwise) and metadata the Settings UI needs to
    render a sane editor (type, min/max, choices, description, hint,
    mutability, restart_required, default_label).
    """
    meta = REGISTRY[key]
    value, source = settings_store.get_effective_with_source(key)
    field: dict = {
        "key": key,
        "section": meta.section,
        "type": meta.type,
        "description": meta.description,
        "hint": meta.hint,
        "default_label": meta.default_label,
        "mutable": meta.mutable,
        "restart_required": meta.restart_required,
        "source": source,
        "updated_at": settings_store.get_override_timestamp(key),
        "min": meta.min,
        "max": meta.max,
        "choices": meta.choices,
        "is_secret": meta.is_secret,
    }
    if meta.is_secret:
        field["set"] = bool(value)
        # Never leak the actual secret.
    else:
        field["value"] = value
    return field


@app.get("/config")
async def get_config() -> dict:
    """Settings page payload — every registered key plus its metadata.

    Secret fields (API keys, webhook secret) emit only ``set: bool``,
    never the underlying value. Edit a field via PATCH /config; revert
    to the env-baseline via DELETE /config/{key}.
    """
    fields = [_serialize_field(k) for k in REGISTRY]
    return {"version": __version__, "fields": fields}


@app.get("/config/presets")
async def get_presets() -> dict:
    """Available translation presets — quick-start configurations."""
    return {
        "presets": [
            {"id": k, "label": v["label"], "description": v["description"]}
            for k, v in PRESETS.items()
        ]
    }


@app.post("/config/preset")
async def apply_preset(body: dict) -> dict:
    """Apply a named preset (quick_cheap, balanced, best_quality, local_free).

    Returns the freshly serialized fields for every key the preset touched.
    """
    name = body.get("preset")
    if not name:
        raise HTTPException(status_code=400, detail="missing preset name")
    try:
        from server.settings_store import apply_preset as _apply
        result = _apply(name)
    except SettingValidationError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    # Return fresh field data for every applied key.
    fields = {k: _serialize_field(k) for k in result["applied"]}
    return {"status": "ok", "preset": result, "fields": fields}


@app.patch("/config")
async def patch_config(body: dict) -> dict:
    """Apply a single setting override.

    Body: ``{"key": "<name>", "value": <typed>}``. Returns the freshly
    serialized field on success so the client can echo the
    server-coerced value (e.g. trimmed string, parsed int) back into the
    form.
    """
    key = body.get("key")
    if not isinstance(key, str) or not key:
        raise HTTPException(status_code=400, detail="missing_key")
    if "value" not in body:
        raise HTTPException(status_code=400, detail="missing_value")
    try:
        settings_store.set_override(key, body["value"])
    except SettingValidationError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return {"status": "ok", "field": _serialize_field(key)}


@app.delete("/config/{key}")
async def delete_config_override(key: str) -> dict:
    """Drop a DB override; revert to the env-baseline value for ``key``."""
    try:
        settings_store.clear_override(key)
    except SettingValidationError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return {"status": "ok", "field": _serialize_field(key)}


@app.get("/jobs/{job_id}")
async def get_job(job_id: str) -> dict:
    job = get_queue().get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="job_not_found")
    return {
        "id": job.id,
        "state": job.state.value,
        "media_path": job.media_path,
        "target_lang": job.target_lang,
        "source_lang": job.source_lang,
        "source_track_index": job.source_track_index,
        "output_path": job.output_path,
        "attempts": job.attempts,
        "max_attempts": job.max_attempts,
        "cost_cents": job.cost_cents,
        "tokens_in": job.tokens_in,
        "tokens_out": job.tokens_out,
        "error": job.error,
        "created_at": job.created_at,
        "updated_at": job.updated_at,
        "finished_at": job.finished_at,
    }


@app.delete("/jobs/{job_id}")
async def cancel_job(job_id: str) -> dict:
    q = get_queue()
    job = q.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="job_not_found")
    if job.state in {JobState.DONE, JobState.FAILED, JobState.CANCELLED}:
        return {"status": "already_terminal", "state": job.state.value}
    q.update_state(job_id, JobState.CANCELLED)
    return {"status": "cancelled"}


@app.post("/translate/sync")
async def translate_sync(req: TranslateRequest) -> dict:
    """Enqueue + short poll: returns quickly in all cases.

    - If the subtitle already exists on disk → returns ``{output_path}``
      immediately (sub-second).
    - If the translation can finish within ``SYNC_POLL_TIMEOUT`` (90 s) →
      waits and returns ``{output_path}`` when done.
    - If the translation takes longer → returns 202 with ``{job_id}`` so
      the client knows to poll or check back later.  Old Emby plugin DLLs
      will surface this as an error ("translation in progress") instead of
      spinning forever.

    All translations run through the queue (visible in the Web UI dashboard).
    """
    target_lang = req.target_lang or settings.target_lang
    media_path_str = str(req.media_path)
    dedup_key = compute_dedup_key(media_path_str, req.source_track_index, target_lang)

    log.info(
        "translate_sync_request",
        media_path=media_path_str,
        source_track_index=req.source_track_index,
        source_lang=req.source_lang,
        target_lang=target_lang,
        force=req.force,
    )

    # --- Fast path: output already on disk ----------------------------------
    media = settings.media_root / req.media_path if not req.media_path.is_absolute() else req.media_path
    if not media.exists():
        raise HTTPException(status_code=404, detail=f"media not found: {req.media_path}")

    out_path = _output_path(media, target_lang)
    if out_path.exists() and not req.force:
        log.info("translate_sync_already_done", output=str(out_path))
        return {
            "output_path": str(out_path),
            "source_events": 0,
            "output_events": 0,
            "cost_cents": 0,
            "tokens_in": 0,
            "tokens_out": 0,
        }

    # --- Enqueue (reuse dedup if any) --------------------------------------
    q = get_queue()
    existing = q.find_by_dedup(dedup_key)

    if existing and existing.state == JobState.DONE and not req.force:
        log.info("translate_sync_dedup_done", job_id=existing.id)
        return {
            "output_path": existing.output_path or "",
            "source_events": 0,
            "output_events": 0,
            "cost_cents": existing.cost_cents,
            "tokens_in": existing.tokens_in,
            "tokens_out": existing.tokens_out,
        }

    if (
        existing
        and existing.state in {JobState.QUEUED, JobState.RUNNING, JobState.RETRYING}
        and not req.force
    ):
        job_id = existing.id
        log.info("translate_sync_dedup_wait", job_id=job_id, state=existing.state.value)
    else:
        job = Job(
            id="",
            dedup_key=dedup_key,
            media_path=media_path_str,
            target_lang=target_lang,
            state=JobState.QUEUED,
            source_track_index=req.source_track_index,
            source_lang=req.source_lang,
            force_flag=req.force,
            glossary_id=req.glossary_id,
        )
        persisted = q.enqueue(job)
        job_id = persisted.id
        log.info("translate_sync_enqueued", job_id=job_id)

    # --- Short poll: wait up to 90 s for the worker to finish ---------------
    SYNC_POLL_TIMEOUT = 90  # seconds — covers most TV episodes.
    poll_interval = 0.5
    deadline = asyncio.get_event_loop().time() + SYNC_POLL_TIMEOUT

    while asyncio.get_event_loop().time() < deadline:
        job = q.get(job_id)
        if job is None:
            raise HTTPException(status_code=500, detail="job_lost")
        if job.state == JobState.DONE:
            log.info("translate_sync_done", job_id=job_id, output=job.output_path)
            return {
                "output_path": job.output_path or "",
                "source_events": 0,
                "output_events": 0,
                "cost_cents": job.cost_cents,
                "tokens_in": job.tokens_in,
                "tokens_out": job.tokens_out,
            }
        if job.state == JobState.FAILED:
            raise HTTPException(status_code=500, detail=job.error or "translation_failed")
        if job.state == JobState.CANCELLED:
            raise HTTPException(status_code=499, detail="job_cancelled")
        await asyncio.sleep(poll_interval)

    # Still running — return 202 so client knows it's in progress.
    # Old Emby DLLs will show this as an error; updated plugin polls.
    log.info("translate_sync_still_running", job_id=job_id)
    raise HTTPException(
        status_code=202,
        detail=f"translation_in_progress: job_id={job_id}",
    )


@app.post("/test/ntfy")
async def test_ntfy() -> dict:
    """Fire a sample push notification to verify the user's NTFY_URL works.

    Used by the Settings page "Send test notification" button so the user
    can confirm their topic/server config without queueing a real
    translation. Reports the result synchronously so the UI can show
    success or the underlying HTTP failure inline.
    """
    if not settings.ntfy_url:
        raise HTTPException(status_code=400, detail="ntfy_not_configured")
    import httpx
    try:
        async with httpx.AsyncClient(timeout=settings.ntfy_timeout_seconds) as client:
            resp = await client.post(
                settings.ntfy_url,
                content=b"This is a test push from your Translarr server. Wire is good.",
                headers={
                    "Title": "Translarr: test notification",
                    "Priority": "3",
                    "Tags": "test_tube",
                },
            )
        if resp.status_code >= 400:
            raise HTTPException(
                status_code=502,
                detail=f"ntfy returned HTTP {resp.status_code}: {resp.text[:200]}",
            )
        return {"status": "ok", "ntfy_response_status": resp.status_code}
    except httpx.RequestError as e:
        raise HTTPException(status_code=502, detail=f"ntfy request failed: {e}") from e


@app.get("/output")
async def get_output(path: str) -> Response:
    """Return the raw bytes of a translated subtitle file.

    Path must resolve inside MEDIA_ROOT (anti-traversal guard). Used by
    the Emby plugin's ISubtitleProvider implementation to fetch the .srt
    after /translate/sync completes — the plugin and server run in
    different containers, so we can't share files via the filesystem.
    """
    p = Path(path)
    if not p.is_absolute():
        p = settings.media_root / p
    p = p.resolve()
    media_root_resolved = settings.media_root.resolve()
    try:
        p.relative_to(media_root_resolved)
    except ValueError as e:
        raise HTTPException(status_code=403, detail="path outside MEDIA_ROOT") from e
    if not p.exists() or not p.is_file():
        raise HTTPException(status_code=404, detail="output not found")
    if p.suffix.lower() not in (".srt", ".ass", ".ssa", ".vtt"):
        raise HTTPException(status_code=415, detail="unsupported output extension")
    return Response(content=p.read_bytes(), media_type="text/plain; charset=utf-8")


# --- Static UI mount (v0.6.5) ---------------------------------------------
# Serves the prerendered SvelteKit bundle at GET /. MUST be mounted LAST so it
# does not shadow API routes registered above (/health, /translate, /jobs/*,
# /webhooks/*). Guarded: if ui/dist/ does not exist (dev environment without
# `npm run build`), the server still starts cleanly and GET / simply 404s.
_UI_DIST = Path(__file__).resolve().parent.parent / "ui" / "dist"
if _UI_DIST.is_dir():
    app.mount("/", StaticFiles(directory=str(_UI_DIST), html=True), name="ui")
    log.info("ui_mounted", path=str(_UI_DIST))
else:
    log.info("ui_not_built", expected_path=str(_UI_DIST))
