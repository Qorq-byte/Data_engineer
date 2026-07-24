"""FormatSQLNode — SQL formatting via sqlparse.

See SPEC §4.12 for PlainNode design.

This is a **PlainNode** (non-LLM). It wraps ``sqlparse.format()`` as a
workflow node so SQL formatting can be inserted anywhere in a workflow plan.

Key features:
  - Keyword case: ``"upper"`` (default), ``"lower"``, or ``"capitalize"``
  - Indent width: configurable (default 2)
  - Reindent: on by default
  - Strip comments: optional
"""

from __future__ import annotations

from dataclasses import dataclass

import sqlparse

from app.nodes.base import BaseNode, NodeInput, NodeOutput


@dataclass
class FormatSQLOutput:
    """Structured output from the format_sql node.

    Attributes:
        original_sql: The SQL before formatting.
        formatted_sql: The SQL after formatting.
        keyword_case: Keyword case used (upper / lower / capitalize).
        indent_width: Indentation width in spaces.
        changed: Whether formatting actually modified the SQL.
    """

    original_sql: str = ""
    formatted_sql: str = ""
    keyword_case: str = "upper"
    indent_width: int = 2
    changed: bool = False


class FormatSQLNode(BaseNode):
    """Format SQL using sqlparse.

    This is a **PlainNode** — no LLM involvement, no AgenticNode inheritance.

    Expected ``input.context`` keys:
        - ``sql``: The SQL string to format (falls back to ``query_text``).

    Expected ``input.config`` keys:
        - ``keyword_case``: ``"upper"`` (default), ``"lower"``, or ``"capitalize"``
        - ``indent_width``: Positive integer (default 2)
        - ``reindent``: ``True`` (default) to restructure indentation
        - ``strip_comments``: ``True`` to remove comments (default False)

    Output ``context`` keys:
        - ``sql`` — the formatted SQL (overwrites input SQL).
        - ``original_sql`` — preserved original SQL.
        - ``formatted`` — bool, whether formatting changed the SQL.
    """

    name = "format_sql"
    description = "Format SQL with configurable keyword case, indentation, and comment stripping"

    # ── Main execute ─────────────────────────────────────────────────

    async def execute(self, input: NodeInput) -> NodeOutput:
        sql = (input.context.get("sql") or input.query_text or "").strip()
        if not sql:
            return NodeOutput(
                result=None,
                errors=["Empty SQL — nothing to format"],
                metadata={"status": "empty_sql"},
            )

        keyword_case = input.config.get("keyword_case", "upper")
        indent_width = int(input.config.get("indent_width", 2))
        reindent = bool(input.config.get("reindent", True))
        strip_comments = bool(input.config.get("strip_comments", False))

        # ── Validate keyword_case ─────────────────────────────────
        valid_cases = {"upper", "lower", "capitalize"}
        if keyword_case not in valid_cases:
            return NodeOutput(
                result=FormatSQLOutput(
                    original_sql=sql,
                    formatted_sql=sql,
                    keyword_case=keyword_case,
                    indent_width=indent_width,
                    changed=False,
                ),
                errors=[
                    f"Invalid keyword_case '{keyword_case}' — "
                    f"must be one of: {', '.join(sorted(valid_cases))}"
                ],
                metadata={
                    "status": "invalid_config",
                    "keyword_case": keyword_case,
                },
            )

        # ── Format ────────────────────────────────────────────────
        try:
            formatted = sqlparse.format(
                sql,
                reindent=reindent,
                keyword_case=keyword_case,
                indent_width=indent_width,
                strip_comments=strip_comments,
            )
        except Exception as exc:
            return NodeOutput(
                result=FormatSQLOutput(
                    original_sql=sql,
                    formatted_sql=sql,
                    keyword_case=keyword_case,
                    indent_width=indent_width,
                    changed=False,
                ),
                errors=[f"SQL formatting failed: {exc}"],
                metadata={"status": "format_error", "error_type": type(exc).__name__},
                context={
                    "sql": sql,
                    "original_sql": sql,
                    "formatted": False,
                },
            )

        changed = formatted.strip() != sql

        output = FormatSQLOutput(
            original_sql=sql,
            formatted_sql=formatted,
            keyword_case=keyword_case,
            indent_width=indent_width,
            changed=changed,
        )

        return NodeOutput(
            result=output,
            metadata={
                "status": "success",
                "changed": changed,
                "keyword_case": keyword_case,
            },
            context={
                "sql": formatted,
                "original_sql": sql,
                "formatted": changed,
            },
        )
