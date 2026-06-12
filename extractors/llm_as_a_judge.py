"""Creats custom judge for LLM-as-a-Judge"""

import asyncio
from typing import Optional
from deepeval.models.base_model import DeepEvalBaseLLM

from config import LLMConfig
from extractors.base import BaseLLMProvider


class DeepEvalProviderAdapter(DeepEvalBaseLLM):
    """
    A single, unified adapter that translates your BaseLLMProvider ecosystem
    into a native DeepEval-compliant execution engine.
    """
    def __init__(
        self, 
        provider: Optional[BaseLLMProvider] = None, 
        config: Optional[LLMConfig] = None
    ):
        """Initialise with either a provider instance or a config to build one.

        Args:
            provider: A pre-built LLM provider. Takes precedence if given.
            config: LLM config used to build a provider via the factory.
        """
        if provider is not None:
            self.provider = provider
        elif config is not None:
            # Reuses your factory method to resolve the provider class from registry
            self.provider = BaseLLMProvider.from_config(config)
        else:
            raise ValueError("You must provide either an active 'provider' instance or an 'LLMConfig'.")

        self.model_name = self.provider.config.model
        super().__init__()

    def load_model(self):
        """DeepEval tracking hook — returns the underlying client if available."""
        return getattr(self.provider, "_client", None)

    def generate(self, prompt: str) -> str:
    # complete_no_json is async — use asyncio.run() to execute it from sync context
        return asyncio.run(self.provider.complete_no_json(prompt))

    async def a_generate(self, prompt: str) -> str:
    # await directly — no need for run_in_executor since complete_no_json is async
        return await self.provider.complete_no_json(prompt)

    def get_model_name(self) -> str:
        """Exposes the active model name to DeepEval logger interfaces."""
        return self.model_name