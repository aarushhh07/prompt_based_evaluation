"""Rule-based formatting checker.

Runs deterministic checks against the email response using criteria
extracted from the prompt by the CriteriaExtractor.
"""

from __future__ import annotations

import re
from typing import Dict

from models.criteria_schema import (
    CheckResult,
    EvaluationResult,
    ExtractedCriteria,
    ResultSummary,
)


class FormatChecker:
    """Evaluate an email response against extracted formatting criteria."""

    def evaluate(self, eval_id: str, response: str, criteria: ExtractedCriteria) -> EvaluationResult:
        """Run all applicable checks and return an EvaluationResult.

        Args:
            eval_id: Identifier carried through from the input.
            response: The full LLM-generated email text.
            criteria: Structured criteria extracted from the prompt.

        Returns:
            EvaluationResult with per-check details and an aggregate score.
        """
        checks: Dict[str, CheckResult] = {}

        # ── Separate subject line from body ────────────────────────────
        subject_line, body = self._split_subject(response)

        # ── Word limit ─────────────────────────────────────────────────
        if criteria.word_limit is not None:
            checks["word_limit"] = self._check_word_limit(body, criteria)

        # ── Character limit ────────────────────────────────────────────
        if criteria.character_limit is not None:
            checks["character_limit"] = self._check_character_limit(body, criteria)

        # ── Line limit ─────────────────────────────────────────────────
        if criteria.line_limit is not None:
            checks["line_limit"] = self._check_line_limit(body, criteria)

        # ── Paragraph limit ────────────────────────────────────────────
        if criteria.paragraph_limit is not None:
            checks["paragraph_limit"] = self._check_paragraph_limit(body, criteria)

        # ── Subject line checks ────────────────────────────────────────
        if criteria.subject_line is not None:
            if criteria.subject_line.required:
                checks["subject_line_present"] = self._check_subject_present(subject_line)
            if criteria.subject_line.max_characters is not None and subject_line is not None:
                checks["subject_line_length"] = self._check_subject_length(
                    subject_line, criteria.subject_line.max_characters
                )

        # ── Banned words ───────────────────────────────────────────────
        if criteria.banned_words:
            checks["banned_words"] = self._check_banned_words(response, criteria.banned_words)

        # ── Required elements (simple keyword / phrase presence) ───────
        if criteria.required_elements:
            checks["required_elements"] = self._check_required_elements(
                response, criteria.required_elements
            )

        # ── Build summary ──────────────────────────────────────────────
        total = len(checks)
        passed = sum(1 for c in checks.values() if c.passed)
        failed = total - passed
        score = passed / total if total else 1.0

        return EvaluationResult(
            eval_id=eval_id,
            checks=checks,
            summary=ResultSummary(
                total_checks=total,
                passed=passed,
                failed=failed,
                score=round(score, 4),
            ),
            criteria_used=criteria,
        )

    # ── Helper: split subject line from body ───────────────────────────

    @staticmethod
    def _split_subject(response: str):
        """Return (subject_line, body).  subject_line is None if not found."""
        lines = response.split("\n")
        for i, line in enumerate(lines):
            stripped = line.strip()
            if stripped.lower().startswith("subject:"):
                subject = stripped[len("subject:"):].strip()
                body = "\n".join(lines[i + 1:]).strip()
                return subject, body
        # No subject line found — treat entire response as body
        return None, response.strip()

    # ── Individual checks ──────────────────────────────────────────────

    @staticmethod
    def _count_words(text: str) -> int:
        return len(text.split())

    @staticmethod
    def _count_paragraphs(text: str) -> int:
        """Count paragraphs as blocks of text separated by blank lines."""
        paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
        return len(paragraphs)

    def _check_word_limit(self, body: str, criteria: ExtractedCriteria) -> CheckResult:
        count = self._count_words(body)
        limit = criteria.word_limit
        passed = True
        parts = []
        if limit.max is not None and count > limit.max:
            passed = False
            parts.append(f"exceeds {limit.max}-word max")
        if limit.min is not None and count < limit.min:
            passed = False
            parts.append(f"below {limit.min}-word min")
        detail = f"{count} words" + (f" — {'; '.join(parts)}" if parts else f" — within limit")
        return CheckResult(passed=passed, actual=count, limit=limit.max or limit.min, detail=detail)

    def _check_character_limit(self, body: str, criteria: ExtractedCriteria) -> CheckResult:
        count = len(body)
        limit = criteria.character_limit
        passed = True
        parts = []
        if limit.max is not None and count > limit.max:
            passed = False
            parts.append(f"exceeds {limit.max}-char max")
        if limit.min is not None and count < limit.min:
            passed = False
            parts.append(f"below {limit.min}-char min")
        detail = f"{count} characters" + (f" — {'; '.join(parts)}" if parts else f" — within limit")
        return CheckResult(passed=passed, actual=count, limit=limit.max or limit.min, detail=detail)

    def _check_line_limit(self, body: str, criteria: ExtractedCriteria) -> CheckResult:
        count = len([l for l in body.splitlines() if l.strip()])
        limit = criteria.line_limit
        passed = True
        parts = []
        if limit.max is not None and count > limit.max:
            passed = False
            parts.append(f"exceeds {limit.max}-line max")
        if limit.min is not None and count < limit.min:
            passed = False
            parts.append(f"below {limit.min}-line min")
        detail = f"{count} non-empty lines" + (f" — {'; '.join(parts)}" if parts else f" — within limit")
        return CheckResult(passed=passed, actual=count, limit=limit.max or limit.min, detail=detail)

    def _check_paragraph_limit(self, body: str, criteria: ExtractedCriteria) -> CheckResult:
        count = self._count_paragraphs(body)
        limit = criteria.paragraph_limit
        passed = True
        parts = []
        if limit.max is not None and count > limit.max:
            passed = False
            parts.append(f"exceeds {limit.max}-paragraph max")
        if limit.min is not None and count < limit.min:
            passed = False
            parts.append(f"below {limit.min}-paragraph min")
        detail = f"{count} paragraphs" + (f" — {'; '.join(parts)}" if parts else f" — within limit")
        return CheckResult(passed=passed, actual=count, limit=limit.max or limit.min, detail=detail)

    @staticmethod
    def _check_subject_present(subject_line) -> CheckResult:
        if subject_line is not None:
            return CheckResult(
                passed=True,
                detail=f"Subject line found: '{subject_line}'",
            )
        return CheckResult(passed=False, detail="No subject line found (expected 'Subject: ...')")

    @staticmethod
    def _check_subject_length(subject_line: str, max_chars: int) -> CheckResult:
        length = len(subject_line)
        passed = length <= max_chars
        detail = f"{length} characters — {'within' if passed else 'exceeds'} {max_chars}-character max"
        return CheckResult(passed=passed, actual=length, limit=max_chars, detail=detail)

    @staticmethod
    def _check_banned_words(response: str, banned: list[str]) -> CheckResult:
        lower = response.lower()
        violations = [w for w in banned if w.lower() in lower]
        passed = len(violations) == 0
        detail = (
            "No banned words detected"
            if passed
            else f"Banned words found: {', '.join(violations)}"
        )
        return CheckResult(passed=passed, violations=violations, detail=detail)

    @staticmethod
    def _check_required_elements(response: str, elements: list[str]) -> CheckResult:
        """Heuristic check: see if each required element is mentioned or
        can be reasonably inferred from the text.

        This is a simple keyword/phrase presence check.  A more
        sophisticated version could use an LLM judge.
        """
        lower = response.lower()
        # Map common element names to heuristic patterns
        _HEURISTICS = {
            "call-to-action": [
                "would you be open",
                "let me know",
                "schedule a",
                "book a",
                "15-minute",
                "happy to",
                "interested in",
                "free to chat",
                "can we",
                "let's",
                "?",  # questions often imply CTA
            ],
            "greeting": [
                "hi ",
                "hello ",
                "hey ",
                "dear ",
            ],
            "signature": [
                "best,",
                "regards,",
                "cheers,",
                "thanks,",
                "sincerely,",
            ],
        }

        missing = []
        for elem in elements:
            key = elem.lower().strip()
            patterns = _HEURISTICS.get(key)
            if patterns:
                if not any(p in lower for p in patterns):
                    missing.append(elem)
            else:
                # Fallback: simple substring match
                if key not in lower:
                    missing.append(elem)

        passed = len(missing) == 0
        if passed:
            detail = f"All required elements present: {', '.join(elements)}"
        else:
            detail = f"Missing required elements: {', '.join(missing)}"
        return CheckResult(passed=passed, violations=missing if missing else None, detail=detail)
