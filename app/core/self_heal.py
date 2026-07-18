"""Self-Healing Retry — automatic SQL correction loop.

See SPEC §4.7.5 and implementation-plan §4.7.5 for the full design.

Core loop (max 3 rounds):
  1. Analyse validation/execution errors.
  2. Build a correction prompt with the error context.
  3. Re-generate SQL via the generator (using L4_SELF_HEAL level).
  4. Re-validate.
  5. If still failing, repeat with updated error info.

Phase 2 MVP: error classification + prompt correction + retry loop.
Phase 3+: LLM-based error reasoning (ReflectionNode).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.models.query import SQR, SQLCandidate, ValidationReport

# ── Error analysis ────────────────────────────────────────────────────


class ErrorCategory:
    SYNTAX = "syntax"
    SCHEMA = "schema"
    TYPE = "type"
    EXECUTION = "execution"
    UNKNOWN = "unknown"


@dataclass
class ErrorAnalysis:
    """Breakdown of validation/execution errors by category."""

    syntax_errors: list[str] = field(default_factory=list)
    schema_errors: list[str] = field(default_factory=list)
    type_errors: list[str] = field(default_factory=list)
    execution_errors: list[str] = field(default_factory=list)
    unknown_errors: list[str] = field(default_factory=list)

    @property
    def total_errors(self) -> int:
        return (
            len(self.syntax_errors)
            + len(self.schema_errors)
            + len(self.type_errors)
            + len(self.execution_errors)
            + len(self.unknown_errors)
        )

    @property
    def is_blocking(self) -> bool:
        """Syntax and schema errors are blocking; type/execution are not."""
        return len(self.syntax_errors) > 0 or len(self.schema_errors) > 0

    def summary(self) -> str:
        """One-line summary of all errors."""
        parts: list[str] = []
        if self.syntax_errors:
            parts.append(f"Syntax: {'; '.join(self.syntax_errors[:2])}")
        if self.schema_errors:
            parts.append(f"Schema: {'; '.join(self.schema_errors[:2])}")
        if self.type_errors:
            parts.append(f"Type: {'; '.join(self.type_errors[:2])}")
        if self.execution_errors:
            parts.append(f"Execution: {'; '.join(self.execution_errors[:2])}")
        if self.unknown_errors:
            parts.append(f"Other: {'; '.join(self.unknown_errors[:2])}")
        return " | ".join(parts) if parts else "No errors"


# ── Self-Healing Retry engine ──────────────────────────────────────────


class SelfHealingRetry:
    """Automatic SQL correction via error analysis and prompt revision.

    Usage::

        healer = SelfHealingRetry(generator, validator)
        result = await healer.retry_until_valid(
            sqr, sql, errors, schema, domain
        )
        if result:
            print(f"Fixed in round {result.retry_round}")
    """

    MAX_ROUNDS = 3

    def __init__(
        self,
        generator: Any = None,   # SQLGenerator (avoid circular import)
        validator: Any = None,   # SQLValidator
    ) -> None:
        self.generator = generator
        self.validator = validator
        self._history: list[dict[str, Any]] = []

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def retry_until_valid(
        self,
        sqr: SQR,
        failed_sql: str,
        errors: list[str] | str,
        schema: Any = None,
        domain: Any = None,
        *,
        dialect: str | None = None,
    ) -> SQLCandidate | None:
        """Run the self-healing retry loop.

        Args:
            sqr: The **original** SQR (with the user's NL question).
            failed_sql: The SQL that failed validation or execution.
            errors: Error messages (list or single string).
            schema: Optional ``SchemaSnapshot``.
            domain: Optional ``DomainConfig``.
            dialect: Optional SQL dialect.

        Returns:
            A corrected ``SQLCandidate`` with ``retry_round`` and
            ``generation_mode="self_heal"``, or ``None`` if all rounds
            exhausted.
        """
        self._history = []

        error_list = [errors] if isinstance(errors, str) else list(errors)
        analysis = _analyse_errors(error_list, None)

        current_sql = failed_sql
        current_errors = error_list

        for round_num in range(1, self.MAX_ROUNDS + 1):
            # 1. Build error context — passed to PromptBuilder as
            #    *error_context*, which triggers L4_SELF_HEAL template.
            #    The original NL question stays in sqr.raw_text so the
            #    LLM sees a clean prompt instead of a double-wrapped one.
            error_context = self._build_error_context(
                sqr.raw_text, current_sql, current_errors, analysis
            )

            # 2. Re-generate using error_context (triggers L4_SELF_HEAL)
            if self.generator is not None:
                candidates = await self.generator.generate(
                    sqr,  # original SQR — keeps the NL question intact
                    schema=schema,
                    domain=domain,
                    num_candidates=1,
                    dialect=dialect,
                    error_context=error_context,
                )
                if not candidates:
                    self._history.append({
                        "round": round_num, "sql": current_sql,
                        "errors": current_errors, "result": "no_candidates",
                    })
                    continue
                corrected_sql = candidates[0].sql_text
            else:
                # No generator available — can't retry
                self._history.append({
                    "round": round_num, "sql": current_sql,
                    "errors": current_errors, "result": "no_generator",
                })
                return None

            # 3. Validate corrected SQL
            if self.validator is not None:
                report = await self.validator.validate(
                    corrected_sql, schema=schema
                )
                if report.passed:
                    self._history.append({
                        "round": round_num, "sql": corrected_sql,
                        "errors": [], "result": "passed",
                    })
                    return SQLCandidate(
                        id=f"heal_{round_num}",
                        sql_text=corrected_sql,
                        confidence=max(0.7 - round_num * 0.1, 0.3),
                        generation_mode="self_heal",
                        reasoning=f"Corrected after {round_num} retry(s)",
                        dialect=dialect or "ansi",
                    )

                # Prepare for next round
                current_sql = corrected_sql
                current_errors = (
                    report.syntax_errors
                    + report.schema_errors
                    + report.type_errors
                )
                analysis = _analyse_errors(current_errors, report)
            else:
                # No validator — accept the correction
                self._history.append({
                    "round": round_num, "sql": corrected_sql,
                    "errors": [], "result": "accepted_no_validator",
                })
                return SQLCandidate(
                    id=f"heal_{round_num}",
                    sql_text=corrected_sql,
                    confidence=0.7,
                    generation_mode="self_heal",
                    dialect=dialect or "ansi",
                )

            self._history.append({
                "round": round_num, "sql": corrected_sql,
                "errors": current_errors, "result": "still_failing",
            })

        return None  # All rounds exhausted

    def _build_error_context(
        self,
        nl_text: str,
        sql: str,
        errors: list[str],
        analysis: ErrorAnalysis,
    ) -> str:
        """Build error context for the L4_SELF_HEAL template.

        Returns a compact error description — the original NL question
        is NOT embedded here because it already lives in ``sqr.raw_text``
        which the ``PromptBuilder`` injects as ``{question}``.
        """
        error_text = "\n".join(f"  - {e}" for e in errors[:5])

        fix_instructions = _build_fix_instructions(analysis)

        return f"""Failed SQL:
{sql}

Errors:
{error_text}

{fix_instructions}"""

    @property
    def history(self) -> list[dict[str, Any]]:
        """Retry history for debugging / audit."""
        return self._history


# ── Error analysis helpers ─────────────────────────────────────────────


def _analyse_errors(
    errors: list[str], report: ValidationReport | None
) -> ErrorAnalysis:
    """Classify errors into categories by keyword matching."""
    analysis = ErrorAnalysis()

    for err in errors:
        err_upper = err.upper()
        if "SYNTAX" in err_upper or "PARSE" in err_upper:
            analysis.syntax_errors.append(err)
        elif "SCHEMA" in err_upper or "TABLE" in err_upper or "COLUMN" in err_upper:
            analysis.schema_errors.append(err)
        elif "TYPE" in err_upper or "COMPATIB" in err_upper:
            analysis.type_errors.append(err)
        elif "EXECUTION" in err_upper or "ERROR:" in err_upper:
            analysis.execution_errors.append(err)
        else:
            analysis.unknown_errors.append(err)

    return analysis


def _build_fix_instructions(analysis: ErrorAnalysis) -> str:
    """Generate category-specific fix instructions."""
    parts: list[str] = []

    if analysis.syntax_errors:
        parts.append(
            "- SYNTAX: Check SQL syntax — verify keywords, "
            "parentheses, quotes, and statement structure are correct."
        )
    if analysis.schema_errors:
        parts.append(
            "- SCHEMA: Verify all table and column names exist in the "
            "database schema. Check for typos and use fully qualified "
            "names (schema.table.column) where needed."
        )
    if analysis.type_errors:
        parts.append(
            "- TYPE: Ensure comparisons are between compatible types. "
            "Use CAST() or ::type to convert as needed."
        )
    if analysis.execution_errors:
        parts.append(
            "- EXECUTION: The query failed at runtime. Check for "
            "division by zero, invalid date formats, constraint "
            "violations, or NULL handling issues."
        )
    if not parts:
        parts.append(
            "- Review the SQL carefully and correct any issues."
        )

    return "\n".join(parts)
