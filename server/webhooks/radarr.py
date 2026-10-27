from pathlib import Path
from typing import Any

import structlog
from fastapi import APIRouter, Depends

from server.config import settings
from server.webhooks.common import has_translate_tag
from server.webhooks.queue import enqueue
from server.webhooks.security import require_secret

router = APIRouter()
log = structlog.get_logger()


@router.post("/radarr", dependencies=[Depends(require_secret)])
async def radarr(payload: dict[str, Any]) -> dict[str, str]:
    event = payload.get("eventType", "")
    log.info("radarr_event", event_type=event)

    if event == "Test":
        return {"status": "ok", "test": "received"}

    if event not in {"Download", "MovieFileImported", "Upgrade"}:
        return {"status": "ignored", "reason": f"unhandled_event:{event}"}

    if not has_translate_tag(payload, "movie", settings.radarr_translate_tag):
        return {"status": "ignored", "reason": "no_translate_tag"}

    movie_file = payload.get("movieFile") or {}
    # relativePath is relative to the movie folder, so it is only usable
    # joined onto movie.folderPath.
    path = movie_file.get("path")
    rel = movie_file.get("relativePath")
    folder = (payload.get("movie") or {}).get("folderPath")
    if not path and rel and folder:
        path = str(Path(folder) / rel)
    if not path:
        return {"status": "no_path"}

    await enqueue(Path(path))
    return {"status": "queued"}
