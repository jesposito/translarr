"""Best-effort library refresh hook called after a successful translation.

Calls Emby/Jellyfin REST APIs to refresh a SPECIFIC item if possible (cheap),
falling back to a full library scan (heavier). Never raises — translation
already succeeded, refresh failures are just logged.

Emby refresh-item:  POST {emby_url}/emby/Items/{item_id}/Refresh?api_key=X
Emby full scan:     POST {emby_url}/emby/Library/Refresh?api_key=X
Jellyfin same shape with /Library and X-Emby-Token header.

For v0.2 the Translarr server doesn't know the Emby item id (only the file
path on disk). So we trigger a full library scan, not an item-specific one.
v0.4+ can wire up the item id via the Emby plugin's REST controller, but for
now full scan is acceptable — Emby's scan is incremental and fast on small
deltas.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import httpx
import structlog

from server.config import settings

log = structlog.get_logger()


async def refresh_libraries_after(output_path: Path) -> None:
    """Fire-and-forget library refresh on configured Emby/Jellyfin instances.

    Called after a successful translation write. Each refresh is bounded by
    library_refresh_timeout_seconds. Failures are logged but don't propagate.
    """
    tasks = []
    if settings.emby_url and settings.emby_api_key:
        tasks.append(_refresh_emby())
    if settings.jellyfin_url and settings.jellyfin_api_key:
        tasks.append(_refresh_jellyfin())
    if not tasks:
        return
    log.info("library_refresh_starting", count=len(tasks), output=str(output_path))
    results = await asyncio.gather(*tasks, return_exceptions=True)
    for r in results:
        if isinstance(r, Exception):
            log.warning("library_refresh_failed", error=str(r))


async def _refresh_emby() -> None:
    url = f"{settings.emby_url.rstrip('/')}/emby/Library/Refresh"
    timeout = settings.library_refresh_timeout_seconds
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(url, params={"api_key": settings.emby_api_key})
        resp.raise_for_status()
    log.info("emby_refresh_ok")


async def _refresh_jellyfin() -> None:
    url = f"{settings.jellyfin_url.rstrip('/')}/Library/Refresh"
    timeout = settings.library_refresh_timeout_seconds
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(
            url,
            headers={"X-Emby-Token": settings.jellyfin_api_key},
        )
        resp.raise_for_status()
    log.info("jellyfin_refresh_ok")
