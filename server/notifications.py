"""Push notifications for translation job completion.

Fires a single HTTP POST per terminal job state to an ntfy.sh-compatible
endpoint. Designed to be cheap, fire-and-forget, and never block job
completion or the worker loop. Errors are logged and dropped.

ntfy's wire format (used here):

    POST {NTFY_URL}
    Title: <header>
    Priority: <1-5>
    Tags: <comma-separated emoji aliases>

    <body>

We deliberately use HTTP headers (not the JSON variant) so the same
config works against the public ntfy.sh, a self-hosted ntfy, and any
plain HTTP endpoint that just logs the body. The user's iPhone+ntfy
setup (see ~/.gstack/memory iphone-tailnet) consumes these directly.

Config:

    NTFY_URL          — destination, empty/unset disables all notifications
    NTFY_ON_SUCCESS   — fire on job done with a real translation
    NTFY_ON_FAILURE   — fire on job FAILED after retries exhausted
    NTFY_ON_SKIP      — fire on terminal skips (already-translated /
                        no-source-subtitles). OFF by default — these
                        are very chatty under AUTO_TRANSLATE_ON_PLAYBACK.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import httpx
import structlog

from server.config import settings

log = structlog.get_logger()

# Strong refs for fire-and-forget tasks so they survive GC long enough
# to actually fire. Tasks self-remove via add_done_callback.
_PENDING: set[asyncio.Task] = set()


def _spawn(coro) -> None:
    task = asyncio.create_task(coro)
    _PENDING.add(task)
    task.add_done_callback(_PENDING.discard)


def _dollars(cents: int) -> str:
    return f"${cents / 100:.2f}"


def _short_name(media_path: str) -> str:
    """Strip directories and codec/release noise for human-friendly toast."""
    return Path(media_path).stem[:80]


async def _post(title: str, body: str, *, priority: int, tags: str) -> None:
    url = settings.ntfy_url
    if not url:
        return
    try:
        async with httpx.AsyncClient(timeout=settings.ntfy_timeout_seconds) as client:
            await client.post(
                url,
                content=body.encode("utf-8"),
                headers={
                    "Title": title,
                    "Priority": str(priority),
                    "Tags": tags,
                },
            )
        log.info("ntfy_sent", title=title, priority=priority)
    except Exception as e:
        log.warning("ntfy_failed", error=str(e), title=title)


def notify_success(*, media_path: str, target_lang: str, cost_cents: int, duration_s: float) -> None:
    """Fire-and-forget: translation finished successfully."""
    if not settings.ntfy_url or not settings.ntfy_on_success:
        return
    title = f"Translarr: {_short_name(media_path)}"
    body = (
        f"Translated to {target_lang.upper()} in {duration_s:.0f}s "
        f"({_dollars(cost_cents)})"
    )
    _spawn(_post(title, body, priority=3, tags="white_check_mark,sparkles"))


def notify_failure(*, media_path: str, target_lang: str, error: str) -> None:
    """Fire-and-forget: translation failed after retries."""
    if not settings.ntfy_url or not settings.ntfy_on_failure:
        return
    title = f"Translarr failed: {_short_name(media_path)}"
    # Keep the body short — the full error is in the Translarr Web UI.
    short_err = error[:200]
    body = f"Could not translate to {target_lang.upper()}.\n{short_err}"
    _spawn(_post(title, body, priority=4, tags="warning,x"))


def notify_skip(*, media_path: str, target_lang: str, reason: str) -> None:
    """Fire-and-forget: translation skipped (already done / no source subs)."""
    if not settings.ntfy_url or not settings.ntfy_on_skip:
        return
    title = f"Translarr skipped: {_short_name(media_path)}"
    body = f"No translation needed ({reason}). Target {target_lang.upper()}."
    _spawn(_post(title, body, priority=2, tags="information_source"))
