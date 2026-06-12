"""Extracts structured evaluation criteria from an email generation prompt.

Uses a lightweight LLM to parse the prompt and produce an ExtractedCriteria
object that the format checker can evaluate against.
"""

from __future__ import annotations

import json
import logging
from typing import Optional

from config import LLMConfig
from extractors.base import BaseLLMProvider
from models.criteria_schema import ExtractedCriteria

logger = logging.getLogger(__name__)

# ── System prompt sent to the lightweight LLM ──────────────────────────

EXTRACTION_SYSTEM_PROMPT = """\
You are a precise instruction parser. Your job is to read an email-generation
prompt and extract every formatting or structural constraint into a JSON object.

Return ONLY valid JSON matching this schema (omit fields that are not mentioned
or implied by the prompt):

{
  "word_limit":       { "min": <int|null>, "max": <int|null> },
  "character_limit":  { "min": <int|null>, "max": <int|null> },
  "line_limit":       { "min": <int|null>, "max": <int|null> },
  "paragraph_limit":  { "min": <int|null>, "max": <int|null> },
  "subject_line":     { "required": <bool>, "max_characters": <int|null> },
  "banned_words":     [ "<word>", ... ],
  "required_elements":[ "<element>", ... ],
  "tone":             "<tone description or null>",
  "additional":       { "<key>": "<value>", ... }
}

Rules:
- Only include fields that the prompt explicitly or clearly implicitly specifies.
- "required_elements" captures things like "call-to-action", "greeting",
  "signature", etc.
- "banned_words" should contain ONLY specific words/phrases that are banned,
  not categories like "emojis" or "links".
- "additional" must be MINIMAL — only simple key-value string pairs for
  formatting constraints that don't fit the other fields. Do NOT nest objects
  or arrays inside "additional". If unsure, omit it.
- Keep the output COMPACT. Do not add context about sender, recipient, or
  campaign — only extract formatting and structural rules.
- Do NOT invent constraints that are not in the prompt.
- Return raw JSON only — no markdown fences, no commentary.
"""


class CriteriaExtractor:
    """Extracts evaluation criteria from a prompt using an LLM."""

    def __init__(self, provider: Optional[BaseLLMProvider] = None, config: Optional[LLMConfig] = None):
        """Initialise with either a provider instance or a config to build one.

        Args:
            provider: A pre-built LLM provider. Takes precedence if given.
            config: LLM config used to build a provider via the factory.
        """
        if provider is not None:
            self._provider = provider
        elif config is not None:
            self._provider = BaseLLMProvider.from_config(config)
        else:
            raise ValueError("Either 'provider' or 'config' must be supplied.")

    async def extract(self, prompt: str) -> ExtractedCriteria:
        """Parse *prompt* and return structured criteria.

        Args:
            prompt: The email-generation prompt to analyse.

        Returns:
            An ExtractedCriteria instance populated from the LLM's output.
        """
        raw = await self._provider.complete(
            system_prompt=EXTRACTION_SYSTEM_PROMPT,
            user_prompt=prompt,
        )

        return self._parse_response(raw)

    # ── internals ──────────────────────────────────────────────────────

    @staticmethod
    def _sanitize_json(text: str) -> str:
        """Clean up common LLM JSON quirks that break json.loads."""
        import re

        # Remove single-line // comments
        text = re.sub(r'//.*?$', '', text, flags=re.MULTILINE)

        # Remove trailing commas before } or ]
        text = re.sub(r',\s*([}\]])', r'\1', text)

        return text.strip()

    @staticmethod
    def _parse_response(raw: str) -> ExtractedCriteria:
        """Best-effort parse of the LLM response into ExtractedCriteria."""
        logger.debug("Raw LLM response:\n%s", raw)

        # Strip markdown fences if the model wraps the JSON
        text = raw.strip()
        if text.startswith("```"):
            # Remove opening fence (with optional language tag)
            text = text.split("\n", 1)[-1]
        if text.endswith("```"):
            text = text.rsplit("```", 1)[0]
        text = text.strip()

        # First attempt: parse as-is
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            # Second attempt: sanitize common quirks
            sanitized = CriteriaExtractor._sanitize_json(text)
            try:
                data = json.loads(sanitized)
            except json.JSONDecodeError as exc:
                logger.warning(
                    "LLM returned invalid JSON even after sanitization; "
                    "using empty criteria. Error: %s\nRaw text:\n%s", exc, text
                )
                return ExtractedCriteria()

        try:
            return ExtractedCriteria.model_validate(data)
        except Exception as exc:
            logger.warning("Failed to validate LLM JSON against schema: %s", exc)
            return ExtractedCriteria()
