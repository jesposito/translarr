"""Tests for the best-effort Emby/Jellyfin library refresh hook."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
import respx

from server import library_refresh
from server.config import settings


@pytest.fixture
def _emby_configured(monkeypatch):
    monkeypatch.setattr(settings, "emby_url", "http://AnsibleMedia:8096")
    monkeypatch.setattr(settings, "emby_api_key", "embykey123")
    monkeypatch.setattr(settings, "jellyfin_url", None)
    monkeypatch.setattr(settings, "jellyfin_api_key", None)
    monkeypatch.setattr(settings, "library_refresh_timeout_seconds", 10)


@pytest.fixture
def _jellyfin_configured(monkeypatch):
    monkeypatch.setattr(settings, "emby_url", None)
    monkeypatch.setattr(settings, "emby_api_key", None)
    monkeypatch.setattr(settings, "jellyfin_url", "http://jellyfin:8096")
    monkeypatch.setattr(settings, "jellyfin_api_key", "jfkey456")
    monkeypatch.setattr(settings, "library_refresh_timeout_seconds", 10)


@pytest.fixture
def _none_configured(monkeypatch):
    monkeypatch.setattr(settings, "emby_url", None)
    monkeypatch.setattr(settings, "emby_api_key", None)
    monkeypatch.setattr(settings, "jellyfin_url", None)
    monkeypatch.setattr(settings, "jellyfin_api_key", None)


@respx.mock
async def test_refresh_emby_posts_to_correct_url_with_api_key(_emby_configured):
    route = respx.post("http://AnsibleMedia:8096/emby/Library/Refresh").mock(
        return_value=httpx.Response(204)
    )

    await library_refresh._refresh_emby()

    assert route.called
    req = route.calls[0].request
    assert req.method == "POST"
    assert "api_key=embykey123" in str(req.url)


@respx.mock
async def test_refresh_jellyfin_posts_with_emby_token_header(_jellyfin_configured):
    route = respx.post("http://jellyfin:8096/Library/Refresh").mock(
        return_value=httpx.Response(204)
    )

    await library_refresh._refresh_jellyfin()

    assert route.called
    req = route.calls[0].request
    assert req.method == "POST"
    assert req.headers.get("X-Emby-Token") == "jfkey456"


async def test_refresh_libraries_after_noop_when_unconfigured(_none_configured):
    # No HTTP mocking — if we tried to call out, respx would not block it,
    # but since there are no configured backends nothing should happen.
    # The point: no exception, returns cleanly.
    await library_refresh.refresh_libraries_after(Path("/tmp/x.en.translarr.srt"))


@respx.mock
async def test_emby_http_500_is_logged_not_raised(_emby_configured):
    respx.post("http://AnsibleMedia:8096/emby/Library/Refresh").mock(
        return_value=httpx.Response(500, text="boom")
    )

    # Top-level entry point must swallow errors.
    await library_refresh.refresh_libraries_after(Path("/tmp/x.en.translarr.srt"))


@respx.mock
async def test_jellyfin_http_500_is_logged_not_raised(_jellyfin_configured):
    respx.post("http://jellyfin:8096/Library/Refresh").mock(
        return_value=httpx.Response(500, text="boom")
    )

    await library_refresh.refresh_libraries_after(Path("/tmp/x.en.translarr.srt"))


@respx.mock
async def test_refresh_bounded_by_timeout(monkeypatch, _emby_configured):
    # Set a tiny timeout and have respx raise httpx.TimeoutException.
    monkeypatch.setattr(settings, "library_refresh_timeout_seconds", 1)
    respx.post("http://AnsibleMedia:8096/emby/Library/Refresh").mock(
        side_effect=httpx.ReadTimeout("simulated timeout")
    )

    # Must not propagate, even though the underlying call raised.
    await library_refresh.refresh_libraries_after(Path("/tmp/x.en.translarr.srt"))


@respx.mock
async def test_refresh_libraries_after_calls_both_when_configured(monkeypatch):
    monkeypatch.setattr(settings, "emby_url", "http://AnsibleMedia:8096")
    monkeypatch.setattr(settings, "emby_api_key", "embykey123")
    monkeypatch.setattr(settings, "jellyfin_url", "http://jellyfin:8096")
    monkeypatch.setattr(settings, "jellyfin_api_key", "jfkey456")

    emby_route = respx.post("http://AnsibleMedia:8096/emby/Library/Refresh").mock(
        return_value=httpx.Response(204)
    )
    jf_route = respx.post("http://jellyfin:8096/Library/Refresh").mock(
        return_value=httpx.Response(204)
    )

    await library_refresh.refresh_libraries_after(Path("/tmp/x.en.translarr.srt"))

    assert emby_route.called
    assert jf_route.called
