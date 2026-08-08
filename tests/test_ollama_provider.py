from unittest.mock import patch

import pytest
from pydantic import ValidationError

from server.config import Settings, settings
from server.llm.ollama_provider import OllamaProvider
from server.settings_store import REGISTRY, SettingValidationError, _coerce_and_validate


class _Response:
    def raise_for_status(self) -> None:
        pass

    def json(self) -> dict:
        return {"message": {"content": "Hola"}}


class _Client:
    timeout: float | None = None

    def __init__(self, *, timeout: float) -> None:
        type(self).timeout = timeout

    async def __aenter__(self) -> "_Client":
        return self

    async def __aexit__(self, *_args: object) -> None:
        pass

    async def post(self, *_args: object, **_kwargs: object) -> _Response:
        return _Response()


@pytest.mark.asyncio
async def test_translation_uses_configured_request_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "ollama_request_timeout_seconds", 321.0)

    with patch("server.llm.ollama_provider.httpx.AsyncClient", _Client):
        result = await OllamaProvider("qwen3").translate_batch(["Hello"], "en", "es")

    assert result == ["Hola"]
    assert _Client.timeout == 321.0


@pytest.mark.parametrize("value", [0, -1, float("inf"), float("nan")])
def test_settings_reject_invalid_request_timeouts(value: float) -> None:
    with pytest.raises(ValidationError):
        Settings(ollama_request_timeout_seconds=value)


@pytest.mark.parametrize("value", [0, 86_401, float("inf"), float("nan")])
def test_runtime_setting_rejects_out_of_range_timeouts(value: float) -> None:
    meta = REGISTRY["ollama_request_timeout_seconds"]
    with pytest.raises(SettingValidationError):
        _coerce_and_validate(meta, value)
