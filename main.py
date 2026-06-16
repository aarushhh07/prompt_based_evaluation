"""Main evaluation pipeline.

Orchestrates: prompts.json + {id}_response.csv → criteria extraction →
format checking → results.

Usage:
    python pipeline.py --prompts sample_data/prompts.json
    python pipeline.py --prompts sample_data/prompts.json --prompt-id 913 --limit 5
    python pipeline.py --prompts sample_data/prompts.json --prompt-id 480 -o results.json
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import logging
import sys
from pathlib import Path
from typing import Optional

from config import PipelineConfig, load_config
from evaluators.format_checker import FormatChecker
from extractors.criteria_extractor import CriteriaExtractor
from models.criteria_schema import ExtractedCriteria
from models.llmasajudge_schema import MetricResult
from models.input_schema import EvaluationInput, PromptConfig, ResponseRecord
from extractors.llm_as_a_judge import DeepEvalProviderAdapter
from deepeval.metrics import GEval
from deepeval.test_case import LLMTestCase, LLMTestCaseParams
from evaluators.amalgamator import amalgamate, to_dicts, get_create_table_sql
logger = logging.getLogger(__name__)


# ── Helpers ──────────────────────────────────────────────────────────────────


def _prompt_text(input_data: EvaluationInput) -> str:
    """Extract the plain-text prompt from the nested PromptConfig."""
    return input_data.prompt_config.evaluation_config_payload.instruction.goal


def _response_text(input_data: EvaluationInput) -> str:
    """Build a plain-text email string from a ResponseRecord.

    Format: ``Subject: <subject>\\n\\n<body>``
    """
    subject = input_data.response.subject or ""
    body = input_data.response.body or ""
    return f"Subject: {subject}\n\n{body}"


# ── Core evaluation ─────────────────────────────────────────────────────────


async def evaluate_single(
    input_data: EvaluationInput,
    config: PipelineConfig,
) -> dict:
    """Run the full evaluation pipeline on a single input record.

    Returns the EvaluationResult serialised as a dict.
    """
    prompt = _prompt_text(input_data)
    response = _response_text(input_data)

    # Step 1 — Extract criteria from the prompt
    if config.offline_mode:
        logger.info("[%s] Offline mode — using empty criteria", input_data.eval_id)
        criteria = ExtractedCriteria()
    else:
        extractor = CriteriaExtractor(config=config.llm)
        logger.info(
            "[%s] Extracting criteria via %s/%s …",
            input_data.eval_id,
            config.llm.provider,
            config.llm.model,
        )
        criteria = await extractor.extract(prompt)
        logger.info(
            "[%s] Extracted criteria: %s",
            input_data.eval_id,
            criteria.model_dump_json(indent=2),
        )

    # Step 2 — Run format checks
    checker = FormatChecker()
    result = checker.evaluate(
        eval_id=input_data.eval_id,
        response=response,
        criteria=criteria,
    )

    return result.model_dump()

async def evaluate_llm_as_a_judge_single(
    input_data: EvaluationInput,
    config: PipelineConfig,
) -> dict:
    prompt = _prompt_text(input_data)
    response = _response_text(input_data)

    if config.offline_mode:
        logger.info("[%s] Offline mode — using empty criteria", input_data.eval_id)
        return MetricResult.empty(metric_name="Tone").to_dict()

    test_case = LLMTestCase(input=prompt, actual_output=response)
    provider_judge = DeepEvalProviderAdapter(config=config.llm)

    tone = input_data.prompt_config.evaluation_config_payload.instruction.tone
    tone_judge = GEval(
        name="Tone",
        model=provider_judge,
        evaluation_steps=[
            f"Check if the tone is {tone}.",
            f"Check if it's {tone} without being over the top.",
            "Penalize if it reads like a cold, copy-paste sales email.",
            "Penalize if it's overly effusive or uses superlatives.",
            "Do not check for formatting, word limit, strictly focus on tone evaluation.",
        ],
        evaluation_params=[LLMTestCaseParams.INPUT, LLMTestCaseParams.ACTUAL_OUTPUT]
    )

    await tone_judge.a_measure(test_case=test_case)
    return MetricResult.from_geval(tone_judge).to_dict()

async def evaluate_batch(
    inputs: list[EvaluationInput],
    config: PipelineConfig,
) -> list[dict]:
    """Evaluate a list of inputs (sequentially for now)."""
    results = []
    for inp in inputs:
        res = await evaluate_single(inp, config)
        results.append(res)
    return results


async def evaluate_llm_as_a_judge_batch(
        inputs: list[EvaluationInput],
    config: PipelineConfig,
) -> list[dict]:
    """Evaluate LLM-as-a-Judge """
    results =[]
    for inp in inputs:
        res = await evaluate_llm_as_a_judge_single(inp, config)
        results.append(res)
    return results

# ── Data loading ─────────────────────────────────────────────────────────────


def load_prompt_configs(path: Path) -> list[PromptConfig]:
    """Load all prompt configs from a ``prompts.json`` file."""
    raw = json.loads(path.read_text())
    if not isinstance(raw, list):
        raw = [raw]
    return [PromptConfig.model_validate(item) for item in raw]


def load_responses(csv_path: Path) -> list[ResponseRecord]:
    """Load response records from a ``{id}_response.csv`` file.

    Each row has a single column ``prompt_response`` whose value is a
    JSON-encoded string with keys ``body``, ``subject``, ``reasoning``,
    and optionally ``__evaluation``.
    """
    records: list[ResponseRecord] = []
    with csv_path.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            raw_json = row["prompt_response"]
            data = json.loads(raw_json)
            records.append(ResponseRecord.model_validate(data))
    return records


def build_evaluation_inputs(
    prompt_cfg: PromptConfig,
    responses: list[ResponseRecord],
    limit: Optional[int] = None,
) -> list[EvaluationInput]:
    """Pair a single PromptConfig with its response rows.

    Each pair gets a deterministic ``eval_id`` of the form
    ``{prompt_id}-{row_index}``.
    """
    if limit is not None:
        responses = responses[:limit]

    inputs: list[EvaluationInput] = []
    for idx, resp in enumerate(responses):
        eval_id = f"{prompt_cfg.id}-{idx}"
        inputs.append(
            EvaluationInput(
                eval_id=eval_id,
                prompt_config=prompt_cfg,
                response=resp,
            )
        )
    return inputs


def load_inputs_for_prompts(
    prompts_path: Path,
    responses_path_directory: Path,
    prompt_id: Optional[str] = None,
    limit: Optional[int] = None,
) -> list[EvaluationInput]:
    """High-level loader: prompts.json → CSV responses → EvaluationInput list.

    Parameters
    ----------
    prompts_path:
        Path to the ``prompts.json`` file.
    prompt_id:
        If given, process only the prompt config with this id.
    limit:
        If given, cap the number of response rows per prompt.
    """
    configs = load_prompt_configs(prompts_path)

    if prompt_id is not None:
        configs = [c for c in configs if c.id == prompt_id]
        if not configs:
            raise ValueError(
                f"Prompt id '{prompt_id}' not found in {prompts_path}"
            )

    all_inputs: list[EvaluationInput] = []
    for cfg in configs:
        csv_path = responses_path_directory/ f"{cfg.id}_response.csv"
        if not csv_path.exists():
            logger.warning(
                "Response CSV not found for prompt %s: %s — skipping",
                cfg.id,
                csv_path,
            )
            continue
        responses = load_responses(csv_path)
        logger.info(
            "Loaded %d response(s) for prompt %s from %s",
            len(responses),
            cfg.id,
            csv_path.name,
        )
        inputs = build_evaluation_inputs(cfg, responses, limit=limit)
        all_inputs.extend(inputs)

    return all_inputs


# ── CLI ──────────────────────────────────────────────────────────────────────


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Email LLM Response Evaluation Pipeline (Layer 1)",
    )
    parser.add_argument(
        "--extractor", "-e",
        action="store_true",
        help="Toggle usage of extractor",
    )
    parser.add_argument(
        "--llmasajudge", "-l",
        action="store_true",
        help="Toggle usage of LLM as a Judge",
    )
    parser.add_argument(
        "--provider_llmasajudge",
        default=None,
        help="Provider name to use for LLM as a Judge.",
    )
    parser.add_argument(
        "--model_llmasajudge",
        default=None,
        help="Model name to use for LLM as a Judge.",
    )
    parser.add_argument(
        "--prompts",
        type=Path,
        required=True,
        help="Path to the prompts.json file.",
    )
    parser.add_argument(
        "--prompt-id",
        default=None,
        help="Process only the prompt config with this id (default: all).",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Max number of responses to evaluate per prompt (default: all).",
    )
    parser.add_argument(
        "--provider",
        default=None,
        help="LLM provider for criteria extraction (openai, gemini, anthropic, ollama).",
    )
    parser.add_argument(
        "--model",
        default=None,
        help="Model name to use for criteria extraction.",
    )
    parser.add_argument(
        "--offline",
        action="store_true",
        help="Skip LLM extraction and run with empty criteria.",
    )
    parser.add_argument(
        "--output", "-o",
        type=Path,
        default=None,
        help="Write results JSON to this file (default: stdout).",
    )
    parser.add_argument(
        "--responses",
        type=Path,
        default=None,
        required=True,
        help="Path for the .csv responses file"
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Enable debug logging.",
    )
    parser.add_argument(
        "--sql-table",
        type=str,
        default=None,
        metavar="TABLE_NAME",
        help="Print CREATE TABLE SQL for the given table name and exit.",
    )
    parser.add_argument(
        "--layer1-weight",
        type=float,
        default=0.4,
        help="Weight for Layer 1 in composite score (default: 0.4).",
    )
    parser.add_argument(
        "--layer2-weight",
        type=float,
        default=0.6,
        help="Weight for Layer 2 in composite score (default: 0.6).",
    )
    parser.add_argument(
        "--cloud-sql",
        type=str,
        default=None,
        metavar="INSTANCE_CONNECTION_NAME",
        help="Write results to Cloud SQL. Format: <PROJECT>:<REGION>:<INSTANCE>",
    )
    parser.add_argument(
        "--db",
        type=str,
        default="evaluation_db",
        help="Cloud SQL database name (default: evaluation_db).",
    )
    parser.add_argument(
        "--db-user",
        type=str,
        default="postgres",
        help="Cloud SQL database user (default: postgres).",
    )
    parser.add_argument(
        "--db-password",
        type=str,
        default=None,
        help="Cloud SQL database password. If omitted, uses IAM authentication.",
    )
    parser.add_argument(
        "--db-table",
        type=str,
        default="evaluation_results",
        help="Cloud SQL table name (default: evaluation_results).",
    )
    return parser


def _build_config(provider=None, model=None, offline=False) -> PipelineConfig:
    """Helper to avoid repeating override logic."""
    overrides = {}
    if provider:
        overrides["provider"] = provider
    if model:
        overrides["model"] = model
    config = load_config(**overrides)
    config.offline_mode = offline
    return config


def main():
    parser = build_arg_parser()
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    )

    # ── Configs ───────────────────────────────────────────────────────────
    judge_config = _build_config(
        provider=args.provider_llmasajudge if args.llmasajudge else None,
        model=args.model_llmasajudge    if args.llmasajudge else None,
        offline=args.offline,
    ) if args.llmasajudge else None

    extractor_config = _build_config(
        provider=args.provider if args.extractor else None,
        model=args.model       if args.extractor else None,
        offline=args.offline,
    ) if args.extractor else None

    # ── Inputs (loaded once, shared across all pipelines) ─────────────────
    inputs = load_inputs_for_prompts(
        prompts_path=args.prompts,
        prompt_id=args.prompt_id,
        limit=args.limit,
        responses_path_directory=args.responses,
    )
    logger.info("Loaded %d evaluation input(s) from %s", len(inputs), args.prompts)

    if not inputs:
        logger.warning("No evaluation inputs to process — exiting.")
        sys.exit(0)

    # ── SQL table helper ──────────────────────────────────────────────────
    if args.sql_table:
        print(get_create_table_sql(args.sql_table))
        sys.exit(0)

    # ── Pipelines ─────────────────────────────────────────────────────────
    extractor_results = None
    judge_results = None

    if args.llmasajudge:
        logger.info("Running LLM-as-a-judge pipeline")
        judge_results = asyncio.run(
            evaluate_llm_as_a_judge_batch(inputs, judge_config)
        )

    if args.extractor:
        logger.info("Running extractor pipeline")
        extractor_results = asyncio.run(
            evaluate_batch(inputs, extractor_config)
        )

    # ── Amalgamate ────────────────────────────────────────────────────────
    weights = {
        "layer1_format": args.layer1_weight,
        "layer2_judge": args.layer2_weight,
    }
    amalgamated = amalgamate(
        inputs=inputs,
        extractor_results=extractor_results,
        judge_results=judge_results,
        weights=weights,
    )

    # ── Output ────────────────────────────────────────────────────────────
    amalgamated_dicts = to_dicts(amalgamated)

    output = {
        "amalgamated": amalgamated_dicts,
        "raw": {},
    }
    if extractor_results:
        output["raw"]["extractor"] = extractor_results
    if judge_results:
        output["raw"]["llm_as_a_judge"] = judge_results

    output_json = json.dumps(output, indent=2)

    if args.output:
        args.output.write_text(output_json)
        logger.info("Results written to %s", args.output)
    else:
        print(output_json)

    # ── Cloud SQL write ──────────────────────────────────────────────────
    if args.cloud_sql:
        from writers.cloud_sql_writer import write_rows

        count = write_rows(
            rows=amalgamated_dicts,
            instance_connection_name=args.cloud_sql,
            db_name=args.db,
            table_name=args.db_table,
            db_user=args.db_user,
            db_password=args.db_password,
        )
        logger.info("Wrote %d row(s) to Cloud SQL", count)


if __name__ == "__main__":
    main()
