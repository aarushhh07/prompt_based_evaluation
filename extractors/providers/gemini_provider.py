"""Google Gemini provider via Vertex AI using the google-genai SDK.

Authenticates via Application Default Credentials (ADC) when vertexai=True.
Falls back to API key mode when vertexai=False.
"""

from __future__ import annotations

from config import LLMConfig
from extractors.base import BaseLLMProvider
from extractors.providers import register


@register("gemini")
class GeminiProvider(BaseLLMProvider):
    """Calls Gemini via the google-genai SDK (Vertex AI or API key)."""

    def __init__(self, config: LLMConfig):
        super().__init__(config)
        try:
            from google import genai
        except ImportError:
            raise ImportError(
                "Install the google-genai package: pip install google-genai"
            )

        if config.vertexai:
            # Vertex AI mode — uses GOOGLE_APPLICATION_CREDENTIALS / ADC
            self._client = genai.Client(
                vertexai=True,
                project=config.gcp_project,
                location=config.gcp_location,
            )
        else:
            # Direct API key mode
            self._client = genai.Client(api_key=config.api_key)

    async def complete(self, system_prompt: str, user_prompt: str) -> str:
        from google.genai import types

        response = self._client.models.generate_content(
            model=self.config.model,
            contents=user_prompt,
            config=types.GenerateContentConfig(
                system_instruction=system_prompt,
                temperature=self.config.temperature,
                max_output_tokens=self.config.max_tokens,
                response_mime_type="application/json",
            ),
        )
        return response.text or ""
    
    async def complete_no_json(self, prompt: str) -> str:
        from google.genai import types

        response = self._client.models.generate_content(
                model=self.config.model,
                contents=prompt,
                config=types.GenerateContentConfig(
                    temperature=self.config.temperature,
                    max_output_tokens=self.config.max_tokens,
                ),
            )
        return response.text or ""
