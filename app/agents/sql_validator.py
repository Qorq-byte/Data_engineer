"""Validation Agent — wraps the multi-stage SQL validation pipeline.

See SPEC §4.11.2 (agent role: Validation) and implementation plan §4.7.4.

Wraps :class:`app.core.sql_validator.SQLValidator` which performs:
  1. Syntax parsing (sqlglot dialect-aware)
  2. Schema reference validation (table/column existence)
  3. Type compatibility check
"""

from __future__ import annotations

import time
from typing import Any

from app.agents.base import AgentResult, BaseAgent


class ValidationAgent(BaseAgent):
    """Agent that validates a SQL candidate against syntax, schema, and type rules.

    Wraps :class:`app.core.sql_validator.SQLValidator`.

    Usage::

        agent = ValidationAgent(validator=SQLValidator())
        result = await agent.execute(candidate_sql, context={"schema": db_schema})
        report: ValidationReport = result.data.get("report")
    """

    name = "sql_validator"
    description = (
        "Validate SQL syntax, schema references, and type compatibility. "
        "Returns a ValidationReport with pass/fail status, errors, and warnings."
    )

    def __init__(
        self,
        validator: Any = None,
        config: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(config)
        if validator is None:
            from app.core.sql_validator import SQLValidator as _SQLValidator

            validator = _SQLValidator()
        self._validator = validator

    async def execute(self, input: Any, context: dict[str, Any]) -> AgentResult:
        """Validate *input* (SQL string or dict with 'sql' key).

        Args:
            input: SQL text string, or dict with ``sql`` key.
            context: dict with ``schema`` (SchemaSnapshot) and optionally ``domain``.

        Returns:
            AgentResult.data = dict with ``report`` (ValidationReport) and ``passed`` (bool).
        """
        t0 = time.perf_counter()
        try:
            sql = self._resolve_sql(input, context)
            schema = context.get("schema")

            from app.models.query import ValidationReport

            report: ValidationReport = await self._validator.validate(sql, schema)

            elapsed = (time.perf_counter() - t0) * 1000
            return self._ok(
                {"report": report, "passed": report.passed},
                latency_ms=round(elapsed, 2),
                passed=report.passed,
                score=report.score,
                error_count=(
                    len(report.syntax_errors)
                    + len(report.schema_errors)
                    + len(report.type_errors)
                ),
                warning_count=len(report.warnings),
            )
        except Exception as exc:
            elapsed = (time.perf_counter() - t0) * 1000
            return self._error(str(exc), latency_ms=round(elapsed, 2))

    # ── helpers ────────────────────────────────────────────────────────

    @staticmethod
    def _resolve_sql(input: Any, context: dict[str, Any]) -> str:
        """Extract SQL string from input or context."""
        if isinstance(input, str):
            return input
        if isinstance(input, dict):
            return input.get("sql") or input.get("query_text") or input.get("text") or ""
        if hasattr(input, "sql_text"):
            return input.sql_text  # SQLCandidate
        return str(input)
