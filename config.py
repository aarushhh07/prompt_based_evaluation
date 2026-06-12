"""
Pluggable configuration for the evaluation pipeline.

LLM provider and model are configured here. API keys are read from
environment variables so nothing sensitive is committed to source control.
"""

import os
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class LLMConfig:
    """Configuration for the lightweight LLM used in criteria extraction."""

    # Supported: "openai", "gemini", "anthropic", "ollama"
    provider: str = "gemini"

    # Model name passed to the provider SDK
    model: str = "gemini-2.5-flash"

    # Provider-specific API key (read from env)
    api_key: Optional[str] = field(default=None, repr=False)

    # For Ollama or self-hosted: base URL override
    base_url: Optional[str] = None

    # Vertex AI / Google Cloud settings
    vertexai: bool = True
    gcp_project: Optional[str] = "symmetric-rune-497719-h0"
    gcp_location: str = "us-central1"

    # Generation parameters
    temperature: float = 0.0
    max_tokens: int = 4096

    def __post_init__(self):
        env_key_map = {
            "openai": "OPENAI_API_KEY",
            "gemini": "GEMINI_API_KEY",
            "anthropic": "ANTHROPIC_API_KEY",
        }
        if self.api_key is None and self.provider in env_key_map:
            self.api_key = os.environ.get(env_key_map[self.provider])

        # Allow env overrides for GCP settings
        if self.gcp_project is None:
            self.gcp_project = os.environ.get("GCP_PROJECT")
        if os.environ.get("GCP_LOCATION"):
            self.gcp_location = os.environ["GCP_LOCATION"]

@dataclass
class PipelineConfig:
    """Top-level configuration for the evaluation pipeline."""

    llm: LLMConfig = field(default_factory=LLMConfig)



    # When True, skip LLM extraction and only run checks that don't need it
    offline_mode: bool = False


def load_config(**overrides) -> PipelineConfig:
    """Build a PipelineConfig, allowing keyword overrides.

    Example:
        config = load_config(provider="gemini", model="gemini-2.5-flash")
    """
    llm_keys = {k for k in LLMConfig.__dataclass_fields__}
    llm_overrides = {k: v for k, v in overrides.items() if k in llm_keys}


    pipeline_overrides = {k: v for k, v in overrides.items() if k not in llm_keys}

    llm_cfg = LLMConfig(**llm_overrides)
    return PipelineConfig(llm=llm_cfg,**pipeline_overrides)
