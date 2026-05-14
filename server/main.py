import logging

import structlog
from fastapi import FastAPI, HTTPException

from server import __version__
from server.config import settings
from server.cost_tracker import CostCapExceeded
from server.models import HealthResponse, TranslateRequest, TranslateResponse
from server.subs.pipeline import AlreadyTranslated, translate_media
from server.webhooks import emby, jellyfin, radarr, sonarr

logging.basicConfig(level=settings.log_level)
log = structlog.get_logger()

app = FastAPI(
    title="Translarr",
    version=__version__,
    description="AI-powered subtitle translation for the arr stack.",
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


@app.post("/translate", response_model=TranslateResponse)
async def translate(req: TranslateRequest) -> TranslateResponse:
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
