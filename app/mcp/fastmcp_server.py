"""NL2SQL MCP Server — exposes NL→SQL capabilities to external AI clients.

See SPEC §3.3.7 (MCP Server external exposure) for the full specification.

This server wraps the NL2SQL pipeline as MCP tools, allowing any MCP-compatible
host (Claude Desktop, etc.) to use natural-language SQL generation.

Tools (5):
  - nl_query       → NL → SQL → execute
  - explain_sql    → EXPLAIN + interpret
  - translate_sql  → dialect translation
  - validate_sql   → syntax/schema validation
  - search_schema  → keyword schema search

Resources (4):
  - nlsql://schema/{db_id}   → database schema
  - nlsql://domains/         → domain configurations
  - nlsql://nodes/           → registered nodes
  - nlsql://workflows/       → workflow plans

Usage::

    from app.mcp.fastmcp_server import create_nl2sql_server

    server = create_nl2sql_server(port=8090)
    await server.run_async(transport="sse")
"""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Any

_APP_ROOT = Path(__file__).resolve().parents[1]


def create_nl2sql_server(
    name: str = "NL2SQL Agent",
    host: str = "127.0.0.1",
    port: int = 8090,
) -> Any:
    """Create and configure the NL2SQL FastMCP server.

    The server is created but not started. Call ``server.run(transport="sse")``
    or ``server.run(transport="stdio")`` to start.

    Args:
        name: Server name (shown in MCP host UI).
        host: Bind address for SSE transport.
        port: Port for SSE transport.

    Returns:
        Configured FastMCP instance.
    """
    from fastmcp import FastMCP, settings

    class ConfiguredFastMCP(FastMCP):
        """Keep factory bind defaults through FastMCP's public transport API."""

        async def run_async(
            self,
            transport: str | None = None,
            show_banner: bool | None = None,
            **transport_kwargs: Any,
        ) -> None:
            if (transport or settings.transport) != "stdio":
                transport_kwargs.setdefault("host", host)
                transport_kwargs.setdefault("port", port)
            await super().run_async(transport, show_banner, **transport_kwargs)

    mcp = ConfiguredFastMCP(
        name=name,
        instructions=(
            "NL2SQL Data Engineering Agent — converts natural language to SQL "
            "with domain-aware schema linking, validation, and execution. "
            "Supports 11 database dialects."
        ),
    )

    # ── Register tools ─────────────────────────────────────────────────

    _register_nl_query(mcp)
    _register_explain_sql(mcp)
    _register_translate_sql(mcp)
    _register_validate_sql(mcp)
    _register_search_schema(mcp)

    # ── Register resources ─────────────────────────────────────────────

    _register_schema_resource(mcp)
    _register_domains_resource(mcp)
    _register_nodes_resource(mcp)
    _register_workflows_resource(mcp)

    return mcp


# ═══════════════════════════════════════════════════════════════════════════════
# Tool registration helpers
# ═══════════════════════════════════════════════════════════════════════════════


def _register_nl_query(mcp: Any) -> None:
    """Register the nl_query tool — NL → SQL → execute."""

    @mcp.tool(
        name="nl_query",
        description=(
            "Convert natural language to SQL and execute it against the database. "
            "Returns the generated SQL, result columns, and up to 100 rows."
        ),
    )
    async def nl_query(
        nl_text: str,
        domain: str | None = None,
        db_id: str | None = None,
    ) -> dict[str, Any]:
        try:
            from app.api.query import QueryRequest, submit_query

            # Share the production pipeline, validation, execution limits and trace.
            response = await submit_query(
                QueryRequest(
                    nl_text=nl_text,
                    domain=domain or "default",
                    database=db_id or "",
                    execute=True,
                    num_candidates=1,
                )
            )
            execution = response.execution or {}
            errors = list(response.errors)
            if not execution and not errors:
                errors.append("Query did not produce an execution result")

            return {
                "status": "failed" if errors else response.status,
                "query_id": response.query_id,
                "session_id": response.session_id,
                "plan_id": response.plan_id,
                "workflow_ids": response.workflow_ids,
                "llm_calls": response.llm_calls,
                "llm_usage": response.llm_usage,
                "llm_audit": response.llm_audit,
                "sql": response.primary_sql or "",
                "candidates": [
                    candidate.model_dump(exclude={"execution"}) for candidate in response.candidates
                ],
                "columns": execution.get("columns", []),
                "rows": execution.get("rows", [])[:100],
                "row_count": min(execution.get("row_count", 0), 100),
                "truncated": execution.get("truncated", False)
                or execution.get("row_count", 0) > 100,
                "errors": errors,
                "warnings": response.warnings,
                **({"error": "; ".join(errors), "error_code": "WORKFLOW_FAILED"} if errors else {}),
            }

        except Exception as exc:
            from fastapi import HTTPException
            from pydantic import ValidationError

            from app.workflow.contracts import (
                QueryCapacityError,
                QueryFailure,
                classify_query_failure,
            )

            failure = classify_query_failure(exc)
            if isinstance(exc, ValidationError):
                failure = QueryFailure("INVALID_REQUEST", "Invalid query request", 422)
            elif isinstance(exc, HTTPException):
                detail: dict[str, Any] = exc.detail if isinstance(exc.detail, dict) else {}
                if detail.get("code") == "QUERY_TIMEOUT":
                    failure = classify_query_failure(TimeoutError())
                elif detail.get("code") == "QUERY_CAPACITY":
                    failure = classify_query_failure(QueryCapacityError())
                elif 400 <= exc.status_code < 500:
                    failure = QueryFailure(
                        "INVALID_REQUEST", "Invalid query request", exc.status_code
                    )
            return {
                "status": "failed",
                "error": failure.message,
                "error_code": failure.code,
                "http_status": failure.http_status,
                "error_type": type(exc).__name__,
                "errors": [failure.message],
                "warnings": [],
                "sql": "",
                "columns": [],
                "rows": [],
                "row_count": 0,
                "truncated": False,
                **({"retry_after_seconds": failure.retry_after_seconds}
                   if failure.retry_after_seconds is not None else {}),
            }


def _register_explain_sql(mcp: Any) -> None:
    """Register the explain_sql tool — EXPLAIN + interpretation."""

    @mcp.tool(
        name="explain_sql",
        description=(
            "Explain a SQL query's execution plan. "
            "Returns the raw EXPLAIN output and a human-readable interpretation."
        ),
    )
    async def explain_sql(
        sql: str,
        db_id: str | None = None,
    ) -> dict[str, Any]:
        if not sql.strip():
            return {"error": "Empty SQL provided", "raw_plan": "", "analysis": None}

        try:
            from app.api.query import ExecuteRequest, execute_sql_direct
            from app.nodes.execute_sql import ExecuteSQLNode
            from app.security.sql_policy import require_read_query

            _, info = _get_connection(db_id)
            dialect = "postgres" if info.db_type.value == "postgresql" else info.db_type.value
            require_read_query(sql, dialect=dialect)
            prefix = "EXPLAIN QUERY PLAN" if dialect == "sqlite" else "EXPLAIN"
            explain_result = await execute_sql_direct(
                ExecuteRequest(sql=f"{prefix} {sql}", database=info.db_id)
            )
            if explain_result.get("status") != "success":
                raise ValueError("; ".join(explain_result.get("errors", ["EXPLAIN failed"])))
            plan_analysis = ExecuteSQLNode._parse_explain_output(explain_result)
            raw_text = "\n".join(str(r) for r in explain_result.get("rows", []))

            return {
                "raw_plan": raw_text,
                "analysis": asdict(plan_analysis),
            }

        except Exception as exc:
            return {
                "error": str(exc),
                "error_type": type(exc).__name__,
                "raw_plan": "",
                "analysis": None,
            }


def _register_translate_sql(mcp: Any) -> None:
    """Register the translate_sql tool — dialect translation."""

    @mcp.tool(
        name="translate_sql",
        description=(
            "Translate SQL between database dialects using sqlglot. "
            "Supports 11 dialects: postgresql, mysql, sqlite, duckdb, "
            "snowflake, bigquery, redshift, clickhouse, databricks, trino, starrocks."
        ),
    )
    async def translate_sql(
        sql: str,
        source_dialect: str = "auto",
        target_dialect: str = "",
    ) -> dict[str, Any]:
        if not sql.strip():
            return {"error": "Empty SQL provided", "translated_sql": sql}

        if not target_dialect:
            return {
                "error": "target_dialect is required",
                "translated_sql": sql,
            }

        try:
            from app.db.dialect_adapter import DialectAdapter
            from app.nodes.dialect_translate import _auto_detect_dialect, _to_sqlglot_dialect

            resolved_source = source_dialect
            if source_dialect == "auto":
                resolved_source = _auto_detect_dialect(sql)

            adapter = DialectAdapter.get_adapter(resolved_source)
            target_key = _to_sqlglot_dialect(target_dialect)
            translated = adapter.translate(sql, target_dialect=target_key)

            return {
                "translated_sql": translated,
                "source_dialect": resolved_source,
                "target_dialect": target_dialect,
                "translated": translated != sql,
            }

        except Exception as exc:
            return {
                "error": str(exc),
                "error_type": type(exc).__name__,
                "translated_sql": sql,
                "translated": False,
            }


def _register_validate_sql(mcp: Any) -> None:
    """Register the validate_sql tool — syntax + schema validation."""

    @mcp.tool(
        name="validate_sql",
        description=(
            "Validate a SQL query for syntax errors and schema reference issues. "
            "Returns validation errors (if any) and a validity flag."
        ),
    )
    async def validate_sql(
        sql: str,
        db_id: str | None = None,
    ) -> dict[str, Any]:
        errors: list[str] = []
        try:
            from app.core.sql_validator import SQLValidator
            from app.security.sql_policy import require_read_query

            schema = await _get_schema(db_id) if db_id else None
            dialect = schema.database_type if schema else ""
            dialect = "postgres" if dialect == "postgresql" else dialect
            require_read_query(sql, dialect=dialect)
            report = await SQLValidator(dialect=dialect).validate(sql, schema=schema)
            errors.extend(report.syntax_errors)
            errors.extend(report.schema_errors)
        except Exception as exc:
            # Missing databases and policy failures must not report validity.
            errors.append(str(exc))

        return {
            "valid": len(errors) == 0,
            "errors": errors,
            "error_count": len(errors),
        }


def _register_search_schema(mcp: Any) -> None:
    """Register the search_schema tool — keyword schema search."""

    @mcp.tool(
        name="search_schema",
        description=(
            "Search database schema by keywords. "
            "Returns matching tables and columns."
        ),
    )
    async def search_schema(
        keywords: list[str],
        db_id: str | None = None,
    ) -> dict[str, Any]:
        try:
            snapshot = await _get_schema(db_id)
            normalized_keywords = {kw.strip().casefold() for kw in keywords if kw.strip()}

            matching_tables: list[dict[str, Any]] = []
            matching_columns: list[dict[str, Any]] = []

            for table, table_schema in snapshot.tables.items():
                if any(kw in table.casefold() for kw in normalized_keywords):
                    matching_tables.append({"table": table, "match_type": "name"})
                table_col_matches = [
                    col.name
                    for col in table_schema.columns
                    if any(kw in col.name.casefold() for kw in normalized_keywords)
                ]

                if table_col_matches:
                    matching_columns.append({
                        "table": table,
                        "columns": table_col_matches,
                    })

            return {
                "tables": matching_tables,
                "columns": matching_columns,
                "total_matches": len(matching_tables) + len(matching_columns),
            }

        except Exception as exc:
            return {
                "error": str(exc),
                "error_type": type(exc).__name__,
                "tables": [],
                "columns": [],
                "total_matches": 0,
            }


# ═══════════════════════════════════════════════════════════════════════════════
# Resource registration helpers
# ═══════════════════════════════════════════════════════════════════════════════


def _register_schema_resource(mcp: Any) -> None:
    """Register the schema resource — database schema snapshot."""

    @mcp.resource(
        "nlsql://schema/{db_id}",
        name="Database Schema",
        description="Full schema snapshot for the given database ID",
        mime_type="application/json",
    )
    async def get_schema(db_id: str) -> str:
        import json

        try:
            snapshot = await _get_schema(db_id)
            tables = {
                name: [{"name": col.name, "type": col.type} for col in table.columns]
                for name, table in snapshot.tables.items()
            }

            return json.dumps({"db_id": db_id, "tables": tables}, indent=2)

        except Exception as exc:
            return json.dumps({"error": str(exc), "db_id": db_id})


def _register_domains_resource(mcp: Any) -> None:
    """Register the domains resource — all domain configurations."""

    @mcp.resource(
        "nlsql://domains/",
        name="Domain Configurations",
        description="List of all domain configurations with terminology and rules",
        mime_type="application/json",
    )
    def get_domains() -> str:
        import json
        try:
            domains_dir = _APP_ROOT / "config" / "domains"
            domains: list[dict[str, Any]] = []

            if domains_dir.exists():
                for yaml_file in sorted(domains_dir.glob("*.yml")):
                    name = yaml_file.stem
                    domains.append({
                        "name": name,
                        "path": yaml_file.relative_to(_APP_ROOT.parent).as_posix(),
                    })

            return json.dumps({"domains": domains}, indent=2)

        except Exception as exc:
            return json.dumps({"error": str(exc), "domains": []})


def _register_nodes_resource(mcp: Any) -> None:
    """Register the nodes resource — list of registered nodes."""

    @mcp.resource(
        "nlsql://nodes/",
        name="Registered Nodes",
        description="List of all registered workflow nodes with descriptions",
        mime_type="application/json",
    )
    def get_nodes() -> str:
        import json

        try:
            from app.nodes.registry import node_registry

            nodes = []
            for name in node_registry.list_all():
                node = node_registry.get(name)
                nodes.append({
                    "name": name,
                    "description": node.description,
                    "type": type(node).__name__,
                })

            return json.dumps({"nodes": nodes}, indent=2)

        except Exception as exc:
            return json.dumps({"error": str(exc), "nodes": []})


def _register_workflows_resource(mcp: Any) -> None:
    """Register the workflows resource — available workflow plans."""

    @mcp.resource(
        "nlsql://workflows/",
        name="Workflow Plans",
        description="List of available workflow plans with node order",
        mime_type="application/json",
    )
    def get_workflows() -> str:
        import json

        try:
            from app.harness.plan_loader import PlanLoader

            loader = PlanLoader(str(_APP_ROOT / "workflow" / "plans"))
            plans = []
            for plan_id in loader.list_available():
                plan = loader.load(plan_id)
                plans.append({
                    "id": plan.id,
                    "name": plan.name,
                    "description": plan.description,
                    "node_order": [asdict(node_def) for node_def in plan.node_order],
                    "node_count": len(plan.node_order),
                })

            return json.dumps({"workflows": plans}, indent=2)

        except Exception as exc:
            return json.dumps({"error": str(exc), "workflows": []})


# ═══════════════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════════════


def _get_connection(db_id: str | None) -> tuple[Any, Any]:
    """Resolve an already registered database; MCP never creates implicit connections."""
    from app.db.connections import ConnectionFactory

    if not db_id:
        connections = ConnectionFactory.list_all()
        if not connections:
            raise ValueError("No database connected")
        db_id = connections[0].db_id
    return ConnectionFactory.get(db_id), ConnectionFactory.get_info(db_id)


async def _get_schema(db_id: str | None) -> Any:
    from app.db.schema_extractor import SchemaExtractor

    connection, info = _get_connection(db_id)
    return await SchemaExtractor.extract(connection, info.db_type, info.database_name)


def _extract_sql_from_response(response: Any) -> str:
    """Extract SQL string from LLM response (str / dict / object)."""
    if isinstance(response, str):
        text = response
    elif isinstance(response, dict):
        text = response.get("content", response.get("text", ""))
    else:
        text = getattr(response, "content", getattr(response, "text", ""))

    # Strip markdown fences
    import re

    match = re.search(r"```(?:sql)?\s*\n?(.*?)```", text, re.DOTALL)
    if match:
        return match.group(1).strip()
    return text.strip()


def _extract_table_names(sql_lower: str) -> list[str]:
    """Extract table names from lowercased SQL via basic heuristics."""
    import re

    tables: set[str] = set()

    # FROM clause
    from_matches = re.findall(r"\bfrom\s+(\w+)", sql_lower)
    tables.update(from_matches)

    # JOIN clause
    join_matches = re.findall(r"\bjoin\s+(\w+)", sql_lower)
    tables.update(join_matches)

    # INSERT INTO
    insert_matches = re.findall(r"\binto\s+(\w+)", sql_lower)
    tables.update(insert_matches)

    # UPDATE
    update_matches = re.findall(r"\bupdate\s+(\w+)", sql_lower)
    tables.update(update_matches)

    return list(tables)
