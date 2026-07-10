"""SQL-based data loader for the evaluation pipeline.

Loads PromptConfig and ResponseRecord from Cloud SQL tables
instead of JSON files and CSVs.

Table schemas:
    - prompts: stores prompt configurations
    - responses: stores LLM-generated responses linked to prompts
"""

from __future__ import annotations

import json
import logging
from typing import Optional

from models.input_schema import (
    EvaluationConfigPayload,
    EvaluationInput,
    ExistingEvaluation,
    PromptConfig,
    PromptInstruction,
    ResponseRecord,
)

logger = logging.getLogger(__name__)


# ── Table creation SQL ───────────────────────────────────────────────────────


PROMPTS_TABLE_SQL = """\
CREATE TABLE IF NOT EXISTS prompts (
    id                          VARCHAR(64) PRIMARY KEY,
    name                        VARCHAR(255) NOT NULL,
    tenant_id                   VARCHAR(128),
    version                     VARCHAR(32),
    input_attributes            TEXT,
    evaluation_config_type      VARCHAR(64),
    goal                        TEXT NOT NULL,
    tone                        VARCHAR(64),
    type                        VARCHAR(64),
    evaluation_guideline        TEXT,
    minimum_quality_score       INTEGER,
    max_regeneration_attempts   INTEGER,
    value_type                  VARCHAR(64),
    target_object               VARCHAR(64),
    created_at                  TIMESTAMP,
    updated_at                  TIMESTAMP,
    created_by                  VARCHAR(128),
    updated_by                  VARCHAR(128),
    status                      VARCHAR(32) DEFAULT 'active'
);
"""

RESPONSES_TABLE_SQL = """\
CREATE TABLE IF NOT EXISTS responses (
    id                          SERIAL PRIMARY KEY,
    prompt_id                   VARCHAR(64) NOT NULL REFERENCES prompts(id),
    body                        TEXT,
    subject                     TEXT,
    reasoning                   TEXT,
    eval_stop_reason            VARCHAR(64),
    eval_key_strengths          TEXT,
    eval_overall_score          INTEGER,
    eval_quality_rating         VARCHAR(32),
    eval_is_regeneratable       BOOLEAN,
    eval_recommendations        TEXT,
    eval_areas_for_improvement  TEXT,
    eval_regeneration_attempts  INTEGER,
    eval_non_regenerable_reasons TEXT,
    created_at                  TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_responses_prompt_id ON responses(prompt_id);
"""


def get_input_tables_sql() -> str:
    """Return the CREATE TABLE SQL for both input tables."""
    return PROMPTS_TABLE_SQL + "\n" + RESPONSES_TABLE_SQL


# ── Database connection ──────────────────────────────────────────────────────


def _create_pool(instance_connection_name: str, db_name: str,
                 db_user: str = "postgres", db_password: str | None = None):
    """Create a SQLAlchemy connection pool using Cloud SQL connector."""
    import sqlalchemy
    from google.cloud.sql.connector import Connector

    connector = Connector()

    def getconn():
        kwargs = {
            "instance_connection_string": instance_connection_name,
            "driver": "pg8000",
            "db": db_name,
            "user": db_user,
        }
        if db_password:
            kwargs["password"] = db_password
        else:
            kwargs["enable_iam_auth"] = True
        return connector.connect(**kwargs)

    return sqlalchemy.create_engine(
        "postgresql+pg8000://",
        creator=getconn,
        pool_size=3,
        max_overflow=1,
        pool_timeout=30,
        pool_recycle=1800,
    )


# ── Data loading ─────────────────────────────────────────────────────────────


def _row_to_prompt_config(row: dict) -> PromptConfig:
    """Convert a SQL row dict into a PromptConfig model."""
    instruction = PromptInstruction(
        goal=row["goal"],
        tone=row.get("tone"),
        type=row.get("type"),
        evaluationGuideline=row.get("evaluation_guideline"),
        minimumQualityScore=row.get("minimum_quality_score"),
        maxRegenerationAttempts=row.get("max_regeneration_attempts"),
    )
    payload = EvaluationConfigPayload(instruction=instruction)

    return PromptConfig(
        id=row["id"],
        name=row["name"],
        tenant_id=row.get("tenant_id"),
        version=row.get("version"),
        input_attributes=row.get("input_attributes"),
        evaluation_config_type=row.get("evaluation_config_type"),
        evaluation_config_payload=payload,
        value_type=row.get("value_type"),
        target_object=row.get("target_object"),
        created_at=row.get("created_at"),
        updated_at=row.get("updated_at"),
        created_by=row.get("created_by"),
        updated_by=row.get("updated_by"),
        status=row.get("status"),
    )


def _parse_json_list(val: str | None) -> list[str] | None:
    """Parse a JSON array string, return None if empty/null."""
    if not val:
        return None
    try:
        return json.loads(val)
    except (json.JSONDecodeError, TypeError):
        return None


def _row_to_response_record(row: dict) -> ResponseRecord:
    """Convert a SQL row dict into a ResponseRecord model."""
    evaluation = None
    if row.get("eval_overall_score") is not None:
        evaluation = ExistingEvaluation(
            stopReason=row.get("eval_stop_reason"),
            keyStrengths=_parse_json_list(row.get("eval_key_strengths")),
            overallScore=row.get("eval_overall_score"),
            qualityRating=row.get("eval_quality_rating"),
            isRegeneratable=row.get("eval_is_regeneratable"),
            recommendations=_parse_json_list(row.get("eval_recommendations")),
            areasForImprovement=_parse_json_list(row.get("eval_areas_for_improvement")),
            regenerationAttempts=row.get("eval_regeneration_attempts"),
            nonRegenerableReasons=_parse_json_list(row.get("eval_non_regenerable_reasons")),
        )

    return ResponseRecord(
        body=row.get("body"),
        subject=row.get("subject"),
        reasoning=row.get("reasoning"),
        **{"__evaluation": evaluation} if evaluation else {},
    )


def load_prompts_from_sql(
    pool,
    prompt_id: str | None = None,
) -> list[PromptConfig]:
    """Load prompt configs from the prompts table."""
    import sqlalchemy

    query = "SELECT * FROM prompts WHERE status = 'active'"
    params = {}
    if prompt_id:
        query += " AND id = :prompt_id"
        params["prompt_id"] = prompt_id

    with pool.connect() as conn:
        result = conn.execute(sqlalchemy.text(query), params)
        columns = list(result.keys())
        rows = [dict(zip(columns, row)) for row in result.fetchall()]

    configs = [_row_to_prompt_config(row) for row in rows]
    logger.info("Loaded %d prompt config(s) from SQL", len(configs))
    return configs


def load_responses_from_sql(
    pool,
    prompt_id: str,
    limit: int | None = None,
) -> list[ResponseRecord]:
    """Load response records for a specific prompt from the responses table."""
    import sqlalchemy

    query = "SELECT * FROM responses WHERE prompt_id = :prompt_id ORDER BY id"
    params: dict = {"prompt_id": prompt_id}
    if limit:
        query += " LIMIT :limit"
        params["limit"] = limit

    with pool.connect() as conn:
        result = conn.execute(sqlalchemy.text(query), params)
        columns = list(result.keys())
        rows = [dict(zip(columns, row)) for row in result.fetchall()]

    records = [_row_to_response_record(row) for row in rows]
    logger.info("Loaded %d response(s) for prompt %s from SQL", len(records), prompt_id)
    return records


def load_inputs_from_sql(
    instance_connection_name: str,
    db_name: str,
    db_user: str = "postgres",
    db_password: str | None = None,
    prompt_id: str | None = None,
    limit: int | None = None,
) -> list[EvaluationInput]:
    """High-level loader: SQL tables → EvaluationInput list.

    Parameters
    ----------
    instance_connection_name:
        Cloud SQL instance connection string (PROJECT:REGION:INSTANCE).
    db_name:
        PostgreSQL database name.
    db_user:
        Database user (default: postgres).
    db_password:
        Database password (omit for IAM auth).
    prompt_id:
        If given, process only this prompt.
    limit:
        Max response rows per prompt.

    Returns
    -------
    List of EvaluationInput objects ready for the pipeline.
    """
    pool = _create_pool(instance_connection_name, db_name, db_user, db_password)

    try:
        configs = load_prompts_from_sql(pool, prompt_id=prompt_id)

        if prompt_id and not configs:
            raise ValueError(f"Prompt id '{prompt_id}' not found in SQL")

        all_inputs: list[EvaluationInput] = []
        for cfg in configs:
            responses = load_responses_from_sql(pool, cfg.id, limit=limit)

            if not responses:
                logger.warning("No responses found for prompt %s — skipping", cfg.id)
                continue

            for idx, resp in enumerate(responses):
                eval_id = f"{cfg.id}-{idx}"
                all_inputs.append(
                    EvaluationInput(
                        eval_id=eval_id,
                        prompt_config=cfg,
                        response=resp,
                    )
                )

        logger.info("Total: %d evaluation input(s) from SQL", len(all_inputs))
        return all_inputs
    finally:
        pool.dispose()
