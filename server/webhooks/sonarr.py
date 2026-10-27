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


@router.post("/sonarr", dependencies=[Depends(require_secret)])
async def sonarr(payload: dict[str, Any]) -> dict[str, str]:
    event = payload.get("eventType", "")
    log.info("sonarr_event", event_type=event)

    if event == "Test":
        return {"status": "ok", "test": "received"}

    if event not in {"Download", "EpisodeFileImported", "Upgrade"}:
        return {"status": "ignored", "reason": f"unhandled_event:{event}"}

    if not has_translate_tag(payload, "series", settings.sonarr_translate_tag):
        return {"status": "ignored", "reason": "no_translate_tag"}

    ep_file = payload.get("episodeFile") or {}
    # relativePath is relative to the series folder, so it is only usable
    # joined onto series.path.
    path = ep_file.get("path")
    rel = ep_file.get("relativePath")
    folder = (payload.get("series") or {}).get("path")
    if not path and rel and folder:
        path = str(Path(folder) / rel)
    if not path:
        return {"status": "no_path"}

    await enqueue(Path(path))
    return {"status": "queued"}
