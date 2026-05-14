import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

import structlog
from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles

from server import __version__
from server.config import settings
from server.cost_tracker import CostCapExceeded
from server.models import HealthResponse, TranslateRequest, TranslateResponse
from server.queue.base import Job, JobState, compute_dedup_key
from server.queue.sqlite import get_queue
from server.queue.worker import start_workers
from server.subs.pipeline import AlreadyTranslated, translate_media
from server.webhooks import emby, jellyfin, radarr, sonarr

logging.basicConfig(level=settings.log_level)
log = structlog.get_logger()


@asynccontextmanager
async def lifespan(app: FastAPI):
    stop_event = asyncio.Event()
    worker_tasks = await start_workers(stop_event)
    log.info("lifespan_startup", workers=len(worker_tasks))
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


@app.post("/translate/sync", response_model=TranslateResponse)
async def translate_sync(req: TranslateRequest) -> TranslateResponse:
    """Back-compat sync endpoint. Same as v0.1 /translate behavior. Blocks until done.

    Prefer POST /translate + polling for new integrations.
    """
    try:
        return await translate_media(req)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except AlreadyTranslated as e:
        raise HTTPException(
            status_code=409,
            detail={"error": "already_translated", "output_path": str(e.path)},
        ) from e
    except CostCapExceeded as e:
        raise HTTPException(status_code=429, detail=str(e)) from e
    except TimeoutError as e:
        raise HTTPException(status_code=504, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


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
