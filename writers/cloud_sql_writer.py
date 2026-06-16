"""Cloud SQL (PostgreSQL) writer for evaluation results.

Uses the Cloud SQL Python Connector for secure, IAM-authenticated connections
without needing to whitelist IPs or manage SSL certificates.

Usage from the pipeline:
    python3 main.py --prompts ... --responses ... --extractor --llmasajudge \
        --cloud-sql <PROJECT>:<REGION>:<INSTANCE> --db evaluation_db

Prerequisites:
    pip install cloud-sql-python-connector[pg8000] pg8000
    gcloud auth application-default login
"""

from __future__ import annotations

import json
import logging
from typing import Optional

logger = logging.getLogger(__name__)


def _get_connector():
    """Lazily import and return a Cloud SQL Connector instance."""
    try:
        from google.cloud.sql.connector import Connector
    except ImportError:
        raise ImportError(
            "Install the Cloud SQL connector: "
            "pip install 'cloud-sql-python-connector[pg8000]' pg8000"
        )
    return Connector()


def _get_connection(
    instance_connection_name: str,
    db_name: str,
    db_user: str = "postgres",
    db_password: Optional[str] = None,
):
    """Create a connection to Cloud SQL PostgreSQL via the connector.

    Parameters
    ----------
    instance_connection_name:
        Format: ``<PROJECT_ID>:<REGION>:<INSTANCE_NAME>``
    db_name:
        Database name inside the instance.
    db_user:
        PostgreSQL user (default: postgres).
    db_password:
        Password for the user. If None, uses IAM authentication.
    """
    connector = _get_connector()

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

    return getconn()


def ensure_table(conn, table_name: str = "evaluation_results") -> None:
    """Create the evaluation results table if it doesn't exist."""
    create_sql = f"""
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
    """
    cursor = conn.cursor()
    cursor.execute(create_sql)
    conn.commit()
    logger.info("Ensured table '%s' exists", table_name)


def write_rows(
    rows: list[dict],
    instance_connection_name: str,
    db_name: str,
    table_name: str = "evaluation_results",
    db_user: str = "postgres",
    db_password: Optional[str] = None,
) -> int:
    """Write amalgamated rows to Cloud SQL PostgreSQL.

    Parameters
    ----------
    rows:
        List of flat dicts from ``amalgamator.to_dicts()``.
    instance_connection_name:
        Cloud SQL instance connection string ``<PROJECT>:<REGION>:<INSTANCE>``.
    db_name:
        PostgreSQL database name.
    table_name:
        Target table name (default: evaluation_results).
    db_user:
        PostgreSQL user (default: postgres).
    db_password:
        Password. If None, uses IAM authentication.

    Returns
    -------
    Number of rows inserted.
    """
    conn = _get_connection(
        instance_connection_name=instance_connection_name,
        db_name=db_name,
        db_user=db_user,
        db_password=db_password,
    )

    try:
        ensure_table(conn, table_name)

        columns = [
            "eval_id", "prompt_id", "prompt_name", "response_index",
            "response_subject", "response_body",
            "layer1_score", "layer1_total_checks", "layer1_passed",
            "layer1_failed", "layer1_checks_json",
            "layer2_tone_score", "layer2_tone_reason",
            "composite_score", "layer1_weight", "layer2_weight",
            "platform_score", "platform_rating",
            "evaluated_at",
        ]

        placeholders = ", ".join(["%s"] * len(columns))
        col_names = ", ".join(columns)

        # UPSERT: insert or update on conflict
        update_set = ", ".join(
            f"{col} = EXCLUDED.{col}" for col in columns if col != "eval_id"
        )
        insert_sql = f"""
        INSERT INTO {table_name} ({col_names})
        VALUES ({placeholders})
        ON CONFLICT (eval_id) DO UPDATE SET {update_set};
        """

        cursor = conn.cursor()
        inserted = 0

        for row in rows:
            values = tuple(row.get(col) for col in columns)
            cursor.execute(insert_sql, values)
            inserted += 1

        conn.commit()
        logger.info(
            "Wrote %d row(s) to %s.%s on %s",
            inserted, db_name, table_name, instance_connection_name,
        )
        return inserted

    finally:
        conn.close()
