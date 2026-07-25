"""RespondNode — assemble workflow results into a user-facing reply.

See SPEC §3.4.2 (chat_agentic plan) — the final ``respond`` step.

Consumes the accumulated shared context (SQL, execution result, validation
report) and produces a natural language response for the end user.

Two modes:
  - **LLM mode** (router injected): builds a prompt with the original
    question, generated SQL, and a preview of the result rows, then asks
    the LLM to write the reply in the user's language.
  - **Template mode** (router is ``None``): deterministic template reply
    including row count, a preview of the first rows, the SQL, and the
    validation status. Never raises — missing context degrades gracefully.
"""

from __future__ import annotations

from typing import Any

from app.llm.router import LiteLLMRouter
from app.nodes.agentic import AgenticNode
from app.nodes.base import NodeInput, NodeOutput

DEFAULT_PREVIEW_ROWS = 5


class RespondNode(AgenticNode):
    """Compose the final user-facing natural language response.

    Expected ``input.context`` keys (all optional — missing keys degrade
    to a sensible fallback reply):
        - ``sql`` / ``primary_sql``: The generated SQL.
        - ``last_execution`` / ``execution_result``: Execution result dict
          (``columns`` / ``rows`` / ``row_count`` / ``truncated``).
        - ``validation_report``: ``ValidationReport`` (or dict).
        - ``query_text``: The original user question.
        - ``language``: ``"zh"`` (default) or ``"en"``.

    Expected ``input.config`` keys:
        - ``language``: Override the context language.
        - ``preview_rows``: Rows to include in the preview (default 5).
        - ``model``: Optional LLM model override (LLM mode only).

    Output:
        ``NodeOutput.result`` — ``{"response": str, "used_llm": bool}``
        ``context["response"]`` — the response text.
    """

    name = "respond"
    description = "Assemble SQL, execution results, and validation into a user-facing reply"

    def __init__(self, router: LiteLLMRouter | None = None) -> None:
        super().__init__()
        self._router = router

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def execute(self, input: NodeInput) -> NodeOutput:
        ctx = input.context
        sql = ctx.get("sql") or ctx.get("primary_sql")
        execution = ctx.get("last_execution") or ctx.get("execution_result")
        report = ctx.get("validation_report")
        question = ctx.get("query_text") or input.query_text
        language = self._resolve_language(input)
        preview_rows = int(input.config.get("preview_rows", DEFAULT_PREVIEW_ROWS))

        used_llm = False
        response: str | None = None

        if self._router is not None:
            response = await self._llm_response(
                question=question,
                sql=sql,
                execution=execution,
                report=report,
                language=language,
                preview_rows=preview_rows,
                model=input.config.get("model"),
            )
            used_llm = response is not None

        if response is None:
            response = self._template_response(
                sql=sql,
                execution=execution,
                report=report,
                language=language,
                preview_rows=preview_rows,
            )

        return NodeOutput(
            result={"response": response, "used_llm": used_llm},
            metadata={
                "status": "success",
                "used_llm": used_llm,
                "language": language,
                "has_sql": sql is not None,
                "has_result": execution is not None,
            },
            context={"response": response},
        )

    # ------------------------------------------------------------------
    # Language resolution
    # ------------------------------------------------------------------

    @staticmethod
    def _resolve_language(input: NodeInput) -> str:
        """Resolve response language: config > context > default ``zh``."""
        language = (
            input.config.get("language")
            or input.context.get("language")
            or input.context.get("sqr_language")
            or "zh"
        )
        return "en" if str(language).lower().startswith("en") else "zh"

    # ------------------------------------------------------------------
    # LLM mode
    # ------------------------------------------------------------------

    async def _llm_response(
        self,
        *,
        question: str,
        sql: str | None,
        execution: dict[str, Any] | None,
        report: Any,
        language: str,
        preview_rows: int,
        model: str | None,
    ) -> str | None:
        """Generate the reply via LLM. Returns ``None`` on any failure."""
        prompt = self._build_prompt(
            question=question,
            sql=sql,
            execution=execution,
            report=report,
            language=language,
            preview_rows=preview_rows,
        )
        system = (
            "你是一个数据查询助手。请根据提供的查询上下文，用简洁的中文回复用户。"
            if language == "zh"
            else "You are a data query assistant. Reply to the user concisely in English "
            "based on the provided query context."
        )
        try:
            result = await self._router.complete(  # type: ignore[union-attr]
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": prompt},
                ],
                model=model,
            )
            content = result["choices"][0]["message"]["content"]
            text = (content or "").strip()
            return text or None
        except Exception:
            return None

    @staticmethod
    def _build_prompt(
        *,
        question: str,
        sql: str | None,
        execution: dict[str, Any] | None,
        report: Any,
        language: str,
        preview_rows: int,
    ) -> str:
        """Build the LLM prompt with question, SQL, and a result preview."""
        parts: list[str] = []
        parts.append(f"User question: {question or '(not provided)'}")
        parts.append(f"Generated SQL: {sql or '(none)'}")

        if execution:
            row_count = execution.get("row_count", len(execution.get("rows", [])))
            parts.append(f"Result row count: {row_count}")
            columns = execution.get("columns") or []
            if columns:
                parts.append(f"Columns: {', '.join(str(c) for c in columns)}")
            rows = execution.get("rows") or []
            if rows:
                preview = "\n".join(str(r) for r in rows[:preview_rows])
                parts.append(f"First rows:\n{preview}")
            if execution.get("truncated"):
                parts.append("Note: the result was truncated.")
        else:
            parts.append("Result: (not executed)")

        passed = _report_passed(report)
        if passed is not None:
            status = "passed" if passed else "FAILED"
            parts.append(f"Validation: {status}")
            errors = _report_errors(report)
            if errors:
                parts.append("Validation errors: " + "; ".join(errors[:5]))

        target = "Chinese" if language == "zh" else "English"
        parts.append(
            f"Write a short, friendly reply in {target} summarizing the outcome for the user."
        )
        return "\n".join(parts)

    # ------------------------------------------------------------------
    # Template mode (deterministic, no LLM)
    # ------------------------------------------------------------------

    def _template_response(
        self,
        *,
        sql: str | None,
        execution: dict[str, Any] | None,
        report: Any,
        language: str,
        preview_rows: int,
    ) -> str:
        zh = language == "zh"
        lines: list[str] = []

        if not sql and not execution:
            return (
                "未生成 SQL，无法执行查询。请补充更多信息后重试。"
                if zh
                else "No SQL was generated, so the query could not be executed. "
                "Please refine your question and try again."
            )

        if execution:
            row_count = execution.get("row_count", len(execution.get("rows", [])))
            head = (
                f"查询已完成，返回 {row_count} 行。"
                if zh
                else f"Query completed: {row_count} row(s) returned."
            )
            if execution.get("truncated"):
                head += "（结果已截断）" if zh else " (result truncated)"
            lines.append(head)
            preview = self._format_preview(execution, preview_rows, language)
            if preview:
                lines.append(preview)
        else:
            lines.append(
                "已生成 SQL，但尚未执行。" if zh else "SQL was generated but not executed."
            )

        validation = self._validation_line(report, language)
        if validation:
            lines.append(validation)

        if sql:
            lines.append(f"SQL: {sql}")

        return "\n".join(lines)

    @staticmethod
    def _format_preview(
        execution: dict[str, Any], preview_rows: int, language: str
    ) -> str | None:
        """Render the first ``preview_rows`` rows as a compact text block."""
        rows = execution.get("rows") or []
        if not rows:
            return None
        zh = language == "zh"
        shown = rows[:preview_rows]
        header = f"结果预览（前 {len(shown)} 行）:" if zh else f"Preview (first {len(shown)} rows):"
        lines = [header]
        columns = execution.get("columns") or []
        if columns:
            lines.append(" | ".join(str(c) for c in columns))
        for row in shown:
            cells = row if isinstance(row, (list, tuple)) else [row]
            lines.append(" | ".join(str(c) for c in cells))
        return "\n".join(lines)

    @staticmethod
    def _validation_line(report: Any, language: str) -> str | None:
        """One-line validation status, or ``None`` if no report available."""
        passed = _report_passed(report)
        if passed is None:
            return None
        zh = language == "zh"
        if passed:
            return "SQL 验证通过。" if zh else "SQL validation passed."
        errors = _report_errors(report)
        line = "SQL 验证未通过。" if zh else "SQL validation failed."
        if errors:
            joined = "; ".join(errors[:3])
            line += f"（{joined}）" if zh else f" ({joined})"
        return line


# ── Module-level helpers ────────────────────────────────────────────────


def _report_passed(report: Any) -> bool | None:
    """Extract the pass/fail flag from a ValidationReport or dict."""
    if report is None:
        return None
    if isinstance(report, dict):
        value = report.get("passed")
        return bool(value) if value is not None else None
    passed = getattr(report, "passed", None)
    return bool(passed) if passed is not None else None


def _report_errors(report: Any) -> list[str]:
    """Collect error messages from a ValidationReport or dict."""
    if report is None:
        return []
    errors: list[str] = []
    for attr in ("syntax_errors", "schema_errors", "type_errors"):
        values = report.get(attr, []) if isinstance(report, dict) else getattr(report, attr, [])
        errors.extend(str(v) for v in (values or []))
    return errors
