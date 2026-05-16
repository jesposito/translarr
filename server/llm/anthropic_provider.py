from anthropic import AsyncAnthropic
from tenacity import retry, stop_after_attempt, wait_exponential

from server.config import settings
from server.llm.base import (
    TRANSLATE_SYSTEM_PROMPT,
    build_user_message,
    reconcile_lines,
)


class AnthropicProvider:
    name = "anthropic"

    def __init__(self, model: str | None = None) -> None:
        if not settings.anthropic_api_key:
            raise RuntimeError("ANTHROPIC_API_KEY not set")
        self.client = AsyncAnthropic(api_key=settings.anthropic_api_key)
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

        msg = await self.client.messages.create(
            model=self.model,
            max_tokens=4096,
            system=system,
            messages=[{"role": "user", "content": user_text}],
        )
        raw = "".join(b.text for b in msg.content if hasattr(b, "text"))
        return reconcile_lines(raw.split("\n"), len(lines))
