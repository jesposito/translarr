"""DeepSeek provider. OpenAI-compatible API.

DeepSeek's chat API is compatible with the OpenAI SDK. Just point the
base_url at DeepSeek's endpoint. No separate SDK needed.
"""

from openai import AsyncOpenAI
from tenacity import retry, stop_after_attempt, wait_exponential

from server.config import settings
from server.llm.base import (
    TRANSLATE_SYSTEM_PROMPT,
    build_user_message,
    reconcile_lines,
)


class DeepSeekProvider:
    name = "deepseek"

    def __init__(self, model: str | None = None) -> None:
        if not settings.deepseek_api_key:
            raise RuntimeError("DEEPSEEK_API_KEY not set")
        self.client = AsyncOpenAI(
            api_key=settings.deepseek_api_key,
            base_url="https://api.deepseek.com/v1",
        )
        self.model = model or settings.llm_model

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=10))
    async def translate_batch(
        self,
        lines: list[str],
        source_lang: str,
        target_lang: str,
        prior_context: list[str] | None = None,
        glossary: dict[str, str] | None = None,
    ) -> list[str]:
        system = TRANSLATE_SYSTEM_PROMPT.format(
            source_lang=source_lang, target_lang=target_lang
        )
        user_text = build_user_message(lines, prior_context, glossary)

        resp = await self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user_text},
            ],
        )
        raw = resp.choices[0].message.content or ""
        return reconcile_lines(raw.split("\n"), len(lines))
