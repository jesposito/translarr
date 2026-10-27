"""Best-effort library refresh hook called after a successful translation.

Tries item-specific refresh first (fast, <2s), falls back to full library
scan. Never raises — translation already succeeded, refresh failures are
just logged.

Item-specific lookup:
  Emby:     GET /emby/Items?Path={encoded_path} → extract Id
            POST /emby/Items/{Id}/Refresh
  Jellyfin: GET /Items?Path={encoded_path} → extract Id
            POST /Items/{Id}/Refresh
  Both authenticate with the X-Emby-Token header so the key never lands
  in a URL (and so never in access logs).
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import httpx
import structlog

from server.browse import MEDIA_EXTENSIONS
from server.config import settings

log = structlog.get_logger()


async def refresh_libraries_after(output_path: Path) -> None:
    """Fire-and-forget library refresh on configured Emby/Jellyfin instances.

    Called after a successful translation write. Each refresh is bounded by
    library_refresh_timeout_seconds. Failures are logged but don't propagate.
    """
    # Emby/Jellyfin index the media file (not our sidecar) by its absolute
    # path, so look up the video next to the .srt.
    # Assumes both servers mount media at the same path as Translarr;
    # add a path-mapping setting if a deployment differs.
    media_path = str(_media_for_output(output_path))

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


def _media_for_output(output_path: Path) -> Path:
    """Return the media file a ``<stem>.<lang>.translarr.srt`` belongs to.

    Falls back to the output path itself when no sibling video is found.
    """
    stem = output_path.name.split(".translarr.")[0].rsplit(".", 1)[0]
    for ext in sorted(MEDIA_EXTENSIONS):
        candidate = output_path.with_name(stem + ext)
        if candidate.exists():
            return candidate
    return output_path


async def _find_emby_item(media_path: str, base_url: str, api_key: str) -> str | None:
    """Look up an Emby item ID by file path. Returns None if not found."""
    timeout = settings.library_refresh_timeout_seconds
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.get(
            f"{base_url}/emby/Items",
            params={
                "Path": media_path,
                "Recursive": "true",
                "Limit": "1",
            },
            headers={"X-Emby-Token": api_key},
        )
        if resp.status_code >= 400:
            return None
        items = resp.json().get("Items") or []
        return items[0].get("Id") if items else None


async def _refresh_emby_item(media_path: str) -> None:
    """Item-specific Emby refresh. Falls back to full library scan.

    Caller must have verified ``settings.emby_url`` and ``settings.emby_api_key``
    are set; this function asserts the same so the type checker can see
    the strings are non-None for URL construction.
    """
    assert settings.emby_url is not None
    assert settings.emby_api_key is not None
    base_url = settings.emby_url.rstrip("/")
    api_key = settings.emby_api_key
    timeout = settings.library_refresh_timeout_seconds

    item_id = await _find_emby_item(media_path, base_url, api_key)
    if item_id:
        url = f"{base_url}/emby/Items/{item_id}/Refresh"
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(url, headers={"X-Emby-Token": api_key})
            resp.raise_for_status()
        log.info("emby_item_refresh_ok", item_id=item_id)
        return

    url = f"{base_url}/emby/Library/Refresh"
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(url, headers={"X-Emby-Token": api_key})
        resp.raise_for_status()
    log.info("emby_full_refresh_ok")


async def _find_jellyfin_item(media_path: str, base_url: str, api_key: str) -> str | None:
    """Look up a Jellyfin item ID by file path. Returns None if not found."""
    timeout = settings.library_refresh_timeout_seconds
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.get(
            f"{base_url}/Items",
            params={
                "Path": media_path,
                "Recursive": "true",
                "Limit": "1",
            },
            headers={"X-Emby-Token": api_key},
        )
        if resp.status_code >= 400:
            return None
        items = resp.json().get("Items") or []
        return items[0].get("Id") if items else None


async def _refresh_jellyfin_item(media_path: str) -> None:
    """Item-specific Jellyfin refresh. Falls back to full library scan.

    Caller must have verified ``settings.jellyfin_url`` and
    ``settings.jellyfin_api_key`` are set; this function asserts the same
    so the type checker can see the strings are non-None.
    """
    assert settings.jellyfin_url is not None
    assert settings.jellyfin_api_key is not None
    base_url = settings.jellyfin_url.rstrip("/")
    api_key = settings.jellyfin_api_key
    timeout = settings.library_refresh_timeout_seconds

    item_id = await _find_jellyfin_item(media_path, base_url, api_key)
    if item_id:
        url = f"{base_url}/Items/{item_id}/Refresh"
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(url, headers={"X-Emby-Token": api_key})
            resp.raise_for_status()
        log.info("jellyfin_item_refresh_ok", item_id=item_id)
        return

    url = f"{base_url}/Library/Refresh"
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(url, headers={"X-Emby-Token": api_key})
        resp.raise_for_status()
    log.info("jellyfin_full_refresh_ok")

