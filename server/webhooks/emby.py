"""Emby webhook handler.

Accepts two event categories today:

1. ``library.*`` (and the ``Test`` ping) — fires on import/scan; enqueues a
   translation for newly added media. Behavior unchanged since v0.1.
2. ``playback.start`` — fires when a user presses Play. Gated behind the
   ``AUTO_TRANSLATE_ON_PLAYBACK`` config flag (default OFF). When enabled,
   enqueues an on-demand translation so the target-lang track becomes
   available 1-2 minutes into playback. Existing safeguards apply:
   queue-level dedup blocks repeat-fires, the pipeline short-circuits via
   :class:`server.subs.pipeline.NoSourceSubtitles` when there is nothing
   to translate, per-job and per-day cost caps cap exposure.

All other event types are ignored.
"""

from pathlib import Path
from typing import Any

import structlog
from fastapi import APIRouter, Depends

from server.config import settings
from server.webhooks.queue import enqueue
from server.webhooks.security import require_secret

router = APIRouter()
log = structlog.get_logger()


_STREAM_PREFIXES = (
    "http://", "https://", "rtsp://", "rtmp://", "udp://", "rtp://",
    "mms://", "hdhomerun://",
)


def _extract_path(payload: dict[str, Any]) -> str | None:
    item = payload.get("Item") or payload.get("item") or {}
    return item.get("Path") or item.get("path")


def _is_translatable_path(path: str) -> bool:
    """Reject paths that the pipeline cannot translate from disk.

    Emby emits playback.start for Live TV and remote-streamed content
    where the "Path" field is a URL (HDHomeRun tuner, RTSP camera, an
    upstream HTTP IPTV feed, etc.). Those URLs have no on-disk file
    to ffprobe, so enqueueing them produces a 404 several seconds
    later. Filter them out at the webhook seam so the queue stays
    clean.
    """
    lowered = path.lower()
    return not lowered.startswith(_STREAM_PREFIXES)


def _event_name(payload: dict[str, Any]) -> str:
    return (payload.get("Event") or payload.get("event") or "").strip()


@router.post("/emby", dependencies=[Depends(require_secret)])
async def emby(payload: dict[str, Any]) -> dict[str, str]:
    event = _event_name(payload)
    event_lower = event.lower()
    log.info("emby_event", event_type=event)

    # Playback-triggered path (TR-2yt). Opt-in via AUTO_TRANSLATE_ON_PLAYBACK.
    # Emby fires this in two shapes depending on plugin/version:
    #   - "playback.start"
    #   - "PlaybackStart"
    if event_lower in {"playback.start", "playbackstart"}:
        if not settings.auto_translate_on_playback:
            return {"status": "playback_disabled"}
        path = _extract_path(payload)
        if not path:
            return {"status": "no_path"}
        if not _is_translatable_path(path):
            log.info("playback_skip_stream", media_path=path)
            return {"status": "stream_skipped"}
        job_id = await enqueue(Path(path))
        if job_id is None:
            log.info("playback_enqueue_dedup", media_path=path)
            return {"status": "dedup"}
        log.info("playback_enqueue", media_path=path, job_id=job_id)
        return {"status": "queued"}

    # Library-scan path (unchanged behavior).
    if "library" not in event_lower and event != "Test":
        return {"status": "ignored"}

    path = _extract_path(payload)
    if not path:
        return {"status": "no_path"}
    if not _is_translatable_path(path):
        log.info("library_skip_stream", media_path=path)
        return {"status": "stream_skipped"}

    await enqueue(Path(path))
    return {"status": "queued"}
