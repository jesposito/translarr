from pathlib import Path
from typing import Any

import structlog
from fastapi import APIRouter, Depends

from server.webhooks.queue import enqueue
from server.webhooks.security import require_secret

router = APIRouter()
log = structlog.get_logger()


@router.post("/emby", dependencies=[Depends(require_secret)])
async def emby(payload: dict[str, Any]) -> dict[str, str]:
    event = payload.get("Event") or payload.get("event") or ""
    log.info("emby_event", event_type=event)

    if "library" not in event.lower() and event != "Test":
        return {"status": "ignored"}

    item = payload.get("Item") or payload.get("item") or {}
    path = item.get("Path") or item.get("path")
    if not path:
        return {"status": "no_path"}

    await enqueue(Path(path))
    return {"status": "queued"}
