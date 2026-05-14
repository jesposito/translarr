from openai import AsyncOpenAI
from tenacity import retry, stop_after_attempt, wait_exponential

from server.config import settings
from server.llm.base import TRANSLATE_SYSTEM_PROMPT


class OpenAIProvider:
    name = "openai"

    def __init__(self, model: str | None = None) -> None:
        if not settings.openai_api_key:
            raise RuntimeError("OPENAI_API_KEY not set")
        self.client = AsyncOpenAI(api_key=settings.openai_api_key)
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

        resp = await self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": "\n".join(user_parts)},
            ],
        )
        text = resp.choices[0].message.content or ""
        out = [ln for ln in text.split("\n") if ln.strip()]
        if len(out) != len(lines):
            if len(out) < len(lines):
                out += [""] * (len(lines) - len(out))
            else:
                out = out[: len(lines)]
        return out
