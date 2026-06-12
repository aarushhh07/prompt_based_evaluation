"""Abstract base class for LLM providers used in criteria extraction."""

from abc import ABC, abstractmethod
from typing import Any, Dict

from config import LLMConfig


class BaseLLMProvider(ABC):
    """Interface that every LLM provider must implement.

    Each provider translates a system prompt + user prompt into an API call
    and returns the raw text response from the model.
    """

    def __init__(self, config: LLMConfig):
        self.config = config

    @abstractmethod
    async def complete(self, system_prompt: str, user_prompt: str) -> str:
        """Send a prompt to the LLM and return the raw text response.

        Args:
            system_prompt: Instructional context for the model.
            user_prompt: The actual content to process.

        Returns:
            The model's text response.
        """
        ...
    @abstractmethod
    async def complete_no_json(self, prompt:str) -> str:
        """Send a prompt get string response"""
    @classmethod
    def from_config(cls, config: LLMConfig) -> "BaseLLMProvider":
        """Factory: instantiate the correct provider from config.provider."""
        from extractors.providers import PROVIDER_REGISTRY

        provider_cls = PROVIDER_REGISTRY.get(config.provider)
        if provider_cls is None:
            available = ", ".join(PROVIDER_REGISTRY.keys())
            raise ValueError(
                f"Unknown LLM provider '{config.provider}'. "
                f"Available: {available}"
            )
        return provider_cls(config)
