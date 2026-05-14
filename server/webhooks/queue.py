"""Simple in-memory dedup queue. Future: swap for a persistent queue."""

import asyncio
from pathlib import Path

import structlog

from server.config import settings
from server.models import TranslateRequest
from server.subs.pipeline import translate_media

log = structlog.get_logger()


_seen: set[str] = set()
_sem = asyncio.Semaphore(settings.max_concurrent)


def _key(path: Path) -> str:
    return str(path)


async def enqueue(path: Path) -> None:
    k = _key(path)
    if k in _seen:
        log.info("dedup_skip", path=k)
        return
    _seen.add(k)

    async def _run() -> None:
        async with _sem:
            try:
                await translate_media(TranslateRequest(media_path=path))
            except Exception as e:
                log.exception("translate_failed", path=k, error=str(e))
            finally:
                _seen.discard(k)

    asyncio.create_task(_run())
