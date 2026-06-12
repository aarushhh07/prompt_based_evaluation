"""Pydantic models for extracted evaluation criteria and check results."""

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


# ── Extracted criteria (output of the LLM criteria extractor) ───────────

class LimitRange(BaseModel):
    """A min/max range for a numeric limit."""

    min: Optional[int] = None
    max: Optional[int] = None


class SubjectLineCriteria(BaseModel):
    """Criteria specific to the email subject line."""

    required: bool = False
    max_characters: Optional[int] = None


class ExtractedCriteria(BaseModel):
    """Structured formatting criteria extracted from the prompt by the LLM.

    Every field is optional — the LLM only populates fields that are
    explicitly or implicitly specified in the prompt.
    """

    word_limit: Optional[LimitRange] = None
    character_limit: Optional[LimitRange] = None
    line_limit: Optional[LimitRange] = None
    paragraph_limit: Optional[LimitRange] = None
    subject_line: Optional[SubjectLineCriteria] = None
    banned_words: List[str] = Field(default_factory=list)
    required_elements: List[str] = Field(default_factory=list)
    tone: Optional[str] = None

    # Catch-all for criteria the LLM finds that we haven't modeled yet
    additional: Dict[str, Any] = Field(default_factory=dict)


# ── Evaluation results ─────────────────────────────────────────────────

class CheckResult(BaseModel):
    """Result of a single formatting check."""

    passed: bool
    actual: Optional[Any] = None
    limit: Optional[Any] = None
    violations: Optional[List[str]] = None
    detail: str = ""


class ResultSummary(BaseModel):
    """Aggregate summary across all checks."""

    total_checks: int = 0
    passed: int = 0
    failed: int = 0
    score: float = Field(0.0, ge=0.0, le=1.0)


class EvaluationResult(BaseModel):
    """Complete evaluation result for a single input."""

    eval_id: str
    checks: Dict[str, CheckResult] = Field(default_factory=dict)
    summary: ResultSummary = Field(default_factory=ResultSummary)
    criteria_used: Optional[ExtractedCriteria] = None
