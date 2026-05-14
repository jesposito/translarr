"""ntfy push notifications module (server.notifications).

Notifications fire only when ``NTFY_URL`` is set + the matching category
flag is on. Errors must never propagate (notifications are best-effort).
"""

from __future__ import annotations

import asyncio

import httpx
import pytest
import respx

from server import notifications
from server.config import settings


@pytest.fixture(autouse=True)
def _quiet_ntfy(monkeypatch):
    monkeypatch.setattr(settings, "ntfy_url", None)
    monkeypatch.setattr(settings, "ntfy_on_success", True)
    monkeypatch.setattr(settings, "ntfy_on_failure", True)
    monkeypatch.setattr(settings, "ntfy_on_skip", False)
    yield


async def _drain_pending() -> None:
    """Wait for fire-and-forget tasks (notifications._PENDING) to complete."""
    for _ in range(20):
        if not notifications._PENDING:
            return
        await asyncio.sleep(0.02)


@pytest.mark.asyncio
async def test_no_url_means_no_op(monkeypatch):
    # Even with flags ON, an unset URL must short-circuit silently.
    monkeypatch.setattr(settings, "ntfy_url", None)
    notifications.notify_success(
        media_path="/m/x.mkv", target_lang="en", cost_cents=30, duration_s=12.0
    )
    await _drain_pending()
    assert not notifications._PENDING


@pytest.mark.asyncio
async def test_success_posts_to_url(monkeypatch):
    monkeypatch.setattr(settings, "ntfy_url", "https://ntfy.local/translarr")
    with respx.mock(base_url="https://ntfy.local") as mock:
        route = mock.post("/translarr").mock(return_value=httpx.Response(200))
        notifications.notify_success(
            media_path="/m/Show/S01E04.mkv",
            target_lang="en",
            cost_cents=30,
            duration_s=12.0,
        )
        await _drain_pending()
    assert route.called
    req = route.calls[0].request
    assert req.headers["Title"].startswith("Translarr:")
    assert b"$0.30" in req.content
    assert b"EN" in req.content


@pytest.mark.asyncio
async def test_skip_quiet_by_default(monkeypatch):
    monkeypatch.setattr(settings, "ntfy_url", "https://ntfy.local/translarr")
    # ntfy_on_skip defaults False — must not fire.
    monkeypatch.setattr(settings, "ntfy_on_skip", False)
    with respx.mock(base_url="https://ntfy.local", assert_all_called=False) as mock:
        route = mock.post("/translarr")
        notifications.notify_skip(
            media_path="/m/Show/S01E04.mkv",
            target_lang="en",
            reason="already translated",
        )
        await _drain_pending()
    assert not route.called


@pytest.mark.asyncio
async def test_skip_fires_when_enabled(monkeypatch):
    monkeypatch.setattr(settings, "ntfy_url", "https://ntfy.local/translarr")
    monkeypatch.setattr(settings, "ntfy_on_skip", True)
    with respx.mock(base_url="https://ntfy.local") as mock:
        route = mock.post("/translarr").mock(return_value=httpx.Response(200))
        notifications.notify_skip(
            media_path="/m/Show/S01E04.mkv",
            target_lang="en",
            reason="already translated",
        )
        await _drain_pending()
    assert route.called


@pytest.mark.asyncio
async def test_failure_truncates_long_error(monkeypatch):
    monkeypatch.setattr(settings, "ntfy_url", "https://ntfy.local/translarr")
    long_err = "X" * 1000
    with respx.mock(base_url="https://ntfy.local") as mock:
        route = mock.post("/translarr").mock(return_value=httpx.Response(200))
        notifications.notify_failure(
            media_path="/m/Show/S01E04.mkv", target_lang="en", error=long_err
        )
        await _drain_pending()
    assert route.called
    body = route.calls[0].request.content.decode()
    # 200 cap + the "Could not translate to EN.\n" preamble
    assert "X" * 200 in body
    assert "X" * 300 not in body


@pytest.mark.asyncio
async def test_http_error_does_not_raise(monkeypatch):
    """Server-side notification failures MUST NOT propagate to the worker."""
    monkeypatch.setattr(settings, "ntfy_url", "https://ntfy.local/translarr")
    with respx.mock(base_url="https://ntfy.local") as mock:
        mock.post("/translarr").mock(return_value=httpx.Response(503))
        # Should not raise — fire-and-forget swallows non-2xx silently.
        notifications.notify_success(
            media_path="/m/x.mkv", target_lang="en", cost_cents=0, duration_s=0.0
        )
        await _drain_pending()
    assert not notifications._PENDING
