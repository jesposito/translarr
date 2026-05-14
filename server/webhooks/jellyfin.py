from pathlib import Path
from typing import Any

import structlog
from fastapi import APIRouter, Depends

from server.webhooks.queue import enqueue
from server.webhooks.security import require_secret

router = APIRouter()
log = structlog.get_logger()


@router.post("/jellyfin", dependencies=[Depends(require_secret)])
async def jellyfin(payload: dict[str, Any]) -> dict[str, str]:
    event = payload.get("NotificationType") or payload.get("Event") or ""
    log.info("jellyfin_event", event_type=event)

    if event not in {"ItemAdded", "ItemUpdated", "Test"}:
        return {"status": "ignored"}

    path = payload.get("Path") or payload.get("ItemPath")
    if not path:
        return {"status": "no_path"}

    await enqueue(Path(path))
    return {"status": "queued"}
