"""Pydantic models for the evaluation pipeline input data.

New format (June 2026)
======================
* **prompts.json** – array of ``PromptConfig`` objects that describe each
  prompt, its evaluation criteria, and its metadata.
* **{id}_response.csv** – one column ``prompt_response`` where every row is a
  JSON-encoded ``ResponseRecord``.  The ``__evaluation`` block is present only
  when the platform already ran an evaluation pass (e.g. prompt 913).

``EvaluationInput`` is the unified object handed to the pipeline: it pairs a
``PromptConfig`` with a single ``ResponseRecord`` and adds an ``eval_id`` for
tracking.
"""

from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


# ── Prompt-side models (prompts.json) ────────────────────────────────────────


class PromptInstruction(BaseModel):
    """Core instruction block inside ``evaluation_config_payload``.

    Captures the prompt text, tone, output type, and the quality / retry
    settings used by the generation loop.
    """

    goal: str = Field(..., description="Full prompt text / generation goal")
    tone: Optional[str] = Field(None, description="Desired tone, e.g. 'excited'")
    type: Optional[str] = Field(
        None, description="Output type, e.g. 'emailFull', 'snippet'"
    )
    evaluationGuideline: Optional[str] = Field(
        None, description="Human-readable rubric for quality evaluation"
    )
    minimumQualityScore: Optional[int] = Field(
        None, description="Minimum acceptable overall score (0-100)"
    )
    maxRegenerationAttempts: Optional[int] = Field(
        None, description="Max retries before accepting a response"
    )


class EvaluationConfigPayload(BaseModel):
    """Wrapper around ``PromptInstruction``; mirrors the JSON nesting in
    ``prompts.json`` where instruction lives one level deep."""

    instruction: PromptInstruction


class PromptConfig(BaseModel):
    """A single entry from ``prompts.json``.

    Describes the prompt definition, its owner tenant, versioning info, and
    the evaluation configuration that governs quality checks.
    """

    id: str = Field(..., description="Unique prompt identifier (e.g. '913')")
    name: str = Field(..., description="Human-readable prompt name")
    tenant_id: Optional[str] = Field(
        None, description="Tenant that owns this prompt"
    )
    version: Optional[str] = Field(None, description="Prompt version string")
    input_attributes: Optional[str] = Field(
        None,
        description=(
            "Comma-separated template placeholders, "
            "e.g. '{account.name},{person.name}'"
        ),
    )
    evaluation_config_type: Optional[str] = Field(
        None, description="Evaluation type, e.g. 'content'"
    )
    evaluation_config_payload: Optional[EvaluationConfigPayload] = Field(
        None, description="Nested evaluation / instruction config"
    )
    value_type: Optional[str] = Field(
        None, description="Expected output type, e.g. 'string'"
    )
    target_object: Optional[str] = Field(
        None, description="Target CRM object, e.g. 'person'"
    )
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    created_by: Optional[str] = None
    updated_by: Optional[str] = None
    status: Optional[str] = Field(None, description="e.g. 'active', 'archived'")


# ── Response-side models ({id}_response.csv) ─────────────────────────────────


class ExistingEvaluation(BaseModel):
    """Platform-generated evaluation attached to a response.

    All fields are optional because not every response carries an evaluation
    (e.g. prompt 480 responses omit ``__evaluation`` entirely).
    """

    stopReason: Optional[str] = Field(
        None,
        description=(
            "Why evaluation stopped, e.g. 'max_iterations_reached', "
            "'quality_threshold_met'"
        ),
    )
    keyStrengths: Optional[List[str]] = Field(
        None, description="Positive aspects identified by the evaluator"
    )
    overallScore: Optional[int] = Field(
        None, description="Aggregate quality score (0-100)"
    )
    qualityRating: Optional[str] = Field(
        None, description="Human-readable rating, e.g. 'Good', 'Excellent'"
    )
    isRegeneratable: Optional[bool] = Field(
        None, description="Whether the response can be regenerated"
    )
    recommendations: Optional[List[str]] = Field(
        None, description="Suggested improvements"
    )
    areasForImprovement: Optional[List[str]] = Field(
        None, description="Weak areas flagged by the evaluator"
    )
    regenerationAttempts: Optional[int] = Field(
        None, description="Number of regeneration attempts made"
    )
    nonRegenerableReasons: Optional[List[str]] = Field(
        None,
        description="Reasons why regeneration would not help (empty list = ok)",
    )


class ResponseRecord(BaseModel):
    """A single LLM-generated response parsed from a ``prompt_response`` CSV
    cell.

    The ``__evaluation`` key is mapped to ``evaluation`` via a field alias so
    the rest of the codebase can use a clean Python name.
    """

    body: Optional[str] = Field(None, description="Email body text")
    subject: Optional[str] = Field(None, description="Email subject line")
    reasoning: Optional[str] = Field(
        None, description="Model's reasoning about its own output"
    )
    evaluation: Optional[ExistingEvaluation] = Field(
        None,
        alias="__evaluation",
        description="Platform evaluation block (absent for some prompts)",
    )

    model_config = {"populate_by_name": True}


# ── Pipeline-level composite model ───────────────────────────────────────────


class EvaluationInput(BaseModel):
    """Unified record handed to every pipeline stage.

    Combines the prompt configuration with a single response so that
    evaluators have full context (prompt goal, tone, rubric, existing scores,
    etc.) in one object.
    """

    eval_id: str = Field(
        ..., description="Unique identifier for this evaluation run"
    )
    prompt_config: PromptConfig = Field(
        ..., description="Prompt definition from prompts.json"
    )
    response: ResponseRecord = Field(
        ..., description="Single LLM response to evaluate"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# Legacy models (pre-June 2026 format) — kept for backward compatibility
# ═══════════════════════════════════════════════════════════════════════════════


class TargetPersona(BaseModel):
    """The recipient the email is addressed to."""

    name: Optional[str] = None
    title: Optional[str] = None
    company: Optional[str] = None
    industry: Optional[str] = None


class CampaignInfo(BaseModel):
    """Campaign-level metadata."""

    campaign_id: Optional[str] = None
    campaign_name: Optional[str] = None
    email_type: Optional[str] = Field(
        None, description="E.g. cold_outreach, follow_up, nurture"
    )


class InputMetadata(BaseModel):
    """Miscellaneous metadata attached to the evaluation input."""

    sender_company: Optional[str] = None
    product_context: Optional[str] = None
    temperature: Optional[float] = None
    attempt: Optional[int] = None
    extra: Dict[str, Any] = Field(default_factory=dict)


class LegacyEvaluationInput(BaseModel):
    """Root schema for the **old** single-JSON evaluation input format.

    Renamed from ``EvaluationInput`` to ``LegacyEvaluationInput`` to avoid
    clashing with the new composite model above.
    """

    eval_id: str = Field(..., description="Unique identifier for this evaluation run")
    prompt: str = Field(..., description="The prompt sent to the LLM")
    response: str = Field(..., description="The LLM-generated email content")
    model: str = Field(..., description="Model that generated the response")
    customer_company: Optional[str] = None
    target_persona: Optional[TargetPersona] = None
    campaign: Optional[CampaignInfo] = None
    timestamp: Optional[datetime] = None
    metadata: Optional[InputMetadata] = None
