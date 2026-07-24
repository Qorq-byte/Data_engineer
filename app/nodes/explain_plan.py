"""ExplainPlanNode — EXPLAIN execution + LLM interpretation.

See SPEC §4.12.4 and implementation-plan §4.4.4 (EXPLAIN support).

This node:
  1. Receives SQL and a database connection from shared context.
  2. Executes ``EXPLAIN`` (dialect-appropriate: ``EXPLAIN QUERY PLAN`` for
     SQLite, ``EXPLAIN`` for others).
  3. Parses the raw output into a structured ``PlanAnalysis``.
  4. Builds an LLM prompt with the raw EXPLAIN output and structured analysis.
  5. Calls the LLM for a human-readable interpretation and optimization
     recommendations.

Phase 4 deliverable: completes the EXPLAIN → LLM interpretation pipeline
that was stubbed in Phase 1-2 ExecuteSQLNode.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.llm.router import LiteLLMRouter, RouterConfig
from app.models.query import PlanAnalysis
from app.nodes.agentic import AgenticNode
from app.nodes.base import NodeInput, NodeOutput
from app.nodes.execute_sql import ExecuteSQLNode as _ExecSQL

# ── Output data class ────────────────────────────────────────────────────


@dataclass
class ExplainPlanOutput:
    """Structured output from the explain_plan node.

    Attributes:
        raw_explain: Raw EXPLAIN execution result (columns + rows dict).
        plan_analysis: Structured ``PlanAnalysis`` with scan/join/warnings.
        interpretation: Human-readable LLM interpretation of the plan.
        recommendations: Specific optimization suggestions from the LLM.
    """

    raw_explain: dict = field(default_factory=dict)
    plan_analysis: PlanAnalysis = field(default_factory=PlanAnalysis)
    interpretation: str = ""
    recommendations: list[str] = field(default_factory=list)


# ── Prompt templates ─────────────────────────────────────────────────────

_EXPLAIN_SYSTEM_PROMPT = """\
You are a database performance expert. Your task is to interpret SQL execution \
plans and provide clear, actionable advice.

Given a raw EXPLAIN output and a structured analysis (scan types, join types, \
warnings), produce:
1. A concise, human-readable interpretation of what the database will do when \
   executing this query (2-4 sentences).
2. A list of specific optimization recommendations (3-5 items). Prioritize the \
   most impactful changes.

Output your response as JSON with exactly these keys:
  - "interpretation": string (plain language explanation)
  - "recommendations": list of strings (actionable suggestions)

Return ONLY valid JSON, no markdown fences or preamble."""

_EXPLAIN_USER_TEMPLATE = """\
SQL Query:
{sql}

Raw EXPLAIN Output:
{raw_explain}

Structured Analysis:
- Scan types: {scan_types}
- Join types: {join_types}
- Estimated rows: {estimated_rows}
- Full table scan detected: {has_full_scan}
- Existing warnings: {warnings}

Please interpret this execution plan and provide optimization recommendations."""


# ── Node implementation ──────────────────────────────────────────────────


class ExplainPlanNode(AgenticNode):
    """Execute EXPLAIN on SQL and provide LLM-based interpretation.

    Consumes ``sql`` and ``connection`` from shared context; produces
    ``explain_raw``, ``plan_analysis``, ``plan_interpretation``, and
    ``plan_recommendations``.

    Expected ``input.context`` keys:
        - ``connection``: A native database connection object (required).
        - ``sql``: The SQL string to explain (falls back to ``query_text``).

    Expected ``input.config`` keys:
        - ``model``: str | None (LLM model override).
        - ``skip_llm``: bool (default False) — skip LLM interpretation;
          return raw EXPLAIN and static analysis only.

    Output ``context`` keys:
        - ``explain_raw`` — raw EXPLAIN output dict.
        - ``plan_analysis`` — ``PlanAnalysis`` dataclass.
        - ``plan_interpretation`` — LLM interpretation string.
        - ``plan_recommendations`` — list of optimization suggestion strings.
    """

    name = "explain_plan"
    description = "Execute EXPLAIN on SQL and provide LLM-based interpretation"

    def __init__(self, router: LiteLLMRouter | None = None) -> None:
        super().__init__()
        self._router = router

    # ── Lazy accessor ────────────────────────────────────────────────

    @property
    def router(self) -> LiteLLMRouter:
        if self._router is None:
            self._router = LiteLLMRouter(RouterConfig(providers={}), mock_mode=True)
        return self._router

    # ── Main execute ─────────────────────────────────────────────────

    async def execute(self, input: NodeInput) -> NodeOutput:
        conn = input.context.get("connection")
        if conn is None:
            return NodeOutput(
                result=None,
                errors=["No database connection in context"],
                metadata={"status": "no_connection"},
            )

        sql = (input.context.get("sql") or input.query_text or "").strip()
        if not sql:
            return NodeOutput(
                result=None,
                errors=["Empty SQL statement"],
                metadata={"status": "empty_sql"},
            )

        model = input.config.get("model")
        skip_llm = input.config.get("skip_llm", False)

        try:
            # ── Step 1: Execute EXPLAIN ──────────────────────────────
            raw_explain = _ExecSQL._execute_explain(conn, sql)

            # ── Step 2: Parse into PlanAnalysis ──────────────────────
            plan_analysis = _ExecSQL._parse_explain_output(raw_explain)
            # Also run static analysis on the SQL text
            static = _ExecSQL._static_plan_analysis(sql)
            plan_analysis = _merge_plan_analyses(plan_analysis, static)

            # ── Step 3: LLM interpretation (optional) ────────────────
            interpretation = ""
            recommendations: list[str] = []

            if not skip_llm:
                interpretation, recommendations = await self._llm_interpret(
                    sql, raw_explain, plan_analysis, model
                )

            output = ExplainPlanOutput(
                raw_explain=raw_explain,
                plan_analysis=plan_analysis,
                interpretation=interpretation,
                recommendations=recommendations,
            )

            return NodeOutput(
                result=output,
                metadata={
                    "status": "success",
                    "has_full_scan": plan_analysis.has_full_scan,
                    "scan_types": plan_analysis.scan_types,
                    "join_types": plan_analysis.join_types,
                    "estimated_rows": plan_analysis.estimated_rows,
                    "warning_count": len(plan_analysis.warnings),
                    "recommendation_count": len(recommendations),
                    "llm_used": not skip_llm,
                },
                context={
                    "explain_raw": raw_explain,
                    "plan_analysis": plan_analysis,
                    "plan_interpretation": interpretation,
                    "plan_recommendations": recommendations,
                },
            )

        except Exception as exc:
            return NodeOutput(
                result=None,
                errors=[f"Explain plan failed: {exc}"],
                metadata={"status": "explain_error", "error_type": type(exc).__name__},
            )

    # ── LLM interpretation ───────────────────────────────────────────

    async def _llm_interpret(
        self,
        sql: str,
        raw_explain: dict,
        plan: PlanAnalysis,
        model: str | None = None,
    ) -> tuple[str, list[str]]:
        """Build prompt and call LLM for plan interpretation.

        Returns (interpretation, recommendations) tuple.
        On LLM failure, returns empty strings and empty lists so the
        node can still deliver raw EXPLAIN output.
        """
        # Format raw EXPLAIN as readable text
        raw_text = _format_explain_rows(raw_explain)

        user_message = _EXPLAIN_USER_TEMPLATE.format(
            sql=sql[:2000],  # Truncate very long SQL
            raw_explain=raw_text[:3000],
            scan_types=", ".join(plan.scan_types) if plan.scan_types else "none detected",
            join_types=", ".join(plan.join_types) if plan.join_types else "none detected",
            estimated_rows=plan.estimated_rows,
            has_full_scan=plan.has_full_scan,
            warnings=", ".join(plan.warnings) if plan.warnings else "none",
        )

        messages: list[dict[str, str]] = [
            {"role": "system", "content": _EXPLAIN_SYSTEM_PROMPT},
            {"role": "user", "content": user_message},
        ]

        try:
            response = await self.router.complete(
                messages=messages,
                model=model or self.router.config.default_model,
                temperature=0.3,
                n=1,
            )
            content = _extract_response_text(response)
            return _parse_llm_json(content)
        except Exception:
            # LLM failure is non-fatal — return empty interpretation
            return "", []


# ── Helpers ───────────────────────────────────────────────────────────────


def _format_explain_rows(raw: dict) -> str:
    """Format raw EXPLAIN output as readable text for LLM consumption."""
    columns = raw.get("columns", [])
    rows = raw.get("rows", [])

    if not rows:
        return "(empty — EXPLAIN returned no rows)"

    lines: list[str] = []
    # Column header
    if columns:
        lines.append(" | ".join(str(c) for c in columns))
        lines.append("-" * len(lines[0]))

    for row in rows:
        lines.append(" | ".join(str(cell) for cell in row))

    return "\n".join(lines)


def _merge_plan_analyses(explain: PlanAnalysis, static: PlanAnalysis) -> PlanAnalysis:
    """Merge EXPLAIN-based and static PlanAnalysis, deduplicating warnings."""
    merged = PlanAnalysis(
        scan_types=list(set(explain.scan_types + static.scan_types)),
        estimated_rows=max(explain.estimated_rows, static.estimated_rows),
        join_types=list(set(explain.join_types + static.join_types)),
        has_full_scan=explain.has_full_scan or static.has_full_scan,
        warnings=list(set(explain.warnings + static.warnings)),
    )
    return merged


def _extract_response_text(response: object) -> str:
    """Extract text content from an LLM response object.

    Handles litellm response format (choices[0].message.content) and
    mock response format (dict with choices).
    """
    if isinstance(response, str):
        return response
    if isinstance(response, dict):
        choices = response.get("choices", [])
        if choices:
            msg = choices[0].get("message", {})
            return str(msg.get("content", ""))
    if hasattr(response, "choices"):
        choices = getattr(response, "choices", [])
        if choices:
            msg = getattr(choices[0], "message", None)
            if msg and hasattr(msg, "content"):
                return str(getattr(msg, "content", ""))
            elif isinstance(msg, dict):
                return str(msg.get("content", ""))
    return str(response)


def _parse_llm_json(content: str) -> tuple[str, list[str]]:
    """Parse LLM JSON response into (interpretation, recommendations).

    Returns empty values on parse failure (LLM may not always return
    valid JSON).
    """
    import json

    # Strip markdown fences if present
    text = content.strip()
    if text.startswith("```"):
        # Remove opening fence
        text = text.split("\n", 1)[-1] if "\n" in text else text[3:]
        # Remove closing fence
        if text.endswith("```"):
            text = text[:-3]
        text = text.strip()

    try:
        data = json.loads(text)
        interpretation = str(data.get("interpretation", ""))
        recommendations = data.get("recommendations", [])
        if isinstance(recommendations, list):
            recommendations = [str(r) for r in recommendations]
        else:
            recommendations = []
        return interpretation, recommendations
    except (json.JSONDecodeError, TypeError, AttributeError):
        # Fallback: treat the whole content as interpretation
        return content.strip(), []
