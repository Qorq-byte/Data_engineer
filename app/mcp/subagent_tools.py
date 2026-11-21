"""Subagent queries over authenticated MCP HTTP, using the REST scope contract."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal

from fastapi import HTTPException

from app.api.subagents import SubagentQueryRequest, list_subagents, query_subagent
from app.auth.tenant import tenant_scope
from app.mcp.access import require_mcp_http_operator
from app.mcp.query_projection import query_error

if TYPE_CHECKING:
    from fastmcp import FastMCP


async def _operator() -> dict[str, Any]:
    from fastmcp.server.dependencies import get_http_request

    try:
        request = get_http_request()
    except RuntimeError:
        raise HTTPException(
            status_code=401, detail="Subagents require authenticated HTTP",
        ) from None
    return await require_mcp_http_operator(request)


def register_subagent_tools(mcp: FastMCP) -> None:
    @mcp.tool(name="list_subagents")
    async def list_scoped() -> dict[str, Any]:
        """List subagents available to the authenticated operator."""
        try:
            user = await _operator()
            with tenant_scope(user["tenant_id"]):
                return await list_subagents(user)
        except Exception as exc:
            return query_error(exc)

    @mcp.tool(name="subagent_query")
    async def query_scoped(
        name: str, nl_text: str, db_id: str = "", execute: bool = False,
        metrics: list[str] | None = None, metric_catalog_version: str = "",
        metric_dimensions: list[str] | None = None, metric_filters: dict[str, str] | None = None,
        metric_grain: Literal["", "day", "week", "month"] = "",
    ) -> dict[str, Any]:
        """Run a scoped natural-language or explicit catalog metric query."""
        try:
            user = await _operator()
            request = SubagentQueryRequest(
                nl_text=nl_text, database=db_id, execute=execute, metrics=metrics or [],
                metric_catalog_version=metric_catalog_version, metric_grain=metric_grain,
                metric_dimensions=metric_dimensions or [], metric_filters=metric_filters or {},
            )
            with tenant_scope(user["tenant_id"]):
                return await query_subagent(name, request, user)
        except Exception as exc:
            return query_error(exc)
