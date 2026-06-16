# Email LLM Response Evaluation Pipeline

A modular Python pipeline that evaluates LLM-generated email content using a layered approach: deterministic format checks (Layer 1), LLM-as-a-Judge quality scoring (Layer 2), and a weighted amalgamator that produces flat, SQL-ready output.

## Architecture

```
prompts.json ──┐
               ├──► Pipeline ──► Layer 1: Format Checker (rule-based) ──|
{id}_response.csv ─┘            └──► Layer 2: LLM-as-a-Judge (GEval) ───┤
                                                                        ▼
                                                                  Amalgamator
                                                                  (weighted composite)
                                                                        ▼
                                                              SQL-ready flat rows
```

### Layers

| Layer | Name | Technique | What It Does |
|---|---|---|---|
| **1** | Format Checker | Rule-based | Extracts formatting rules from the prompt via Gemini, then runs deterministic checks (character limits, banned words, subject line, required elements) |
| **2** | LLM-as-a-Judge | GEval (DeepEval) | Uses a judge LLM to score the email on qualitative criteria like tone alignment, with chain-of-thought evaluation steps |
| — | Amalgamator | Weighted average | Combines both layer scores into a composite score and flattens results into SQL-ready rows |

## Prerequisites

1. **Google Cloud project** with Vertex AI API enabled
2. **Application Default Credentials** configured (see [GCP Setup](#gcp-setup))
3. Python 3.11+

## GCP Setup

### 1. Project & APIs

1. Create or select a Google Cloud project
2. Enable the **Vertex AI API**:
   ```bash
   gcloud services enable aiplatform.googleapis.com --project=<YOUR_PROJECT_ID>
   ```

### 2. Authentication (Local Development)

The pipeline uses **Application Default Credentials (ADC)** — no API keys in code:

```bash
# Authenticate your user account
gcloud auth application-default login

# Set your default project
gcloud config set project <YOUR_PROJECT_ID>
```

Credentials are stored at `~/.config/gcloud/application_default_credentials.json` and picked up automatically by the SDK.

### 3. Configuration

Set your project and region in `config.py` or via environment variables:

| Setting | Config File (`config.py`) | Environment Variable |
|---|---|---|
| GCP Project ID | `gcp_project = "<YOUR_PROJECT_ID>"` | `GCP_PROJECT` |
| GCP Region | `gcp_location = "us-central1"` | `GCP_LOCATION` |
| Vertex AI mode | `vertexai = True` | — |

### 4. Deployment (Cloud Run Jobs)

For production deployment, create a dedicated service account:

```bash
# Create a service account
gcloud iam service-accounts create pipeline-runner \
    --display-name="Pipeline Runner" \
    --project=<YOUR_PROJECT_ID>

# Grant Vertex AI access
gcloud projects add-iam-policy-binding <YOUR_PROJECT_ID> \
    --member="serviceAccount:pipeline-runner@<YOUR_PROJECT_ID>.iam.gserviceaccount.com" \
    --role="roles/aiplatform.user"

# Grant GCS access (for reading data from a bucket)
gcloud projects add-iam-policy-binding <YOUR_PROJECT_ID> \
    --member="serviceAccount:pipeline-runner@<YOUR_PROJECT_ID>.iam.gserviceaccount.com" \
    --role="roles/storage.objectViewer"
```

Create a GCS bucket to store your pipeline data (prompts, responses, results):

```bash
# Create the bucket
gcloud storage buckets create gs://<YOUR_BUCKET_NAME> \
    --project=<YOUR_PROJECT_ID> \
    --location=<YOUR_REGION>

# Upload your data
gcloud storage cp sample_data/prompts.json gs://<YOUR_BUCKET_NAME>/sample_data/
gcloud storage cp sample_data/*_response.csv gs://<YOUR_BUCKET_NAME>/sample_data/
```

Deploy and run as a Cloud Run Job:

```bash
# Deploy the job
gcloud run jobs deploy <JOB_NAME> \
    --source . \
    --command="python3" \
    --service-account="pipeline-runner@<YOUR_PROJECT_ID>.iam.gserviceaccount.com" \
    --region=<YOUR_REGION> \
    --add-volume=name=config-volume,type=cloud-storage,bucket=<YOUR_BUCKET_NAME> \
    --add-volume-mount=volume=config-volume,mount-path=/mnt/configs

# Execute the job
gcloud run jobs execute <JOB_NAME> \
    --region=<YOUR_REGION> \
    --args="main.py,--prompts,/mnt/configs/sample_data/prompts.json,--responses,/mnt/configs/sample_data,--limit,1,--llmasajudge,--extractor,-o,/mnt/configs/"
```

### 5. Required IAM Roles

| Role | Purpose |
|---|---|
| `roles/aiplatform.user` | Call Vertex AI / Gemini models |
| `roles/storage.objectViewer` | Read prompt + response data from GCS bucket |
| `roles/storage.objectCreator` | Write results back to GCS bucket (optional) |
| `roles/run.invoker` | Execute Cloud Run Jobs (for CI/CD triggers) |

## Quick Start

```bash
# 1. Create and activate virtual environment
python3 -m venv .venv
source .venv/bin/activate

# 2. Install dependencies
pip install -r requirements.txt

# 3. Run Layer 1 (format checker) on sample data
python3 main.py --prompts sample_data/sample_prompts.json --responses sample_data \
  --extractor

# 4. Run Layer 2 (LLM-as-a-Judge) on sample data
python3 main.py --prompts sample_data/sample_prompts.json --responses sample_data \
  --llmasajudge

# 5. Run both layers together with a limit
python3 main.py --prompts sample_data/sample_prompts.json --responses sample_data \
  --limit 2 --extractor --llmasajudge

# 6. Save results to a file
python3 main.py --prompts sample_data/sample_prompts.json --responses sample_data \
  --extractor --llmasajudge -o output.json
```

## Input Format

### prompts.json

An array of prompt configurations. Each has an `id` that maps to a response CSV file:

```json
[
  {
    "id": "001",
    "name": "Product Launch Outreach",
    "evaluation_config_payload": {
      "instruction": {
        "goal": "Generate a short outreach email... <full prompt text>",
        "tone": "friendly",
        "type": "emailFull",
        "evaluationGuideline": "Email should be warm and not salesy.",
        "minimumQualityScore": 85,
        "maxRegenerationAttempts": 2
      }
    }
  }
]
```

### {id}_response.csv

One column `prompt_response` where each row is a JSON-encoded object:

```json
{
  "body": "Hi Sarah,\n\nCongrats on the new product launch...",
  "subject": "Congrats on the launch",
  "reasoning": "I kept the email short and genuine...",
  "__evaluation": {
    "overallScore": 88,
    "qualityRating": "Good",
    "keyStrengths": ["..."],
    "areasForImprovement": ["..."],
    "recommendations": ["..."]
  }
}
```

> **Note:** The `__evaluation` block is optional — some responses don't include it.

### How Data Is Paired

The pipeline pairs them by matching `prompts.json[].id` to `{id}_response.csv`:

- Prompt `id: "001"` → loads `001_response.csv`
- Prompt `id: "002"` → loads `002_response.csv`

## What Each Layer Uses

### Layer 1: Format Checker

| Prompt Field | Response Field | Purpose |
|---|---|---|
| `instruction.goal` | `subject` + `body` | Goal is sent to Gemini to extract formatting rules; subject + body are checked against those rules |

**Checks performed:** word count, character count, line/paragraph count, subject line presence & length, banned words, required elements.

### Layer 2: LLM-as-a-Judge

| Prompt Field | Response Field | Purpose |
|---|---|---|
| `instruction.goal` | `subject` + `body` | Goal is the `input` and subject + body are the `actual_output` for the GEval judge |
| `instruction.tone` | — | Used to construct dynamic evaluation steps (e.g. "Check if the tone is friendly") |

**Current criterion:** Tone alignment (scored 0.0–1.0 with reasoning).

The judge uses DeepEval's `GEval` metric with custom evaluation steps and the configured Gemini provider as the judge model (via `DeepEvalProviderAdapter`).

## CLI Reference

```bash
python3 main.py --prompts <path> --responses <path> [options]
```

| Flag | Description |
|---|---|
| `--prompts` | Path to `prompts.json` file (required) |
| `--responses` | Path to directory containing response CSV files (required) |
| `--prompt-id` | Process only the prompt with this id (default: all) |
| `--limit` | Max number of responses to evaluate per prompt (default: all) |
| `--extractor` / `-e` | Enable Layer 1 (format checker) |
| `--llmasajudge` / `-l` | Enable Layer 2 (LLM-as-a-Judge) |
| `--provider` | LLM provider for Layer 1 extractor (default: gemini) |
| `--model` | Model name for Layer 1 extractor (default: gemini-2.5-flash) |
| `--provider_llmasajudge` | LLM provider for Layer 2 judge |
| `--model_llmasajudge` | Model name for Layer 2 judge |
| `--layer1-weight` | Weight for Layer 1 in composite score (default: 0.4) |
| `--layer2-weight` | Weight for Layer 2 in composite score (default: 0.6) |
| `--sql-table TABLE` | Print `CREATE TABLE` SQL for the given table name and exit |
| `--offline` | Skip LLM calls, run with empty criteria |
| `-o FILE` | Write results to file instead of stdout |
| `-v` | Verbose logging |

## Configuration

Defaults are set in `config.py`:

| Setting | Default | Override |
|---|---|---|
| Provider | `gemini` | `--provider` CLI flag |
| Model | `gemini-2.5-flash` | `--model` CLI flag |
| GCP Project | — | `GCP_PROJECT` env var or `config.py` |
| GCP Region | `us-central1` | `GCP_LOCATION` env var |
| Vertex AI mode | `True` | `config.py` |
| Temperature | `0.0` | `config.py` |
| Max tokens | `4096` | `config.py` |

See `.env.example` for all available environment variables.

## Amalgamator & SQL Integration

The amalgamator automatically runs after both layers and produces flat rows — each row has only primitive types (`str`, `float`, `int`, `None`), ready for direct SQL insertion.

**Composite scoring:** weighted average of active layers (default: 40% Layer 1, 60% Layer 2). If only one layer runs, that layer's score becomes the composite.

**SQL columns per row:**

| Column | Type | Source |
|---|---|---|
| `eval_id` | `VARCHAR(64)` | Identity |
| `prompt_id`, `prompt_name` | `VARCHAR` | Prompt config |
| `response_index` | `INTEGER` | Row position in CSV |
| `response_subject`, `response_body` | `TEXT` | Response content |
| `layer1_score` | `FLOAT` | Format checker (0.0–1.0) |
| `layer1_total_checks`, `layer1_passed`, `layer1_failed` | `INTEGER` | Check counts |
| `layer1_checks_json` | `TEXT` | Detailed per-check results (JSON string) |
| `layer2_tone_score` | `FLOAT` | Tone judge (0.0–1.0) |
| `layer2_tone_reason` | `TEXT` | Judge reasoning |
| `composite_score` | `FLOAT` | Weighted composite (0.0–1.0) |
| `platform_score` | `INTEGER` | Existing `__evaluation.overallScore` (0–100) |
| `platform_rating` | `VARCHAR(32)` | Existing `__evaluation.qualityRating` |
| `evaluated_at` | `TIMESTAMP` | When evaluation ran |

**Generate the CREATE TABLE SQL:**

```bash
python3 main.py --prompts sample_data/sample_prompts.json --responses sample_data \
  --sql-table evaluation_results
```

**Custom weights:**

```bash
python3 main.py --prompts sample_data/sample_prompts.json --responses sample_data \
  --extractor --llmasajudge --layer1-weight 0.3 --layer2-weight 0.7
```

## Sample Output

The output contains `amalgamated` (flat SQL-ready rows) and `raw` (original layer results):

```json
{
  "amalgamated": [
    {
      "eval_id": "001-0",
      "prompt_id": "001",
      "prompt_name": "Product Launch Outreach",
      "response_index": 0,
      "response_subject": "Congrats on the launch",
      "response_body": "Hi Sarah...",
      "layer1_score": 0.6,
      "layer1_total_checks": 5,
      "layer1_passed": 3,
      "layer1_failed": 2,
      "layer1_checks_json": "{...}",
      "layer2_tone_score": 0.9,
      "layer2_tone_reason": "The tone is clearly friendly...",
      "composite_score": 0.78,
      "layer1_weight": 0.4,
      "layer2_weight": 0.6,
      "platform_score": 88,
      "platform_rating": "Good",
      "evaluated_at": "2026-06-16T04:41:05+00:00"
    }
  ],
  "raw": {
    "extractor": [ ... ],
    "llm_as_a_judge": [ ... ]
  }
}
```

## Project Structure

```
├── main.py                           # CLI entrypoint — runs both layers
├── config.py                         # Provider config, GCP settings, defaults
├── requirements.txt                  # Python dependencies
├── Dockerfile                        # Container image for Cloud Run Jobs
├── .env.example                      # Environment variable reference
├── .gcloudignore                     # Files excluded from Cloud Run deploy
├── models/
│   ├── input_schema.py               # Pydantic models for prompts.json + CSV responses
│   ├── criteria_schema.py            # Criteria + format check result models
│   └── llmasajudge_schema.py         # MetricResult model for GEval output
├── extractors/
│   ├── base.py                       # Abstract LLM provider interface
│   ├── criteria_extractor.py         # LLM prompt + parsing for Layer 1
│   ├── llm_as_a_judge.py             # DeepEvalProviderAdapter for Layer 2
│   └── providers/
│       ├── gemini_provider.py        # Vertex AI / Gemini (active provider)
│       ├── openai_provider.py        # OpenAI (available)
│       ├── anthropic_provider.py     # Anthropic (available)
│       └── ollama_provider.py        # Ollama local (available)
├── evaluators/
│   ├── format_checker.py             # Rule-based formatting checks (Layer 1)
│   └── amalgamator.py                # Combines layers into SQL-ready output
├── sample_data/
│   ├── sample_prompts.json           # Sample prompt configuration
│   └── sample_001_response.csv       # Sample response data
└── tests/
    ├── test_format_checker.py        # Format checker unit tests
    └── test_criteria_extractor.py    # Criteria extractor tests
```

