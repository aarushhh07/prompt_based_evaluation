# Technical Documentation — Evaluation Pipeline Internals

This document covers the internal workings of each pipeline component: how the extractor identifies checks, how the schema models data, how the tone judge scores emails, how the amalgamator computes the final score, and how results reach Cloud SQL.

---


## 1. Criteria Extraction (Layer 1 — Step 1)

### Motivation

Heuristically, the email generation prompt contains functional checks including and not limited to word limits, character limits, line limits, requirements of some phrases, words, strict exclusion of some other phrases. Automation of these algorithmic checks requires precise arguments extracted from the prompt, which has been tasked using a prompt to an LLM as a zero-shot task.

### What happens

The prompt's `instruction.goal` field is sent to **Extractor Model** with a system prompt inststructions: *"read this email-generation prompt and extract every formatting or structural constraint into JSON."*

The system prompt lives in [criteria_extractor.py](extractors/criteria_extractor.py).

### What the LLM extracts

The LLM returns a JSON object matching the `ExtractedCriteria` schema. Every field is optional — only fields that the prompt explicitly or implicitly specifies are populated.

| Extracted Field | Schema Type | Example from Prompt |
|---|---|---|
| `word_limit` | `{ min, max }` | *"Keep it under 100 words"* → `{ "max": 100 }` |
| `character_limit` | `{ min, max }` | *"Strictly 250 characters max"* → `{ "max": 250 }` |
| `line_limit` | `{ min, max }` | *"No more than 5 lines"* → `{ "max": 5 }` |
| `paragraph_limit` | `{ min, max }` | *"Keep it to 2 paragraphs"* → `{ "max": 2 }` |
| `subject_line` | `{ required, max_characters }` | *"Subject line under 50 characters"* → `{ "required": true, "max_characters": 49 }` |
| `banned_words` | `[ string, ... ]` | *"No exclamation marks. No em dashes."* → `["!", "—"]` |
| `required_elements` | `[ string, ... ]` | *"Include a CTA and congratulate them"* → `["congratulations", "call-to-action"]` |
| `tone` | `string` | *"Friendly and genuine"* → `"friendly and genuine"` |
| `additional` | `{ key: value }` | Catch-all for rules that don't fit above — e.g. `"no_links": "true"`, `"sentence_limit": "2"` |

### LLM instruction rules

The system prompt enforces these constraints on the LLM:

- Only include fields the prompt explicitly or clearly implicitly specifies
- `banned_words` must contain only specific words/phrases, not categories like "emojis"
- `additional` must be minimal — simple key-value string pairs only, no nesting
- Do not invent constraints not in the prompt
- Return raw JSON only — no markdown fences, no commentary

### JSON sanitization

If the LLM returns malformed JSON, [criteria_extractor.py](extractors/criteria_extractor.py#L91-L137) applies two fallback strategies:

1. **Strip markdown fences** — removes `` ```json `` wrappers
2. **Sanitize quirks** — removes `//` comments and trailing commas before `}` or `]`

If both fail, an empty `ExtractedCriteria()` is used (no checks run).
### Limitations and Further Work
The manual function writing can only work for a limited amount of constraints, further testing has also revealed problems when the Extractor Model extracts required elements which are not compulsory, rather recommended. One approach to tackle this has been to tighten the prompt, also thinking of removing the field entirely. 

---
## 2. Format Checking (Layer 1 — Step 2)

### Alternatives
The approach of **Code as a Judge** with dynamic generation of a checker file is a alternative to hard-coding a file for generation. However some function shall remain in this setup as a seful fallback, this feature is being tested locally, will report results.
### What happens

The [FormatChecker](evaluators/format_checker.py#L20-L90) takes the extracted criteria and runs deterministic checks against the email response.

### Input parsing

The response string `Subject: {subject}\n\n{body}` is split:
- **Subject line**: extracted by searching for a line starting with `Subject:` (case-insensitive)
- **Body**: everything after the subject line

### Individual checks

#### 1. Word Limit (`word_limit`)
- **Runs when**: `criteria.word_limit` is not `None`
- **Measures**: `len(body.split())`
- **Passes if**: word count is between `min` and `max` (either can be `None`)
- **Output**: `"86 words — within limit"` or `"152 words — exceeds 100-word max"`

#### 2. Character Limit (`character_limit`)
- **Runs when**: `criteria.character_limit` is not `None`
- **Measures**: `len(body)`
- **Passes if**: character count is between `min` and `max`
- **Output**: `"273 characters — exceeds 250-char max"`

#### 3. Line Limit (`line_limit`)
- **Runs when**: `criteria.line_limit` is not `None`
- **Measures**: count of non-empty lines in body
- **Passes if**: line count is between `min` and `max`
- **Output**: `"7 non-empty lines — within limit"`

#### 4. Paragraph Limit (`paragraph_limit`)
- **Runs when**: `criteria.paragraph_limit` is not `None`
- **Measures**: count of text blocks separated by blank lines (`\n\n`)
- **Passes if**: paragraph count is between `min` and `max`
- **Output**: `"3 paragraphs — within limit"`

#### 5. Subject Line Present (`subject_line_present`)
- **Runs when**: `criteria.subject_line.required` is `True`
- **Passes if**: a `Subject: ...` line was found in the response
- **Output**: `"Subject line found: 'Big congrats on the new round'"` or `"No subject line found (expected 'Subject: ...')"`

#### 6. Subject Line Length (`subject_line_length`)
- **Runs when**: `criteria.subject_line.max_characters` is set AND a subject line was found
- **Measures**: `len(subject_line)`
- **Passes if**: length ≤ `max_characters`
- **Output**: `"29 characters — within 49-character max"`

#### 7. Banned Words (`banned_words`)
- **Runs when**: `criteria.banned_words` is non-empty
- **Measures**: case-insensitive substring search of each banned word in the entire response (subject + body)
- **Passes if**: none of the banned words are found
- **Output**: `"No banned words detected"` or `"Banned words found: !, —"`

#### 8. Required Elements (`required_elements`) (Under Review)
- **Runs when**: `criteria.required_elements` is non-empty
- **Measures**: each required element is checked using heuristic patterns:

| Element | Heuristic Patterns Checked |
|---|---|
| `call-to-action` | "would you be open", "let me know", "schedule a", "book a", "15-minute", "happy to", "interested in", "free to chat", "can we", "let's", "?" |
| `greeting` | "hi ", "hello ", "hey ", "dear " |
| `signature` | "best,", "regards,", "cheers,", "thanks,", "sincerely," |
| *anything else* | Simple case-insensitive substring match |

- **Passes if**: all required elements are found
- **Output**: `"Missing required elements: congratulations, call-to-action for meeting"`

### Scoring

```
Layer 1 score = passed_checks / total_checks
```

If no checks run (empty criteria), score defaults to `1.0`.

---

## 3. Schema Architecture

### Data Models

The pipeline uses Pydantic models to enforce types at every stage:

```
prompts.json                          {id}_response.csv
     │                                       │
     ▼                                       ▼
PromptConfig                          ResponseRecord
  ├── id: str                           ├── body: str
  ├── name: str                         ├── subject: str
  ├── evaluation_config_payload         ├── reasoning: str
  │   └── instruction                   └── evaluation: ExistingEvaluation (optional)
  │       ├── goal: str                       ├── overallScore: int
  │       ├── tone: str                       ├── qualityRating: str
  │       ├── type: str                       ├── keyStrengths: [str]
  │       ├── evaluationGuideline: str        ├── areasForImprovement: [str]
  │       ├── minimumQualityScore: int        └── recommendations: [str]
  │       └── maxRegenerationAttempts: int
  │
  └── (metadata: tenant_id, version, status, etc.)
```

These are combined into `EvaluationInput`:

```
EvaluationInput
  ├── eval_id: str           (e.g. "913-0")
  ├── prompt_config: PromptConfig
  └── response: ResponseRecord
```

### Results Models

```
ExtractedCriteria (LLM output)       CheckResult (per check)
  ├── word_limit: LimitRange           ├── passed: bool
  ├── character_limit: LimitRange      ├── actual: int/None
  ├── line_limit: LimitRange           ├── limit: int/None
  ├── paragraph_limit: LimitRange      ├── violations: [str]/None
  ├── subject_line: SubjectLineCriteria└── detail: str
  ├── banned_words: [str]
  ├── required_elements: [str]       EvaluationResult
  ├── tone: str                        ├── eval_id: str
  └── additional: {str: str}           ├── checks: {name: CheckResult}
                                       ├── summary: ResultSummary
MetricResult (GEval output)            │   ├── total_checks: int
  ├── metric: str                      │   ├── passed: int
  ├── score: float                     │   ├── failed: int
  ├── reason: str                      │   └── score: float (0.0-1.0)
  ├── passed: bool/None                └── criteria_used: ExtractedCriteria
  └── threshold: float/None
```

### File Locations

| Model | File |
|---|---|
| `PromptConfig`, `ResponseRecord`, `EvaluationInput` | [input_schema.py](models/input_schema.py) |
| `ExtractedCriteria`, `CheckResult`, `EvaluationResult` | [criteria_schema.py](models/criteria_schema.py) |
| `MetricResult` | [llmasajudge_schema.py](models/llmasajudge_schema.py) |
| `AmalgamatedRow` | [amalgamator.py](evaluators/amalgamator.py) |

---

## 4. LLM as a Judge (Layer 2)

### Motivation
This is the subjective monitoring of the response, since finetuning a Large Language Model proves unnecessarily feeble and computationally expensive, a few-shot approach has been employed using Chain of Thought Reasoning. The **Judge Model** is given evaluation steps with tone guidance to output a score and reasoning for the score.
### What happens

Each email is evaluated using DeepEval's `GEval` metric — an LLM-as-a-Judge framework that uses chain-of-thought evaluation steps to produce a score.

### How it works

1. A `LLMTestCase` is created with:
   - `input` = the prompt's `instruction.goal` text
   - `actual_output` = `Subject: {subject}\n\n{body}`

2. A `DeepEvalProviderAdapter` wraps the Gemini provider so DeepEval can use it as its judge model. This adapter lives in [extractors/llm_as_a_judge.py](file:///home/aarus/Tapistro/version2/extractors/llm_as_a_judge.py) and implements DeepEval's `DeepEvalBaseLLM` interface by delegating to the `complete_no_json()` method on the Gemini provider.

3. A `GEval` metric is configured with 5 evaluation steps that are **dynamic** — the `tone` field from the prompt config is injected:

```python
tone = input_data.prompt_config.evaluation_config_payload.instruction.tone
# e.g. tone = "excited"

evaluation_steps = [
    f"Check if the tone is {tone}.",                                    # Step 1
    f"Check if it's {tone} without being over the top.",                # Step 2
    "Penalize if it reads like a cold, copy-paste sales email.",        # Step 3
    "Penalize if it's overly effusive or uses superlatives.",           # Step 4
    "Do not check for formatting, word limit, strictly focus on tone.", # Step 5
]
```

4. DeepEval internally sends these steps + the test case to the judge LLM, which produces:
   - **Score**: `0.0` to `1.0` (e.g. `0.9`)
   - **Reason**: chain-of-thought explanation (e.g. *"The tone is clearly excited, using phrases like 'Big congrats'..."*)

### Output

```json
{
    "metric": "Tone",
    "score": 0.9,
    "passed": null,
    "threshold": null,
    "reason": "The tone is clearly excited, using phrases like 'Big congrats'..."
}
```

### Provider adapter flow

```
DeepEval GEval
    │ calls a_generate(prompt)
    ▼
DeepEvalProviderAdapter
    │ calls provider.complete_no_json(prompt)
    ▼
GeminiProvider
    │ calls google-genai SDK (no JSON mime constraint)
    ▼
Vertex AI → Gemini 2.5 Flash → plain text response
```

---

## 5. Amalgamator

### What happens

After both layers run, the amalgamator combines their results into flat rows — one per evaluated response — with a weighted composite score.

### Composite score formula

```
composite = (layer1_score × W₁ + layer2_score × W₂) / (W₁ + W₂)
```

Default weights: `W₁ = 0.4` (format checker), `W₂ = 0.6` (tone judge).

**Edge cases:**
- If only Layer 1 ran → `composite = layer1_score`
- If only Layer 2 ran → `composite = layer2_score`
- If neither ran → `composite = null`

Weights are normalized before use, so `--layer1-weight 30 --layer2-weight 70` produces the same result as `--layer1-weight 0.3 --layer2-weight 0.7`.

### Row structure

Each `AmalgamatedRow` is a flat Pydantic model with only primitive types — no nested objects. This means every field maps directly to a SQL column:

| Field | Type | Where It Comes From |
|---|---|---|
| `eval_id` | `str` | `{prompt_id}-{response_index}` — deterministic |
| `prompt_id` | `str` | Parsed from `eval_id` |
| `prompt_name` | `str` | `PromptConfig.name` |
| `response_index` | `int` | Row position in the CSV file |
| `response_subject` | `str` | `ResponseRecord.subject` |
| `response_body` | `str` | `ResponseRecord.body` |
| `layer1_score` | `float` | `EvaluationResult.summary.score` |
| `layer1_total_checks` | `int` | Number of checks that ran |
| `layer1_passed` | `int` | Checks that passed |
| `layer1_failed` | `int` | Checks that failed |
| `layer1_checks_json` | `str` | `json.dumps()` of the detailed per-check results |
| `layer2_tone_score` | `float` | `MetricResult.score` |
| `layer2_tone_reason` | `str` | `MetricResult.reason` |
| `composite_score` | `float` | Weighted average (formula above) |
| `layer1_weight` | `float` | Weight used for Layer 1 |
| `layer2_weight` | `float` | Weight used for Layer 2 |
| `platform_score` | `int` | `__evaluation.overallScore` from response CSV (if present) |
| `platform_rating` | `str` | `__evaluation.qualityRating` from response CSV (if present) |
| `evaluated_at` | `str` | ISO timestamp of when the pipeline ran |

---

## 6. Cloud SQL Writer

### What happens

When `--cloud-sql` is provided, the amalgamated rows are written to a PostgreSQL table on Cloud SQL.

### Connection flow

```
main.py
  │ passes amalgamated_dicts to write_rows()
  ▼
cloud_sql_writer.py
  │ imports google.cloud.sql.connector
  ▼
Cloud SQL Python Connector
  │ handles auth (ADC or password), SSL, connection pooling
  ▼
Cloud SQL PostgreSQL instance
  │ evaluation_db → evaluation_results table
  ▼
pg8000 driver executes SQL
```

### Table auto-creation

On first run, `ensure_table()` executes a `CREATE TABLE IF NOT EXISTS` with the schema matching `AmalgamatedRow`. No manual setup needed.

### Upsert behavior

```sql
INSERT INTO evaluation_results (eval_id, prompt_id, ...)
VALUES (%s, %s, ...)
ON CONFLICT (eval_id) DO UPDATE SET
    prompt_id = EXCLUDED.prompt_id,
    prompt_name = EXCLUDED.prompt_name,
    ...
```

This means:
- **First run**: inserts all rows
- **Re-run same data**: overwrites existing rows (no duplicates)
- **New data**: inserts new rows alongside existing ones

### Authentication options

| Method | When to Use | How |
|---|---|---|
| **Password auth** | Simple setup, Cloud Run Jobs | `--db-password <PASSWORD>` |
| **IAM auth** | No password management, uses ADC | Omit `--db-password`, service account needs `roles/cloudsql.instanceUser` + IAM DB user created |

### Querying results

Once results are in the database, you can query them directly:

```sql
-- Average composite score per prompt
SELECT prompt_id, prompt_name,
       AVG(composite_score) AS avg_composite,
       AVG(layer1_score) AS avg_format,
       AVG(layer2_tone_score) AS avg_tone,
       COUNT(*) AS responses_evaluated
FROM evaluation_results
GROUP BY prompt_id, prompt_name;

-- Compare pipeline score vs platform score
SELECT eval_id, composite_score,
       platform_score / 100.0 AS platform_normalized,
       ABS(composite_score - platform_score / 100.0) AS delta
FROM evaluation_results
WHERE platform_score IS NOT NULL
ORDER BY delta DESC;

-- Find responses that failed format checks
SELECT eval_id, layer1_score, layer1_failed, layer1_checks_json
FROM evaluation_results
WHERE layer1_score < 1.0
ORDER BY layer1_score ASC;

-- Responses with poor tone despite good formatting
SELECT eval_id, layer1_score, layer2_tone_score, layer2_tone_reason
FROM evaluation_results
WHERE layer1_score >= 0.8 AND layer2_tone_score < 0.5;
```

---

## Full Pipeline Flow

```
                        ┌─────────────────────────────────┐
                        │        prompts.json             │
                        │  (instruction.goal, tone, etc.) │
                        └──────────┬──────────────────────┘
                                   │
                                   ▼
                        ┌──────────────────────┐
                        │   Load & pair data    │
                        │  prompts + CSV rows   │
                        │  → EvaluationInput[]  │
                        └──────────┬───────────┘
                                   │
                    ┌──────────────┴──────────────┐
                    ▼                              ▼
         ┌─────────────────┐            ┌─────────────────────┐
         │ LAYER 1          │            │ LAYER 2              │
         │                  │            │                      │
         │ 1. Send goal to  │            │ 1. Build LLMTestCase │
         │    Gemini Flash  │            │    (goal + email)    │
         │                  │            │                      │
         │ 2. Get criteria  │            │ 2. Configure GEval   │
         │    JSON back     │            │    with tone steps   │
         │                  │            │                      │
         │ 3. Run checks:   │            │ 3. Judge scores      │
         │    - word limit   │            │    0.0–1.0 + reason │
         │    - char limit   │            │                      │
         │    - line limit   │            └──────────┬───────────┘
         │    - para limit   │                       │
         │    - subject line │                       │
         │    - banned words │                       │
         │    - required     │                       │
         │      elements    │                       │
         │                  │                       │
         │ 4. Score =        │                       │
         │    passed/total  │                       │
         └────────┬─────────┘                       │
                  │                                  │
                  └──────────────┬───────────────────┘
                                 ▼
                      ┌─────────────────────┐
                      │    AMALGAMATOR       │
                      │                     │
                      │ composite =          │
                      │  L1×0.4 + L2×0.6    │
                      │                     │
                      │ Flatten to SQL rows │
                      └─────────┬───────────┘
                                │
                  ┌─────────────┼──────────────┐
                  ▼             ▼              ▼
            ┌──────────┐ ┌──────────┐  ┌─────────────┐
            │  stdout  │ │  -o file │  │  Cloud SQL  │
            │  (JSON)  │ │  (JSON)  │  │ (PostgreSQL)│
            └──────────┘ └──────────┘  └─────────────┘
```
## Since Last Demo 
- The amalgamator file and SQL integration complete the end-to-end framework, with deployment scope.
- The results produced are coherent with sensible composite scores on which valuable insight may be gained upon the generated responses
- Current work is to assess if a partially dynamic model for generation of python file is feasible
- Also, if documents pertaining to further insight on the provider are accessible, a faithfulness check can also be introduced as a new layer.
- The modular framework allows for easy changes and SQL integration is helpful in querying results.
