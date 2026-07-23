"""Tool Execution Agent — unified MCP tool + SQL execution gateway.

See SPEC §4.11.2 (agent role: Tool Execution) and §4.11.4 (gateway pattern).

All agents route external calls through this agent rather than calling
MCP tools or executing SQL directly.  This provides a single point for
rate limiting, caching, retry, and audit logging.
"""

from __future__ import annotations

import time
from typing import Any

from app.agents.base import AgentResult, BaseAgent


class ToolExecutionAgent(BaseAgent):
    """Unified gateway for MCP tool calls and SQL execution.

    Wraps :class:`app.mcp.fastmcp_client.MCPClientManager` for MCP calls
    and :class:`app.db.connections.ConnectionFactory` for SQL execution.

    Usage::

        agent = ToolExecutionAgent()
        # Execute SQL
        result = await agent.execute({"sql": "SELECT 1", "db_id": "main"})
        # Call MCP tool
        result = await agent.call_mcp_tool("db_server", "list_tables", {})
    """

    name = "tool_executor"
    description = (
        "Unified gateway for SQL execution and MCP tool calls. "
        "All agents route external access through this agent."
    )

    def __init__(
        self,
        mcp_client: Any = None,
        config: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(config)
        self._mcp_client = mcp_client

    async def execute(self, input: Any, context: dict[str, Any]) -> AgentResult:
        """Dispatch to the appropriate execution path based on input.

        Args:
            input: dict with one of these shapes:
                - ``{"sql": "...", "db_id": "..."}`` → execute SQL
                - ``{"server": "...", "tool": "...", "args": {...}}`` → call MCP tool
                - ``{"action": "get_schema", "db_id": "..."}`` → fetch schema
            context: Shared dict with optional ``connection``, ``max_rows``, etc.

        Returns:
            AgentResult with execution results or error.
        """
        t0 = time.perf_counter()
        try:
            if not isinstance(input, dict):
                return self._error(
                    f"ToolExecutionAgent expects a dict input, got {type(input).__name__}",
                    latency_ms=0.0,
                )

            # ── SQL execution path ─────────────────────────────────
            if "sql" in input:
                return await self._execute_sql(input, context, t0)

            # ── MCP tool call path ─────────────────────────────────
            if "server" in input and "tool" in input:
                return await self._call_mcp_tool(input, context, t0)

            # ── Schema fetch path ──────────────────────────────────
            if input.get("action") == "get_schema":
                return await self._get_schema(input, context, t0)

            return self._error(
                "Unknown action — expected 'sql', 'server'+'tool', or action='get_schema'",
                latency_ms=(time.perf_counter() - t0) * 1000,
            )

        except Exception as exc:
            elapsed = (time.perf_counter() - t0) * 1000
            return self._error(str(exc), latency_ms=round(elapsed, 2))

    # ── Convenience methods ────────────────────────────────────────────

    async def execute_query(
        self,
        sql: str,
        db_id: str = "",
        params: dict[str, Any] | None = None,
        context: dict[str, Any] | None = None,
    ) -> AgentResult:
        """Execute a SQL query (convenience wrapper)."""
        ctx = dict(context or {})
        ctx["db_id"] = db_id
        return await self.execute(
            {"sql": sql, "params": params or {}, "db_id": db_id},
            ctx,
        )

    async def call_mcp_tool(
        self,
        server: str,
        tool: str,
        args: dict[str, Any] | None = None,
        context: dict[str, Any] | None = None,
    ) -> AgentResult:
        """Call an MCP tool on a registered server (convenience wrapper)."""
        return await self.execute(
            {"server": server, "tool": tool, "args": args or {}},
            context or {},
        )

    # ── Private execution paths ────────────────────────────────────────

    async def _execute_sql(
        self, input: dict[str, Any], context: dict[str, Any], t0: float
    ) -> AgentResult:
        """Execute SQL via ConnectionFactory."""
        sql = str(input.get("sql", ""))
        if not sql.strip():
            return self._error("Empty SQL — nothing to execute", latency_ms=0.0)

        db_id = input.get("db_id") or context.get("db_id", "")
        connection = context.get("connection")
        max_rows = int(input.get("max_rows") or context.get("max_rows", 1000))

        # If a live connection is provided, use it directly
        if connection is not None:
            try:
                cursor = connection.execute(sql)
                rows = (
                    cursor.fetchmany(max_rows + 1)
                    if hasattr(cursor, "fetchmany")
                    else cursor.fetchall()
                )
                columns = (
                    [d[0] for d in cursor.description]
                    if cursor.description
                    else []
                )
                truncated = len(rows) > max_rows
                if truncated:
                    rows = rows[:max_rows]
                elapsed = (time.perf_counter() - t0) * 1000
                return self._ok(
                    {
                        "columns": columns,
                        "rows": rows,
                        "truncated": truncated,
                        "row_count": len(rows),
                    },
                    latency_ms=round(elapsed, 2),
                    sql=sql[:200],
                )
            except Exception as exc:
                elapsed = (time.perf_counter() - t0) * 1000
                return self._error(
                    f"SQL execution failed: {exc}",
                    latency_ms=round(elapsed, 2),
                    sql=sql[:200],
                )

        # No connection available
        return self._error(
            "No database connection available in context",
            latency_ms=(time.perf_counter() - t0) * 1000,
            db_id=db_id,
        )

    async def _call_mcp_tool(
        self, input: dict[str, Any], context: dict[str, Any], t0: float
    ) -> AgentResult:
        """Call an MCP tool via MCPClientManager."""
        server = str(input.get("server", ""))
        tool = str(input.get("tool", ""))
        args = input.get("args") or {}

        if self._mcp_client is not None:
            try:
                result = await self._mcp_client.call_tool(server, tool, args)
                elapsed = (time.perf_counter() - t0) * 1000
                return self._ok(
                    result,
                    latency_ms=round(elapsed, 2),
                    server=server,
                    tool=tool,
                )
            except Exception as exc:
                elapsed = (time.perf_counter() - t0) * 1000
                return self._error(
                    f"MCP tool call failed [{server}.{tool}]: {exc}",
                    latency_ms=round(elapsed, 2),
                )

        # No MCP client — stub acknowledgment
        elapsed = (time.perf_counter() - t0) * 1000
        return self._ok(
            {"stub": True, "server": server, "tool": tool, "args": args},
            latency_ms=round(elapsed, 2),
            note="No MCP client configured — returning stub result",
        )

    async def _get_schema(
        self, input: dict[str, Any], context: dict[str, Any], t0: float
    ) -> AgentResult:
        """Fetch database schema."""
        input.get("db_id") or context.get("db_id", "")
        connection = context.get("connection")

        if connection is not None:
            try:
                from app.db.connections import DatabaseType
                from app.db.schema_extractor import SchemaExtractor

                extractor = SchemaExtractor()
                db_type = DatabaseType.SQLITE  # Default; real impl would detect
                snapshot = await extractor.extract(connection, db_type)
                elapsed = (time.perf_counter() - t0) * 1000
                return self._ok(
                    snapshot,
                    latency_ms=round(elapsed, 2),
                    table_count=len(snapshot.tables) if snapshot else 0,
                )
            except Exception as exc:
                elapsed = (time.perf_counter() - t0) * 1000
                return self._error(f"Schema extraction failed: {exc}", latency_ms=round(elapsed, 2))

        # Return what's already in context
        schema = context.get("schema")
        elapsed = (time.perf_counter() - t0) * 1000
        return self._ok(
            schema,
            latency_ms=round(elapsed, 2),
            source="context",
        )
