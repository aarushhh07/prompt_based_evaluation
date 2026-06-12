"""Ollama (local) provider using its OpenAI-compatible API."""

from __future__ import annotations

from config import LLMConfig
from extractors.base import BaseLLMProvider
from extractors.providers import register

_DEFAULT_OLLAMA_URL = "http://localhost:11434/v1"


@register("ollama")
class OllamaProvider(BaseLLMProvider):
    """Calls a local Ollama instance via its OpenAI-compatible endpoint."""

    def __init__(self, config: LLMConfig):
        super().__init__(config)
        try:
            from openai import AsyncOpenAI
        except ImportError:
            raise ImportError(
                "Ollama provider uses the openai SDK. "
                "Install it: pip install openai"
            )

        self._client = AsyncOpenAI(
            api_key="ollama",  # Ollama doesn't need a real key
            base_url=config.base_url or _DEFAULT_OLLAMA_URL,
        )

    async def complete(self, system_prompt: str, user_prompt: str) -> str:
        response = await self._client.chat.completions.create(
            model=self.config.model,
            temperature=self.config.temperature,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        )
        return response.choices[0].message.content or ""
