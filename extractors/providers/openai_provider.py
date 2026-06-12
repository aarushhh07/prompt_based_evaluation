"""OpenAI / Azure OpenAI provider."""

from __future__ import annotations

from config import LLMConfig
from extractors.base import BaseLLMProvider
from extractors.providers import register


@register("openai")
class OpenAIProvider(BaseLLMProvider):
    """Calls the OpenAI Chat Completions API."""

    def __init__(self, config: LLMConfig):
        super().__init__(config)
        try:
            from openai import AsyncOpenAI
        except ImportError:
            raise ImportError(
                "Install the openai package: pip install openai"
            )

        kwargs = {"api_key": config.api_key}
        if config.base_url:
            kwargs["base_url"] = config.base_url
        self._client = AsyncOpenAI(**kwargs)

    async def complete(self, system_prompt: str, user_prompt: str) -> str:
        response = await self._client.chat.completions.create(
            model=self.config.model,
            temperature=self.config.temperature,
            max_tokens=self.config.max_tokens,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        )
        return response.choices[0].message.content or ""
