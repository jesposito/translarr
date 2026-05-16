"""Plex webhook handler.

Plex webhooks fire a JSON payload with ``event`` and ``Metadata`` fields.
We handle ``library.new`` events (new media added to library) and
``media.play`` events (opt-in via AUTO_TRANSLATE_ON_PLAYBACK).

Plex webhook format differs from Emby/Jellyfin:
- Event type is in ``payload.event`` (may also be ``Event``)
- Media path is in ``payload.Metadata.media[*].Part[*].file``
- The payload may arrive as form data ``payload=<json>`` or raw JSON
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import structlog
from fastapi import APIRouter, Depends, Request

from server.config import settings
from server.webhooks.queue import enqueue
from server.webhooks.security import require_secret

router = APIRouter()
log = structlog.get_logger()


def _extract_paths(payload: dict[str, Any]) -> list[str]:
    """Extract file paths from Plex's nested Metadata structure."""
    paths: list[str] = []
    metadata = payload.get("Metadata") or {}
    # Movies: Metadata.media[].Part[].file
    # TV: same structure, one Part per episode file
    for media in metadata.get("Media") or []:
        for part in media.get("Part") or []:
            path = part.get("file")
            if path:
                paths.append(path)
    return paths


def _event_name(payload: dict[str, Any]) -> str:
    return (payload.get("event") or payload.get("Event") or "").strip().lower()


@router.post("/plex", dependencies=[Depends(require_secret)])
async def plex(request: Request) -> dict[str, str]:
    """Handle Plex webhook notifications.

    Plex sends webhooks as ``application/x-www-form-urlencoded`` with a
    ``payload`` field containing JSON. We handle both form-encoded and
    raw JSON bodies.
    """
    content_type = request.headers.get("content-type", "")

    if "application/x-www-form-urlencoded" in content_type:
        form = await request.form()
        raw = form.get("payload")
        if not raw:
            return {"status": "no_payload"}
        try:
            payload = json.loads(str(raw))
        except (json.JSONDecodeError, TypeError):
            return {"status": "invalid_payload"}
    else:
        payload = await request.json()

    event = _event_name(payload)
    log.info("plex_event", event_type=event)

    # Playback-triggered translation (opt-in).
    if event in {"media.play", "media.resume"}:
        if not settings.auto_translate_on_playback:
            return {"status": "playback_disabled"}
        paths = _extract_paths(payload)
        if not paths:
            return {"status": "no_path"}
        results = []
        for path in paths:
            job_id = await enqueue(Path(path))
            results.append({"path": path, "job_id": job_id})
        log.info("plex_playback_enqueue", paths=len(paths))
        return {"status": "queued", "count": str(len(paths))}

    # Library events — new imports.
    if event not in {"library.new", "library.on.deck"}:
        return {"status": "ignored"}

    paths = _extract_paths(payload)
    if not paths:
        return {"status": "no_path"}

    for path in paths:
        await enqueue(Path(path))

    log.info("plex_library_enqueue", paths=len(paths))
    return {"status": "queued", "count": str(len(paths))}
