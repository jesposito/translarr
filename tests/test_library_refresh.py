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
async def test_refresh_emby_item_specific_with_api_key(_emby_configured):
    """Item-specific refresh: lookup returns an item ID, refresh that item."""
    # Mock the Items lookup to return an item ID.
    respx.get("http://AnsibleMedia:8096/emby/Items").mock(
        return_value=httpx.Response(200, json={"Items": [{"Id": "item123"}]})
    )
    refresh_route = respx.post("http://AnsibleMedia:8096/emby/Items/item123/Refresh").mock(
        return_value=httpx.Response(204)
    )

    await library_refresh._refresh_emby_item("Movies/test.mkv")

    assert refresh_route.called
    req = refresh_route.calls[0].request
    assert "api_key=embykey123" in str(req.url)


@respx.mock
async def test_refresh_emby_fallback_to_full_scan(_emby_configured):
    """When item lookup returns nothing, fall back to full library scan."""
    respx.get("http://AnsibleMedia:8096/emby/Items").mock(
        return_value=httpx.Response(200, json={"Items": []})
    )
    full_route = respx.post("http://AnsibleMedia:8096/emby/Library/Refresh").mock(
        return_value=httpx.Response(204)
    )

    await library_refresh._refresh_emby_item("Movies/test.mkv")

    assert full_route.called
    assert "api_key=embykey123" in str(full_route.calls[0].request.url)


@respx.mock
async def test_refresh_jellyfin_item_specific(_jellyfin_configured):
    """Item-specific Jellyfin refresh."""
    respx.get("http://jellyfin:8096/Items").mock(
        return_value=httpx.Response(200, json={"Items": [{"Id": "jfitem456"}]})
    )
    refresh_route = respx.post("http://jellyfin:8096/Items/jfitem456/Refresh").mock(
        return_value=httpx.Response(204)
    )

    await library_refresh._refresh_jellyfin_item("TV/test.mkv")

    assert refresh_route.called
    req = refresh_route.calls[0].request
    assert req.headers.get("X-Emby-Token") == "jfkey456"


@respx.mock
async def test_refresh_jellyfin_fallback_to_full_scan(_jellyfin_configured):
    """When item lookup returns nothing, fall back to full library scan."""
    respx.get("http://jellyfin:8096/Items").mock(
        return_value=httpx.Response(200, json={"Items": []})
    )
    full_route = respx.post("http://jellyfin:8096/Library/Refresh").mock(
        return_value=httpx.Response(204)
    )

    await library_refresh._refresh_jellyfin_item("TV/test.mkv")

    assert full_route.called
    assert full_route.calls[0].request.headers.get("X-Emby-Token") == "jfkey456"


async def test_refresh_libraries_after_noop_when_unconfigured(_none_configured):
    # No HTTP mocking — if we tried to call out, respx would not block it,
    # but since there are no configured backends nothing should happen.
    # The point: no exception, returns cleanly.
    await library_refresh.refresh_libraries_after(Path("/tmp/x.en.translarr.srt"))


@respx.mock
async def test_emby_http_500_is_logged_not_raised(_emby_configured):
    # Item lookup returns nothing, full scan returns 500.
    respx.get("http://AnsibleMedia:8096/emby/Items").mock(
        return_value=httpx.Response(200, json={"Items": []})
    )
    respx.post("http://AnsibleMedia:8096/emby/Library/Refresh").mock(
        return_value=httpx.Response(500, text="boom")
    )

    # Top-level entry point must swallow errors.
    await library_refresh.refresh_libraries_after(Path("/tmp/x.en.translarr.srt"))


@respx.mock
async def test_jellyfin_http_500_is_logged_not_raised(_jellyfin_configured):
    # Item lookup returns nothing, full scan returns 500.
    respx.get("http://jellyfin:8096/Items").mock(
        return_value=httpx.Response(200, json={"Items": []})
    )
    respx.post("http://jellyfin:8096/Library/Refresh").mock(
        return_value=httpx.Response(500, text="boom")
    )

    await library_refresh.refresh_libraries_after(Path("/tmp/x.en.translarr.srt"))


@respx.mock
async def test_refresh_bounded_by_timeout(monkeypatch, _emby_configured):
    # Set a tiny timeout and have respx raise httpx.TimeoutException.
    monkeypatch.setattr(settings, "library_refresh_timeout_seconds", 1)
    respx.get("http://AnsibleMedia:8096/emby/Items").mock(
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

    # Both return empty items → fall back to full scan.
    respx.get("http://AnsibleMedia:8096/emby/Items").mock(
        return_value=httpx.Response(200, json={"Items": [{"Id": "e1"}]})
    )
    respx.post("http://AnsibleMedia:8096/emby/Items/e1/Refresh").mock(
        return_value=httpx.Response(204)
    )
    respx.get("http://jellyfin:8096/Items").mock(
        return_value=httpx.Response(200, json={"Items": [{"Id": "j1"}]})
    )
    respx.post("http://jellyfin:8096/Items/j1/Refresh").mock(
        return_value=httpx.Response(204)
    )

    await library_refresh.refresh_libraries_after(Path("/tmp/x.en.translarr.srt"))
