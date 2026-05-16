from server.config import settings
from server.llm.anthropic_provider import AnthropicProvider
from server.llm.base import LLMProvider
from server.llm.deepseek_provider import DeepSeekProvider
from server.llm.gemini_provider import GeminiProvider
from server.llm.ollama_provider import OllamaProvider
from server.llm.openai_provider import OpenAIProvider


def get_provider(name: str | None = None, model: str | None = None) -> LLMProvider:
    provider = (name or settings.llm_provider).lower()
    match provider:
        case "anthropic":
            return AnthropicProvider(model=model)
        case "openai":
            return OpenAIProvider(model=model)
        case "ollama":
            return OllamaProvider(model=model)
        case "deepseek":
            return DeepSeekProvider(model=model)
        case "gemini":
            return GeminiProvider(model=model)
        case other:
            raise ValueError(f"unknown LLM provider: {other}")
