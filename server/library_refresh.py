"""Best-effort library refresh hook called after a successful translation.

Tries item-specific refresh first (fast, <2s), falls back to full library
scan. Never raises — translation already succeeded, refresh failures are
just logged.

Item-specific lookup:
  Emby:     GET /emby/Items?Path={encoded_path}&api_key=X → extract Id
            POST /emby/Items/{Id}/Refresh?api_key=X
  Jellyfin: GET /Items?Path={encoded_path}&X-Emby-Token=X → extract Id
            POST /Items/{Id}/Refresh?X-Emby-Token=X
"""

from __future__ import annotations

import asyncio
import contextlib
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
    # Resolve the output path relative to MEDIA_ROOT so Emby/Jellyfin
    # can match it against their internal path database.
    media_path = str(output_path)
    with contextlib.suppress(ValueError):
        media_path = str(output_path.relative_to(settings.media_root))

    tasks = []
    if settings.emby_url and settings.emby_api_key:
        tasks.append(_refresh_emby_item(media_path))
    if settings.jellyfin_url and settings.jellyfin_api_key:
        tasks.append(_refresh_jellyfin_item(media_path))
    if not tasks:
        return
    log.info("library_refresh_starting", count=len(tasks), media_path=media_path)
    results = await asyncio.gather(*tasks, return_exceptions=True)
    for r in results:
        if isinstance(r, Exception):
            log.warning("library_refresh_failed", error=str(r))


async def _find_emby_item(media_path: str) -> str | None:
    """Look up an Emby item ID by file path. Returns None if not found."""
    url = f"{settings.emby_url.rstrip('/')}/emby/Items"
    timeout = settings.library_refresh_timeout_seconds
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.get(
            url,
            params={
                "Path": media_path,
                "Recursive": "true",
                "Limit": "1",
                "api_key": settings.emby_api_key,
            },
        )
        if resp.status_code >= 400:
            return None
        data = resp.json()
        items = data.get("Items") or []
        if items:
            return items[0].get("Id")
    return None


async def _refresh_emby_item(media_path: str) -> None:
    """Item-specific Emby refresh. Falls back to full library scan."""
    timeout = settings.library_refresh_timeout_seconds

    # Try item-specific refresh first.
    item_id = await _find_emby_item(media_path)
    if item_id:
        url = f"{settings.emby_url.rstrip('/')}/emby/Items/{item_id}/Refresh"
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(url, params={"api_key": settings.emby_api_key})
            resp.raise_for_status()
        log.info("emby_item_refresh_ok", item_id=item_id)
        return

    # Fallback: full library scan.
    url = f"{settings.emby_url.rstrip('/')}/emby/Library/Refresh"
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(url, params={"api_key": settings.emby_api_key})
        resp.raise_for_status()
    log.info("emby_full_refresh_ok")


async def _find_jellyfin_item(media_path: str) -> str | None:
    """Look up a Jellyfin item ID by file path. Returns None if not found."""
    url = f"{settings.jellyfin_url.rstrip('/')}/Items"
    timeout = settings.library_refresh_timeout_seconds
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.get(
            url,
            params={
                "Path": media_path,
                "Recursive": "true",
                "Limit": "1",
            },
            headers={"X-Emby-Token": settings.jellyfin_api_key},
        )
        if resp.status_code >= 400:
            return None
        data = resp.json()
        items = data.get("Items") or []
        if items:
            return items[0].get("Id")
    return None


async def _refresh_jellyfin_item(media_path: str) -> None:
    """Item-specific Jellyfin refresh. Falls back to full library scan."""
    timeout = settings.library_refresh_timeout_seconds

    # Try item-specific refresh first.
    item_id = await _find_jellyfin_item(media_path)
    if item_id:
        url = f"{settings.jellyfin_url.rstrip('/')}/Items/{item_id}/Refresh"
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(
                url,
                headers={"X-Emby-Token": settings.jellyfin_api_key},
            )
            resp.raise_for_status()
        log.info("jellyfin_item_refresh_ok", item_id=item_id)
        return

    # Fallback: full library scan.
    url = f"{settings.jellyfin_url.rstrip('/')}/Library/Refresh"
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(
            url,
            headers={"X-Emby-Token": settings.jellyfin_api_key},
        )
        resp.raise_for_status()
    log.info("jellyfin_full_refresh_ok")

