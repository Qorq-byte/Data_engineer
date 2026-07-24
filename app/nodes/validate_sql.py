"""ValidateSQLNode — multi-stage SQL validation (full implementation).

See SPEC §4.5.1 for the 5-stage validation pipeline. All five stages
are implemented:
  1. Syntax parsing (sqlglot dialect-aware)              — blocking
  2. Schema reference validation (table / column)        — blocking
  3. Type compatibility check                            — warnings only
  4. Performance analysis (static EXPLAIN heuristics)    — warnings only
  5. Business rule validation (BusinessRule enforcement) — warnings only

Stages 3–5 produce warnings that lower the score without affecting
``passed``, matching the validation state machine in the design docs.
"""

from __future__ import annotations

from app.core.sql_validator import SQLValidator
from app.nodes.agentic import AgenticNode
from app.nodes.base import NodeInput, NodeOutput


class ValidateSQLNode(AgenticNode):
    """Validates generated SQL across syntax, schema, and type dimensions.

    Consumes SQL candidates from shared context (placed there by
    ``GenerateSQLNode``) and produces a ``ValidationReport``.

    Output context keys:
        ``validation_report`` — ``ValidationReport``
        ``validation_passed`` — ``bool``
    """

    name = "validate_sql"
    description = (
        "Multi-stage SQL validation: syntax parsing, schema reference check, "
        "type compatibility analysis, performance heuristics, business rules"
    )

    def __init__(
        self,
        validator: SQLValidator | None = None,
        dialect: str = "ansi",
    ) -> None:
        super().__init__()
        self._validator = validator
        self.dialect = dialect

    @property
    def validator(self) -> SQLValidator:
        if self._validator is None:
            self._validator = SQLValidator(dialect=self.dialect)
        return self._validator

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def execute(self, input: NodeInput) -> NodeOutput:
        """Validate the primary SQL candidate.

        Expected ``input.context`` keys:
            - ``primary_sql``: The SQL string to validate.
            - ``schema``: Optional ``SchemaSnapshot`` for reference checks.
            - ``business_rules``: Optional ``list[BusinessRule]`` for Step 5.

        Expected ``input.config`` keys:
            - ``skip_syntax``: bool (default False)
            - ``skip_schema``: bool (default False)
            - ``skip_types``: bool (default False)
            - ``skip_performance``: bool (default False)
            - ``skip_rules``: bool (default False)
            - ``dialect``: str | None (override)
        """
        sql = input.context.get("primary_sql") or input.query_text
        schema = input.context.get("schema")
        business_rules = input.context.get("business_rules") or []

        skip_syntax = input.config.get("skip_syntax", False)
        skip_schema = input.config.get("skip_schema", False)
        skip_types = input.config.get("skip_types", False)
        skip_performance = input.config.get("skip_performance", False)
        skip_rules = input.config.get("skip_rules", False)
        dialect = input.config.get("dialect", self.dialect)

        # Use a validator with the right dialect
        validator = (
            self._validator
            if self._validator is not None
            else SQLValidator(dialect=dialect)
        )

        if validator.dialect != dialect and self._validator is None:
            validator = SQLValidator(dialect=dialect)

        try:
            report = await validator.validate(
                sql,
                schema=schema,
                rules=business_rules,
                skip_syntax=skip_syntax,
                skip_schema=skip_schema,
                skip_types=skip_types,
                skip_performance=skip_performance,
                skip_rules=skip_rules,
            )
        except Exception as exc:
            return NodeOutput(
                result={"passed": False, "score": 0.0},
                errors=[f"Validation failed: {exc}"],
                metadata={"status": "validation_error"},
            )

        perf_warnings = (
            report.plan_analysis.warnings if report.plan_analysis else []
        )

        return NodeOutput(
            result={
                "passed": report.passed,
                "score": report.score,
                "syntax_ok": report.syntax_ok,
                "schema_valid": report.schema_valid,
                "type_valid": report.type_valid,
                "business_valid": report.business_valid,
            },
            metadata={
                "status": "success",
                "passed": report.passed,
                "score": report.score,
                "syntax_errors_count": len(report.syntax_errors),
                "schema_errors_count": len(report.schema_errors),
                "type_warnings_count": len(report.type_errors),
                "perf_warnings_count": len(perf_warnings),
                "rule_warnings_count": len(report.business_warnings),
            },
            context={
                "validation_report": report,
                "validation_passed": report.passed,
            },
        )
