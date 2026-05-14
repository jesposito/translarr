from pathlib import Path
from typing import Any

import structlog
from fastapi import APIRouter, Depends

from server.config import settings
from server.webhooks.queue import enqueue
from server.webhooks.security import require_secret

router = APIRouter()
log = structlog.get_logger()


def _has_translate_tag(payload: dict[str, Any], tag_label: str) -> bool:
    """Check Radarr 'movie' object for the translate tag.

    Radarr Connect payloads put tags under `movie.tags`, an array of tag-label strings
    (modern Radarr) or `{id, label}` objects (older formats). Accept both.
    """
    movie = payload.get("movie") or {}
    tags = movie.get("tags") or []
    for t in tags:
        if isinstance(t, str) and t == tag_label:
            return True
        if isinstance(t, dict) and t.get("label") == tag_label:
            return True
    return False


@router.post("/radarr", dependencies=[Depends(require_secret)])
async def radarr(payload: dict[str, Any]) -> dict[str, str]:
    event = payload.get("eventType", "")
    log.info("radarr_event", event_type=event)

    if event == "Test":
        return {"status": "ok", "test": "received"}

    if event not in {"Download", "MovieFileImported", "Upgrade"}:
        return {"status": "ignored", "reason": f"unhandled_event:{event}"}

    if not _has_translate_tag(payload, settings.radarr_translate_tag):
        return {"status": "ignored", "reason": "no_translate_tag"}

    movie_file = payload.get("movieFile") or {}
    path = movie_file.get("path") or movie_file.get("relativePath")
    if not path:
        return {"status": "no_path"}

    await enqueue(Path(path))
    return {"status": "queued"}
