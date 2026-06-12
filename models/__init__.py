# ── New format models ────────────────────────────────────────────────
from .input_schema import (
    PromptInstruction,
    EvaluationConfigPayload,
    PromptConfig,
    ExistingEvaluation,
    ResponseRecord,
    EvaluationInput,
)

# ── Criteria & results models ────────────────────────────────────────
from .criteria_schema import (
    ExtractedCriteria,
    LimitRange,
    SubjectLineCriteria,
    CheckResult,
    EvaluationResult,
    ResultSummary,
)

# ── Legacy models ────────────────────────────────────────────────────
from .input_schema import (
    TargetPersona,
    CampaignInfo,
    InputMetadata,
    LegacyEvaluationInput,
)
