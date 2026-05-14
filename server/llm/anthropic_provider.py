from anthropic import AsyncAnthropic
from tenacity import retry, stop_after_attempt, wait_exponential

from server.config import settings
from server.llm.base import TRANSLATE_SYSTEM_PROMPT


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
        system = TRANSLATE_SYSTEM_PROMPT.format(source_lang=source_lang, target_lang=target_lang)

        user_parts: list[str] = []
        if prior_context:
            user_parts.append("Previously translated context:")
            user_parts.extend(prior_context[-10:])
            user_parts.append("---")
        if glossary:
            user_parts.append("Glossary (preserve these exactly):")
            for src, dst in glossary.items():
                user_parts.append(f"  {src} -> {dst}")
            user_parts.append("---")
        user_parts.append(f"Translate the following {len(lines)} lines:")
        user_parts.extend(lines)

        msg = await self.client.messages.create(
            model=self.model,
            max_tokens=4096,
            system=system,
            messages=[{"role": "user", "content": "\n".join(user_parts)}],
        )
        text = "".join(b.text for b in msg.content if hasattr(b, "text"))
        out = [ln for ln in text.split("\n") if ln.strip()]
        if len(out) != len(lines):
            # Best-effort recovery: pad or truncate to keep alignment.
            if len(out) < len(lines):
                out += [""] * (len(lines) - len(out))
            else:
                out = out[: len(lines)]
        return out
