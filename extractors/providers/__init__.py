"""LLM provider registry.

Import and register all concrete providers here.
"""

from typing import Dict, Type

from extractors.base import BaseLLMProvider

# Registry mapping provider name → class
PROVIDER_REGISTRY: Dict[str, Type[BaseLLMProvider]] = {}


def register(name: str):
    """Decorator that registers a provider class under the given name."""

    def decorator(cls: Type[BaseLLMProvider]):
        PROVIDER_REGISTRY[name] = cls
        return cls

    return decorator


# Import providers so they self-register on module load
from extractors.providers.openai_provider import OpenAIProvider  # noqa: E402, F401
from extractors.providers.gemini_provider import GeminiProvider  # noqa: E402, F401
from extractors.providers.anthropic_provider import AnthropicProvider  # noqa: E402, F401
from extractors.providers.ollama_provider import OllamaProvider  # noqa: E402, F401
