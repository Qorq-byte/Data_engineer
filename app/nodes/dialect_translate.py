"""DialectTranslateNode — translate SQL between database dialects via sqlglot.

See SPEC §4.5.3 and implementation-plan §4.3.4 for DialectAdapter design.

This is a **PlainNode** (non-LLM). It wraps ``DialectAdapter.translate()``
as a workflow node so dialect translation can be inserted anywhere in a
workflow plan.

Key features:
  - Source dialect: explicit via config, or ``"auto"`` for auto-detection.
  - Target dialect: **required** (no-op if same as source or empty).
  - Graceful degradation: returns original SQL on translation failure.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.db.dialect_adapter import DialectAdapter
from app.nodes.base import BaseNode, NodeInput, NodeOutput


@dataclass
class DialectTranslateOutput:
    """Structured output from the dialect_translate node.

    Attributes:
        original_sql: The SQL before translation.
        translated_sql: The SQL after translation (may equal original).
        source_dialect: Detected or explicit source dialect name.
        target_dialect: Target dialect name.
        translated: Whether any actual translation was performed.
    """

    original_sql: str = ""
    translated_sql: str = ""
    source_dialect: str = ""
    target_dialect: str = ""
    translated: bool = False


class DialectTranslateNode(BaseNode):
    """Translate SQL from one database dialect to another using sqlglot.

    This is a **PlainNode** — no LLM involvement, no AgenticNode inheritance.

    Expected ``input.context`` keys:
        - ``sql``: The SQL string to translate (falls back to ``query_text``).

    Expected ``input.config`` keys:
        - ``source_dialect``: Source dialect name (e.g. ``"postgresql"``).
          Use ``"auto"`` to auto-detect via sqlglot. Default: ``"auto"``.
        - ``target_dialect``: Target dialect name (e.g. ``"mysql"``).
          **Required** — if empty, no translation occurs.

    Output ``context`` keys:
        - ``sql`` — the translated SQL (overwrites input SQL).
        - ``original_sql`` — preserved original SQL.
        - ``source_dialect`` — detected or explicit source dialect.
        - ``target_dialect`` — target dialect.
        - ``translated`` — bool, whether translation was performed.
    """

    name = "dialect_translate"
    description = "Translate SQL between database dialects using sqlglot"

    # ── Main execute ─────────────────────────────────────────────────

    async def execute(self, input: NodeInput) -> NodeOutput:
        sql = (input.context.get("sql") or input.query_text or "").strip()
        if not sql:
            return NodeOutput(
                result=None,
                errors=["Empty SQL — nothing to translate"],
                metadata={"status": "empty_sql"},
            )

        source = input.config.get("source_dialect", "auto")
        target = input.config.get("target_dialect", "")

        if not target:
            return NodeOutput(
                result=DialectTranslateOutput(
                    original_sql=sql,
                    translated_sql=sql,
                    source_dialect=source,
                    target_dialect="",
                    translated=False,
                ),
                metadata={
                    "status": "skipped",
                    "reason": "no target dialect specified",
                },
                context={
                    "sql": sql,
                    "original_sql": sql,
                    "source_dialect": source,
                    "target_dialect": "",
                    "translated": False,
                },
            )

        # ── Resolve source dialect ───────────────────────────────────
        resolved_source = self._resolve_source(sql, source)

        # Normalize: no-op if source == target
        if _normalize_dialect(resolved_source) == _normalize_dialect(target):
            return NodeOutput(
                result=DialectTranslateOutput(
                    original_sql=sql,
                    translated_sql=sql,
                    source_dialect=resolved_source,
                    target_dialect=target,
                    translated=False,
                ),
                metadata={
                    "status": "skipped",
                    "reason": f"source and target are the same ({resolved_source})",
                },
                context={
                    "sql": sql,
                    "original_sql": sql,
                    "source_dialect": resolved_source,
                    "target_dialect": target,
                    "translated": False,
                },
            )

        # ── Translate ────────────────────────────────────────────────
        try:
            adapter = DialectAdapter.get_adapter(resolved_source)
            target_dialect_key = _to_sqlglot_dialect(target)
            translated = adapter.translate(sql, target_dialect=target_dialect_key)
        except ValueError as exc:
            # Unknown dialect
            return NodeOutput(
                result=DialectTranslateOutput(
                    original_sql=sql,
                    translated_sql=sql,
                    source_dialect=resolved_source,
                    target_dialect=target,
                    translated=False,
                ),
                errors=[str(exc)],
                metadata={"status": "invalid_dialect", "error": str(exc)},
            )
        except Exception as exc:
            # Translation failure — return original SQL (graceful degradation)
            return NodeOutput(
                result=DialectTranslateOutput(
                    original_sql=sql,
                    translated_sql=sql,
                    source_dialect=resolved_source,
                    target_dialect=target,
                    translated=False,
                ),
                errors=[f"Dialect translation failed: {exc}"],
                metadata={
                    "status": "translation_error",
                    "error_type": type(exc).__name__,
                },
                context={
                    "sql": sql,
                    "original_sql": sql,
                    "source_dialect": resolved_source,
                    "target_dialect": target,
                    "translated": False,
                },
            )

        actually_translated = translated != sql

        output = DialectTranslateOutput(
            original_sql=sql,
            translated_sql=translated,
            source_dialect=resolved_source,
            target_dialect=target,
            translated=actually_translated,
        )

        return NodeOutput(
            result=output,
            metadata={
                "status": "success",
                "translated": actually_translated,
                "source_dialect": resolved_source,
                "target_dialect": target,
            },
            context={
                "sql": translated,
                "original_sql": sql,
                "source_dialect": resolved_source,
                "target_dialect": target,
                "translated": actually_translated,
            },
        )

    # ── Helpers ───────────────────────────────────────────────────────

    @staticmethod
    def _resolve_source(sql: str, source: str) -> str:
        """Resolve the source dialect.

        When ``source`` is ``"auto"``, attempts to auto-detect via sqlglot.
        Falls back to ``"postgresql"`` if detection fails.
        """
        if source and source != "auto":
            return source
        return _auto_detect_dialect(sql)


# ── Module-level helpers ─────────────────────────────────────────────────


def _auto_detect_dialect(sql: str) -> str:
    """Attempt to auto-detect the SQL dialect using heuristic rules.

    Heuristic checks run first (fast, no parsing needed). Falls back to
    sqlglot auto-detection for ambiguous cases.

    Returns a dialect name from ``PRESET_DIALECTS``, or ``"postgresql"``
    as a safe default.
    """
    upper = sql.upper().strip()

    # ── Fast heuristic checks (no parser needed) ─────────────────

    # Backtick identifiers → MySQL / BigQuery style
    if "`" in sql:
        return "mysql"

    # PostgreSQL-style type casts (value::type)
    if "::" in sql:
        return "postgresql"

    # SQLite-specific function patterns
    if any(
        kw in upper
        for kw in [
            "STRFTIME",
            "DATETIME(",
            "GROUP_CONCAT",
            "SQLITE_",
            "RANDOMBLOB",
        ]
    ):
        return "sqlite"

    # PostgreSQL-specific function / syntax patterns
    if any(
        kw in upper
        for kw in [
            "ILIKE",
            "DATE_TRUNC",
            "JSONB",
            "STRING_AGG",
            "ARRAY_AGG",
        ]
    ):
        return "postgresql"

    # SQL Server style
    if "TOP" in upper:
        return "postgresql"  # Conservative default

    # ── sqlglot-based auto-detection (for ambiguous cases) ──────

    try:
        import sqlglot

        # Try parsing with auto-detection to see if sqlglot identifies a dialect
        expressions = sqlglot.parse(sql, read=None, error_level=None)
        if expressions:
            # Check if the parsed expression carries dialect info
            expr = expressions[0]
            dialect_name = getattr(expr, "dialect", None)
            if dialect_name:
                return _normalize_dialect(str(dialect_name))
    except (ImportError, Exception):
        pass  # Fall through to default

    return "postgresql"  # Safe default (ANSI-like)


def _normalize_dialect(name: str) -> str:
    """Normalize dialect name for comparison.

    Maps sqlglot dialect names to our PRESET_DIALECTS keys.
    """
    # sqlglot uses "postgres" not "postgresql"
    mapping = {
        "postgres": "postgresql",
        "postgresql": "postgresql",
        "mysql": "mysql",
        "sqlite": "sqlite",
        "duckdb": "duckdb",
        "snowflake": "snowflake",
        "starrocks": "starrocks",
        "bigquery": "bigquery",
        "redshift": "redshift",
        "clickhouse": "clickhouse",
        "databricks": "databricks",
        "trino": "trino",
    }
    return mapping.get(name.lower(), name.lower())


def _to_sqlglot_dialect(name: str) -> str:
    """Convert our dialect key to sqlglot dialect name."""
    mapping = {
        "postgresql": "postgres",
        "mysql": "mysql",
        "sqlite": "sqlite",
        "duckdb": "duckdb",
        "snowflake": "snowflake",
        "starrocks": "starrocks",
        "bigquery": "bigquery",
        "redshift": "redshift",
        "clickhouse": "clickhouse",
        "databricks": "databricks",
        "trino": "trino",
    }
    return mapping.get(name.lower(), name.lower())
