from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from server.llm.deepseek_provider import DeepSeekProvider


@pytest.mark.asyncio
async def test_translation_disables_deepseek_thinking() -> None:
    create = AsyncMock(
        return_value=SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="Hola"))]
        )
    )
    provider = DeepSeekProvider.__new__(DeepSeekProvider)
    provider.client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create))
    )
    provider.model = "deepseek-v4-flash"

    result = await provider.translate_batch(["Hello"], "en", "es")

    assert result == ["Hola"]
    assert create.await_args.kwargs["extra_body"] == {
        "thinking": {"type": "disabled"}
    }
