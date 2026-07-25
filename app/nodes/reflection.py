r"""ReflectionNode — analyse SQL execution results and decide whether revision is needed.

See SPEC §3.4.2 and implementation-plan §4.4.6 for the full specification.

This node sits after ``execute_sql`` in the **reflection** workflow plan and
implements the feedback loop:

    schema_linking → generate_sql → execute_sql → **reflect**
                                                       ↓ (needs_revision)
                                                     revise → execute_sql → reflect
                                                       ↓ (pass)
                                                     done

The node is **deterministic** — it classifies errors by keyword matching and
generates fix hints without calling an LLM.  The actual SQL correction is
delegated to the ``revise`` node (``generate_sql`` in ``self_heal`` mode).

Input context keys (expected from upstream nodes):
    ``sql``: The SQL string that was executed.
    ``last_execution``: Dict result from ``ExecuteSQLNode``.
    ``query_text``: The original NL query.
    ``errors``: Optional list of error strings collected by upstream nodes.

Output context keys:
    ``reflection`` — ``ReflectionOutput`` with full analysis.
    ``error_analysis`` — Short error summary string for the revise node.
    ``fix_hints`` — List of fix hints for the revise node.
    ``needs_revision`` — Boolean flag consumed by ``WorkflowRunner``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.nodes.base import BaseNode, NodeInput, NodeOutput

# ── Error category constants ────────────────────────────────────────────


class ErrorCategory:
    """Well-known error categories used by the keyword classifier."""

    SYNTAX = "syntax"
    SCHEMA = "schema"
    TYPE = "type"
    EXECUTION = "execution"
    TIMEOUT = "timeout"
    EMPTY_RESULT = "empty_result"
    NONE = "none"


# ── Output model ─────────────────────────────────────────────────────────


@dataclass
class ReflectionOutput:
    """Structured result of a reflection pass over SQL execution output.

    This is the ``result`` field of ``NodeOutput`` and is also written to
    ``context["reflection"]`` so the ``revise`` node can consume it.
    """

    # ── What was analysed ────────────────────────────────────────────
    sql: str = ""
    original_query: str = ""

    # ── Execution assessment ─────────────────────────────────────────
    execution_success: bool = False
    row_count: int = 0
    has_results: bool = False
    truncated: bool = False

    # ── Error classification ─────────────────────────────────────────
    error_category: str = ErrorCategory.NONE
    error_summary: str = ""
    error_details: list[str] = field(default_factory=list)

    # ── Decision ─────────────────────────────────────────────────────
    needs_revision: bool = False
    confidence: float = 1.0

    # ── Fix strategy ─────────────────────────────────────────────────
    fix_strategy: str = ""          # "rewrite" | "fix_syntax" | "fix_schema" | ...
    fix_hints: list[str] = field(default_factory=list)
    suggested_approach: str = ""


# ── Keyword-based error classifier ──────────────────────────────────────


# Patterns → (category, fix_strategy)
_ERROR_PATTERNS: list[tuple[str, str, str]] = [
    # ── Syntax errors ────────────────────────────────────────────────
    ("syntax error", ErrorCategory.SYNTAX, "fix_syntax"),
    ("parse error", ErrorCategory.SYNTAX, "fix_syntax"),
    ("operationalerror", ErrorCategory.SYNTAX, "fix_syntax"),   # SQLite
    ("unexpected token", ErrorCategory.SYNTAX, "fix_syntax"),
    ("missing keyword", ErrorCategory.SYNTAX, "fix_syntax"),
    ("malformed", ErrorCategory.SYNTAX, "fix_syntax"),
    # ── Schema errors ────────────────────────────────────────────────
    ("no such table", ErrorCategory.SCHEMA, "fix_schema"),
    ("no such column", ErrorCategory.SCHEMA, "fix_schema"),
    # "column … not found" / "table … not found" / "relation … does not exist"
    ("not found", ErrorCategory.SCHEMA, "fix_schema"),
    ("does not exist", ErrorCategory.SCHEMA, "fix_schema"),
    ("doesn't exist", ErrorCategory.SCHEMA, "fix_schema"),      # "column … doesn't exist"
    ("ambiguous column", ErrorCategory.SCHEMA, "fix_schema"),
    ("is ambiguous", ErrorCategory.SCHEMA, "fix_schema"),       # "column reference … is ambiguous"
    ("schema_error", ErrorCategory.SCHEMA, "fix_schema"),
    # ── Type errors ──────────────────────────────────────────────────
    ("type mismatch", ErrorCategory.TYPE, "add_cast"),
    ("type_mismatch", ErrorCategory.TYPE, "add_cast"),
    ("datatype mismatch", ErrorCategory.TYPE, "add_cast"),
    ("incompatible", ErrorCategory.TYPE, "add_cast"),
    ("cannot compare", ErrorCategory.TYPE, "add_cast"),
    ("cannot cast", ErrorCategory.TYPE, "add_cast"),
    # ── Timeout ──────────────────────────────────────────────────────
    ("timeout", ErrorCategory.TIMEOUT, "optimize"),
    ("timed out", ErrorCategory.TIMEOUT, "optimize"),
    ("took too long", ErrorCategory.TIMEOUT, "optimize"),
    # ── Execution / runtime errors ───────────────────────────────────
    ("execution error", ErrorCategory.EXECUTION, "rewrite"),
    ("division by zero", ErrorCategory.EXECUTION, "rewrite"),
    ("null value", ErrorCategory.EXECUTION, "rewrite"),
    ("constraint", ErrorCategory.EXECUTION, "rewrite"),
    ("violation", ErrorCategory.EXECUTION, "rewrite"),
    ("out of range", ErrorCategory.EXECUTION, "rewrite"),
    ("overflow", ErrorCategory.EXECUTION, "rewrite"),
    ("permission denied", ErrorCategory.EXECUTION, "rewrite"),
    ("access denied", ErrorCategory.EXECUTION, "rewrite"),
    ("blocked", ErrorCategory.EXECUTION, "rewrite"),
    ("write operation", ErrorCategory.EXECUTION, "rewrite"),
    ("read_only", ErrorCategory.EXECUTION, "rewrite"),
]


def _classify_error(message: str) -> tuple[str, str]:
    """Classify a single error message into (category, fix_strategy).

    Uses case-insensitive keyword matching against the well-known
    ``_ERROR_PATTERNS`` table.  Returns ``(ErrorCategory.UNKNOWN, "rewrite")``
    when no pattern matches.
    """
    lower = message.lower()
    for pattern, category, strategy in _ERROR_PATTERNS:
        if pattern in lower:
            return category, strategy
    return "unknown", "rewrite"


def _build_fix_hints(category: str, error_details: list[str]) -> list[str]:
    """Generate actionable fix hints for a given error category."""
    hints: list[str] = []

    if category == ErrorCategory.SYNTAX:
        hints.append("Check SQL syntax — verify keywords, parentheses, quotes")
        hints.append("Ensure all identifiers are properly escaped for the target dialect")
        hints.append("Validate the statement structure (SELECT ... FROM ... WHERE ...)")
    elif category == ErrorCategory.SCHEMA:
        hints.append("Verify all table and column names exist in the database schema")
        hints.append("Use fully qualified names (schema.table.column) where needed")
        hints.append("Check for typos in table/column identifiers")
    elif category == ErrorCategory.TYPE:
        hints.append("Ensure comparisons are between compatible types")
        hints.append("Use CAST() or ::type to convert operands as needed")
        hints.append("Check that aggregation functions receive numeric arguments")
    elif category == ErrorCategory.TIMEOUT:
        hints.append("Add or refine WHERE clauses to reduce scanned rows")
        hints.append("Consider adding LIMIT to bound result size")
        hints.append("Check that relevant indexes exist for JOIN/WHERE columns")
    elif category == ErrorCategory.EMPTY_RESULT:
        hints.append("Broaden filter conditions — the current WHERE may be too strict")
        hints.append("Verify that date ranges and thresholds match the user's intent")
        hints.append("Consider whether the correct tables are being queried")
    elif category == ErrorCategory.EXECUTION:
        hints.append("Check for runtime issues: division by zero, NULL propagation")
        hints.append("Use COALESCE() or NULLIF() to handle NULL safely")
        hints.append("Verify that data types match the intended operations")
    else:  # unknown
        hints.append("Review the SQL carefully and correct any issues")
        hints.append("Consider rewriting the query from a different angle")

    return hints


# ── Node ─────────────────────────────────────────────────────────────────


class ReflectionNode(BaseNode):
    """Analyse SQL execution output and determine whether revision is needed.

    This node is **not** an ``AgenticNode`` — the error classification and
    fix-hint generation are deterministic (keyword matching).  No LLM is
    called during reflection; the actual correction is done by the
    downstream ``revise`` node.

    Input config keys:
        ``auto_revise``: bool — if False, always report needs_revision=False
            (default True).  Useful for plan-level feature flags.

    Output context keys:
        ``reflection`` — ``ReflectionOutput`` full analysis.
        ``error_analysis`` — Short error summary string.
        ``fix_hints`` — List of actionable fix hints (str).
        ``needs_revision`` — Boolean consumed by ``WorkflowRunner``.
    """

    name = "reflection"
    description = (
        "Analyse SQL execution results, classify errors, and decide "
        "whether the query should be revised via the feedback loop"
    )

    # ── Lifecycle ──────────────────────────────────────────────────────

    async def execute(self, input: NodeInput) -> NodeOutput:
        """Run reflection analysis on the upstream execution result.

        Expected ``input.context`` keys:
            ``sql``: The SQL that was executed.
            ``last_execution``: Dict from ``ExecuteSQLNode``.
            ``query_text``: Original NL query.
        """
        sql: str = input.context.get("sql", "")
        query_text: str = input.context.get("query_text", input.query_text)
        last_exec: dict[str, Any] | None = input.context.get("last_execution")
        auto_revise: bool = input.config.get("auto_revise", True)

        # Guard: coerce non-dict last_execution to None
        if last_exec is not None and not isinstance(last_exec, dict):
            last_exec = None

        # ── No execution result at all ────────────────────────────────
        if last_exec is None:
            return self._no_execution_result(sql, query_text)

        # ── Collect errors from multiple sources ──────────────────────
        all_errors: list[str] = []

        # Errors explicitly passed in context (e.g., from validate_sql)
        ctx_errors: list[str] = input.context.get("errors", [])
        if isinstance(ctx_errors, list):
            all_errors.extend(ctx_errors)
        elif isinstance(ctx_errors, str):
            all_errors.append(ctx_errors)

        # Errors from the execution result itself
        exec_errors = last_exec.get("errors", [])
        if isinstance(exec_errors, list):
            all_errors.extend(exec_errors)
        elif isinstance(exec_errors, str):
            all_errors.append(exec_errors)

        # Check if result contains an error indicator
        exec_status = last_exec.get("status", "success")
        exec_error_msg = last_exec.get("error", "")

        has_errors = len(all_errors) > 0
        is_error_status = exec_status in ("error", "execution_error", "blocked", "timeout")
        has_exec_error = bool(exec_error_msg)

        # ── Build reflection output ───────────────────────────────────
        output = ReflectionOutput(
            sql=sql,
            original_query=query_text,
            row_count=last_exec.get("row_count", 0),
            has_results=last_exec.get("row_count", 0) > 0,
            truncated=last_exec.get("truncated", False),
        )

        # ── Case 1: Successful execution with results ─────────────────
        if not has_errors and not is_error_status and not has_exec_error:
            # Check for empty result first (sub-case of success)
            if output.row_count == 0:
                output.execution_success = True  # Query ran fine, just 0 rows
                output.error_category = ErrorCategory.EMPTY_RESULT
                output.error_summary = (
                    "Query returned 0 rows — filter conditions may be too strict"
                )
                output.needs_revision = auto_revise
                output.confidence = 0.6
                output.fix_strategy = "broaden_filters"
                output.fix_hints = _build_fix_hints(ErrorCategory.EMPTY_RESULT, [])
                output.suggested_approach = (
                    "Loosen WHERE conditions, expand date ranges, or remove overly "
                    "restrictive filters while maintaining query intent"
                )

                return NodeOutput(
                    result=output,
                    metadata={
                        "status": "empty_result",
                        "needs_revision": output.needs_revision,
                        "error_category": ErrorCategory.EMPTY_RESULT,
                    },
                    context={
                        "reflection": output,
                        "error_analysis": output.error_summary,
                        "fix_hints": output.fix_hints,
                        "needs_revision": output.needs_revision,
                    },
                )

            # Genuine success with results
            output.execution_success = True
            output.error_category = ErrorCategory.NONE
            output.needs_revision = False
            output.confidence = 1.0
            output.error_summary = "Execution succeeded"

            return NodeOutput(
                result=output,
                metadata={
                    "status": "success",
                    "needs_revision": False,
                    "error_category": ErrorCategory.NONE,
                },
                context={
                    "reflection": output,
                    "error_analysis": output.error_summary,
                    "fix_hints": [],
                    "needs_revision": False,
                },
            )

        # ── Case 2: Errors present — classify and recommend ───────────
        if has_exec_error and exec_error_msg not in all_errors:
            all_errors.append(exec_error_msg)

        # If status indicates error but no specific messages, add a synthetic one
        if is_error_status and not all_errors:
            all_errors.append(f"execution failed with status: {exec_status}")

        # Classify the first actionable error
        category, strategy = ErrorCategory.NONE, "rewrite"
        if all_errors:
            category, strategy = _classify_error(all_errors[0])
            # If the first error is unknown, scan all for a known one
            if category == "unknown":
                for err in all_errors[1:]:
                    cat, strat = _classify_error(err)
                    if cat != "unknown":
                        category, strategy = cat, strat
                        break

        output.error_category = category
        output.error_details = all_errors
        output.error_summary = "; ".join(all_errors[:3])
        output.needs_revision = auto_revise
        output.confidence = 0.8
        output.fix_strategy = strategy
        output.fix_hints = _build_fix_hints(category, all_errors)
        output.suggested_approach = _build_suggested_approach(
            category, strategy, all_errors
        )

        return NodeOutput(
            result=output,
            metadata={
                "status": "error_analyzed",
                "needs_revision": output.needs_revision,
                "error_category": category,
                "error_count": len(all_errors),
                "fix_strategy": strategy,
            },
            context={
                "reflection": output,
                "error_analysis": output.error_summary,
                "fix_hints": output.fix_hints,
                "needs_revision": output.needs_revision,
            },
        )

    # ── Helpers ────────────────────────────────────────────────────────

    @staticmethod
    def _no_execution_result(sql: str, query_text: str) -> NodeOutput:
        """Build output when there is no execution result at all (e.g.
        execute_sql was skipped because of a prior error)."""
        output = ReflectionOutput(
            sql=sql,
            original_query=query_text,
            error_category="unknown",
            error_summary="No execution result available — upstream node may have failed",
            needs_revision=True,
            confidence=0.5,
            fix_strategy="rewrite",
            fix_hints=[
                "Check the upstream nodes for errors",
                "Verify the query is valid before execution",
            ],
            suggested_approach="Re-examine the query and upstream outputs before re-executing",
        )
        return NodeOutput(
            result=output,
            metadata={"status": "no_execution", "needs_revision": True},
            context={
                "reflection": output,
                "error_analysis": output.error_summary,
                "fix_hints": output.fix_hints,
                "needs_revision": True,
            },
        )

    # ── Context merge ──────────────────────────────────────────────────

    async def update_context(
        self, output: NodeOutput, shared_context: dict[str, Any]
    ) -> dict[str, Any]:
        """Merge reflection results into shared context.

        In addition to the standard merge, ensures ``needs_revision``,
        ``error_analysis``, and ``fix_hints`` are present so the
        ``revise`` node can consume them directly.
        """
        merged = {**shared_context}
        if output.context:
            merged.update(output.context)

        # Ensure fix hints are always a list
        if "fix_hints" not in merged:
            merged["fix_hints"] = []

        return merged


# ── Suggested approach builder ──────────────────────────────────────────


def _build_suggested_approach(
    category: str, strategy: str, errors: list[str]
) -> str:
    """Build a one-sentence suggested approach based on error category."""
    if category == ErrorCategory.SYNTAX:
        return "Fix the SQL syntax error and regenerate"
    elif category == ErrorCategory.SCHEMA:
        return "Correct the table/column references to match the database schema"
    elif category == ErrorCategory.TYPE:
        return "Add explicit type casts to make comparisons compatible"
    elif category == ErrorCategory.TIMEOUT:
        return "Optimize the query with better filtering, indexes, or LIMIT"
    elif category == ErrorCategory.EXECUTION:
        return "Handle runtime edge cases (NULL, division by zero, constraints)"
    elif category == ErrorCategory.EMPTY_RESULT:
        return "Broaden filter conditions while preserving query intent"
    else:
        return "Rewrite the query addressing the reported errors"
