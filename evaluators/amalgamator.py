"""Amalgamator — combines Layer 1 and Layer 2 results into a final score.

Produces flat, SQL-ready rows that can be directly inserted into a database
table via any SQL connector (PostgreSQL, MySQL, BigQuery, Cloud SQL, etc.).

Each row represents one evaluated response and contains:
- Identity columns (eval_id, prompt_id, prompt_name, response_index)
- Layer 1 scores (format checker pass/fail counts + per-check detail)
- Layer 2 scores (LLM-as-a-Judge tone score + reasoning)
- Composite score (weighted average of both layers)
- Metadata (timestamp, existing platform score if available)
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


# ── Default weights for composite scoring ────────────────────────────────────

DEFAULT_WEIGHTS = {
    "layer1_format": 0.4,
    "layer2_judge": 0.6,
}


# ── SQL-ready output schema ─────────────────────────────────────────────────


class AmalgamatedRow(BaseModel):
    """A single flat row ready for SQL insertion.

    Every field is a primitive type (str, float, int, bool, or None)
    so it maps directly to a SQL column with no nested objects.
    """

    # ── Identity ──
    eval_id: str = Field(..., description="Unique eval identifier, e.g. '913-0'")
    prompt_id: str = Field(..., description="Prompt config id, e.g. '913'")
    prompt_name: str = Field("", description="Human-readable prompt name")
    response_index: int = Field(0, description="Row index within the response CSV")

    # ── Response content ──
    response_subject: Optional[str] = Field(None, description="Email subject line")
    response_body: Optional[str] = Field(None, description="Email body text")

    # ── Layer 1: Format Checker ──
    layer1_score: Optional[float] = Field(None, description="Format checker score (0.0-1.0)")
    layer1_total_checks: Optional[int] = Field(None, description="Total format checks run")
    layer1_passed: Optional[int] = Field(None, description="Checks that passed")
    layer1_failed: Optional[int] = Field(None, description="Checks that failed")
    layer1_checks_json: Optional[str] = Field(
        None, description="JSON string of detailed per-check results"
    )

    # ── Layer 2: LLM-as-a-Judge ──
    layer2_tone_score: Optional[float] = Field(None, description="Tone alignment score (0.0-1.0)")
    layer2_tone_reason: Optional[str] = Field(None, description="Judge reasoning for tone score")

    # ── Composite ──
    composite_score: Optional[float] = Field(None, description="Weighted composite score (0.0-1.0)")
    layer1_weight: float = Field(DEFAULT_WEIGHTS["layer1_format"], description="Weight applied to Layer 1")
    layer2_weight: float = Field(DEFAULT_WEIGHTS["layer2_judge"], description="Weight applied to Layer 2")

    # ── Existing platform evaluation (if present) ──
    platform_score: Optional[int] = Field(None, description="Existing platform overallScore (0-100)")
    platform_rating: Optional[str] = Field(None, description="Existing platform qualityRating")

    # ── Metadata ──
    evaluated_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(),
        description="ISO timestamp of when this evaluation was produced",
    )


# ── Core amalgamation logic ─────────────────────────────────────────────────


def amalgamate(
    inputs: list,
    extractor_results: Optional[list[dict]] = None,
    judge_results: Optional[list[dict]] = None,
    weights: Optional[dict[str, float]] = None,
) -> list[AmalgamatedRow]:
    """Combine Layer 1 and Layer 2 results into flat SQL-ready rows.

    Parameters
    ----------
    inputs:
        List of EvaluationInput objects (used for identity + response content).
    extractor_results:
        List of Layer 1 dicts (from evaluate_batch). Same length/order as inputs.
        None if Layer 1 was not run.
    judge_results:
        List of Layer 2 dicts (from evaluate_llm_as_a_judge_batch). Same
        length/order as inputs. None if Layer 2 was not run.
    weights:
        Optional override for layer weights. Keys: "layer1_format", "layer2_judge".

    Returns
    -------
    List of AmalgamatedRow objects, one per input.
    """
    w = {**DEFAULT_WEIGHTS, **(weights or {})}
    total_weight = sum(w.values())
    # Normalize weights so they sum to 1.0
    w = {k: v / total_weight for k, v in w.items()}

    rows: list[AmalgamatedRow] = []
    now = datetime.now(timezone.utc).isoformat()

    for idx, inp in enumerate(inputs):
        # Parse eval_id to extract prompt_id and response_index
        parts = inp.eval_id.rsplit("-", 1)
        prompt_id = parts[0] if len(parts) == 2 else inp.eval_id
        resp_idx = int(parts[1]) if len(parts) == 2 else idx

        # ── Layer 1 ──
        l1_score = None
        l1_total = None
        l1_passed = None
        l1_failed = None
        l1_checks_json = None

        if extractor_results and idx < len(extractor_results):
            ext = extractor_results[idx]
            summary = ext.get("summary", {})
            l1_score = summary.get("score")
            l1_total = summary.get("total_checks")
            l1_passed = summary.get("passed")
            l1_failed = summary.get("failed")
            l1_checks_json = json.dumps(ext.get("checks", {}))

        # ── Layer 2 ──
        l2_tone_score = None
        l2_tone_reason = None

        if judge_results and idx < len(judge_results):
            judge = judge_results[idx]
            l2_tone_score = judge.get("score")
            l2_tone_reason = judge.get("reason")

        # ── Composite score ──
        composite = _compute_composite(l1_score, l2_tone_score, w)

        # ── Platform evaluation (from response's __evaluation) ──
        platform_score = None
        platform_rating = None
        if inp.response.evaluation:
            platform_score = inp.response.evaluation.overallScore
            platform_rating = inp.response.evaluation.qualityRating

        rows.append(
            AmalgamatedRow(
                eval_id=inp.eval_id,
                prompt_id=prompt_id,
                prompt_name=inp.prompt_config.name,
                response_index=resp_idx,
                response_subject=inp.response.subject,
                response_body=inp.response.body,
                layer1_score=l1_score,
                layer1_total_checks=l1_total,
                layer1_passed=l1_passed,
                layer1_failed=l1_failed,
                layer1_checks_json=l1_checks_json,
                layer2_tone_score=l2_tone_score,
                layer2_tone_reason=l2_tone_reason,
                composite_score=composite,
                layer1_weight=w["layer1_format"],
                layer2_weight=w["layer2_judge"],
                platform_score=platform_score,
                platform_rating=platform_rating,
                evaluated_at=now,
            )
        )

    logger.info("Amalgamated %d evaluation rows", len(rows))
    return rows


def _compute_composite(
    l1_score: Optional[float],
    l2_score: Optional[float],
    weights: dict[str, float],
) -> Optional[float]:
    """Compute weighted composite, handling cases where one layer is missing."""
    scores = {}
    if l1_score is not None:
        scores["layer1_format"] = l1_score
    if l2_score is not None:
        scores["layer2_judge"] = l2_score

    if not scores:
        return None

    # Re-normalize weights to only active layers
    active_weight = sum(weights[k] for k in scores)
    if active_weight == 0:
        return None

    composite = sum(scores[k] * (weights[k] / active_weight) for k in scores)
    return round(composite, 4)


# ── Output helpers ───────────────────────────────────────────────────────────


def to_dicts(rows: list[AmalgamatedRow]) -> list[dict]:
    """Convert rows to plain dicts (for JSON serialization)."""
    return [row.model_dump() for row in rows]


def to_sql_rows(rows: list[AmalgamatedRow]) -> list[dict]:
    """Convert rows to SQL-insertion-ready dicts.

    Identical to to_dicts() but explicitly provided as a named entry point
    so downstream code can call `amalgamator.to_sql_rows(rows)` for clarity.
    Each dict maps column_name → value with only primitive types.
    """
    return [row.model_dump() for row in rows]


def get_create_table_sql(table_name: str = "evaluation_results") -> str:
    """Return a CREATE TABLE statement matching the AmalgamatedRow schema.

    Works with PostgreSQL, Cloud SQL, and most SQL dialects.
    Adjust types as needed for your database.
    """
    return f"""
CREATE TABLE IF NOT EXISTS {table_name} (
    eval_id             VARCHAR(64)   PRIMARY KEY,
    prompt_id           VARCHAR(64)   NOT NULL,
    prompt_name         VARCHAR(256),
    response_index      INTEGER       NOT NULL,

    response_subject    TEXT,
    response_body       TEXT,

    layer1_score        FLOAT,
    layer1_total_checks INTEGER,
    layer1_passed       INTEGER,
    layer1_failed       INTEGER,
    layer1_checks_json  TEXT,

    layer2_tone_score   FLOAT,
    layer2_tone_reason  TEXT,

    composite_score     FLOAT,
    layer1_weight       FLOAT         NOT NULL DEFAULT 0.4,
    layer2_weight       FLOAT         NOT NULL DEFAULT 0.6,

    platform_score      INTEGER,
    platform_rating     VARCHAR(32),

    evaluated_at        TIMESTAMP     NOT NULL
);
""".strip()
