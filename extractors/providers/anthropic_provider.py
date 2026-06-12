"""Anthropic (Claude) provider."""

from __future__ import annotations

from config import LLMConfig
from extractors.base import BaseLLMProvider
from extractors.providers import register


@register("anthropic")
class AnthropicProvider(BaseLLMProvider):
    """Calls the Anthropic Messages API."""

    def __init__(self, config: LLMConfig):
        super().__init__(config)
        try:
            from anthropic import AsyncAnthropic
        except ImportError:
            raise ImportError(
                "Install the anthropic package: pip install anthropic"
            )

        kwargs = {"api_key": config.api_key}
        if config.base_url:
            kwargs["base_url"] = config.base_url
        self._client = AsyncAnthropic(**kwargs)

    async def complete(self, system_prompt: str, user_prompt: str) -> str:
        response = await self._client.messages.create(
            model=self.config.model,
            max_tokens=self.config.max_tokens,
            temperature=self.config.temperature,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
        )
        return response.content[0].text
