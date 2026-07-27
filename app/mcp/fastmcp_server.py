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
    await server.run(transport="sse")
"""

from __future__ import annotations

from typing import Any


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
    from mcp.server.fastmcp import FastMCP

    mcp = FastMCP(
        name=name,
        instructions=(
            "NL2SQL Data Engineering Agent — converts natural language to SQL "
            "with domain-aware schema linking, validation, and execution. "
            "Supports 11 database dialects."
        ),
        host=host,
        port=port,
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
            from app.core.nlp_parser import NLParser
            from app.core.prompt_builder import PromptBuilder
            from app.db.connection import ConnectionFactory
            from app.harness.config_loader import ConfigLoader
            from app.llm.router import LiteLLMRouter

            # 1. Parse NL → SQR
            parser = NLParser()
            sqr = parser.parse(nl_text)

            # 2. Build prompt with schema context
            config_loader = ConfigLoader("app/config/agent.yml")
            config_loader.load()  # validate agent config is loadable
            prompt_builder = PromptBuilder()
            schema_context = ""
            if db_id:
                try:
                    schema_extractor = ConnectionFactory.get_schema_extractor(
                        db_id, dialect="postgresql"
                    )
                    schema = schema_extractor.extract()
                    schema_context = schema.format_for_llm()
                except Exception:
                    schema_context = "(schema not available)"

            prompt = prompt_builder.build(
                sqr=sqr,
                schema_context=schema_context,
                domain=domain or "general",
                n_total_tokens=8000,
            )

            # 3. Generate SQL via LLM
            router = LiteLLMRouter()
            gen_result = await router.complete(prompt)
            sql = _extract_sql_from_response(gen_result)

            # 4. Execute
            factory = ConnectionFactory()
            conn = factory.create(db_id, dialect="postgresql")
            rows = await conn.fetch_all(sql)
            columns = list(rows[0].keys()) if rows else []

            return {
                "sql": sql,
                "columns": columns,
                "rows": [dict(r) for r in rows[:100]],
                "row_count": len(rows),
            }

        except Exception as exc:
            return {
                "error": str(exc),
                "error_type": type(exc).__name__,
                "sql": "",
                "columns": [],
                "rows": [],
                "row_count": 0,
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
            from app.db.connection import ConnectionFactory
            from app.nodes.execute_sql import ExecuteSQLNode

            factory = ConnectionFactory()
            conn = factory.create(db_id, dialect="postgresql")

            explain_rows = ExecuteSQLNode._execute_explain(conn, sql)
            plan_analysis = ExecuteSQLNode._parse_explain_output(explain_rows)
            raw_text = "\n".join(str(r) for r in explain_rows)

            return {
                "raw_plan": raw_text,
                "analysis": {
                    "scan_types": list(plan_analysis.scan_types),
                    "estimated_rows": plan_analysis.estimated_rows,
                    "join_types": list(plan_analysis.join_types),
                    "has_full_scan": plan_analysis.has_full_scan,
                    "warnings": list(plan_analysis.warnings),
                },
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
        if not sql.strip():
            return {"valid": False, "errors": ["Empty SQL"]}

        errors: list[str] = []

        # Step 1: Syntax validation via sqlglot
        try:
            import sqlglot

            parsed = sqlglot.parse(sql, error_level=None)
            if not parsed:
                errors.append("Failed to parse SQL — no valid statements found")
        except Exception as exc:
            errors.append(f"Syntax error: {exc}")

        # Step 2: Write-statement check
        try:
            from app.nodes.execute_sql import ExecuteSQLNode

            if ExecuteSQLNode._is_write_statement(sql):
                errors.append("Write statements are not allowed")
        except Exception:
            pass

        # Step 3: Schema reference check (if db_id provided)
        if db_id and not errors:
            try:
                from app.db.connection import ConnectionFactory
                from app.db.schema_extractor import SchemaExtractor

                factory = ConnectionFactory()
                conn = factory.create(db_id, dialect="postgresql")
                extractor = SchemaExtractor(conn)
                table_names = extractor.list_table_names()

                # Basic table reference check
                sql_lower = sql.lower()
                referenced_tables = _extract_table_names(sql_lower)
                for table in referenced_tables:
                    if table not in [t.lower() for t in table_names]:
                        errors.append(f"Unknown table: {table}")
            except Exception:
                pass

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
            from app.db.connection import ConnectionFactory
            from app.db.schema_extractor import SchemaExtractor

            factory = ConnectionFactory()
            conn = factory.create(db_id, dialect="postgresql")
            extractor = SchemaExtractor(conn)
            table_names = extractor.list_table_names()

            matching_tables: list[dict[str, Any]] = []
            matching_columns: list[dict[str, Any]] = []

            for table in table_names:
                columns = extractor.list_columns(table)
                col_names = [c.lower() for c in columns]
                table_col_matches: list[str] = []

                for kw in keywords:
                    kw_lower = kw.lower()
                    if kw_lower in table.lower() and table not in [
                        t["table"] for t in matching_tables
                    ]:
                        matching_tables.append({
                            "table": table,
                            "match_type": "name",
                        })
                    for col in col_names:
                        if kw_lower in col:
                            table_col_matches.append(columns[col_names.index(col)])

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
    def get_schema(db_id: str) -> str:
        import json

        try:
            from app.db.connection import ConnectionFactory

            factory = ConnectionFactory()
            conn = factory.create(db_id, dialect="postgresql")
            from app.db.schema_extractor import SchemaExtractor

            extractor = SchemaExtractor(conn)
            tables: dict[str, list[dict[str, str]]] = {}
            for table in extractor.list_table_names():
                columns = extractor.list_columns(table)
                col_defs = []
                for col_name in columns:
                    col_type = extractor.get_column_type(table, col_name)
                    col_defs.append({"name": col_name, "type": col_type or "unknown"})
                tables[table] = col_defs

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
        from pathlib import Path

        try:
            domains_dir = Path("app/config/domains")
            domains: list[dict[str, Any]] = []

            if domains_dir.exists():
                for yaml_file in sorted(domains_dir.glob("*.yml")):
                    name = yaml_file.stem
                    domains.append({
                        "name": name,
                        "path": str(yaml_file.relative_to(".")),
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

            loader = PlanLoader("app/workflow/plans")
            plans = []
            for plan_id in loader.list_available():
                plan = loader.load(plan_id)
                plans.append({
                    "id": plan.id,
                    "name": plan.name,
                    "description": plan.description,
                    "node_order": plan.node_order,
                    "node_count": len(plan.node_order),
                })

            return json.dumps({"workflows": plans}, indent=2)

        except Exception as exc:
            return json.dumps({"error": str(exc), "workflows": []})


# ═══════════════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════════════


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
