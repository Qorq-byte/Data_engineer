"""Authenticated metric tools; local-process trust is an explicit host decision."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal

from fastapi import HTTPException

from app.mcp.access import require_mcp_http_operator

if TYPE_CHECKING:
    from fastmcp import FastMCP


async def require_metric_operator(trust_local: bool) -> None:
    from fastmcp.server.dependencies import get_http_request

    try:
        request = get_http_request()
    except RuntimeError:
        if trust_local:
            return
        raise HTTPException(
            status_code=401, detail="Local metric tools require explicit host trust"
        ) from None
    await require_mcp_http_operator(request)


def register_metric_tools(mcp: FastMCP, *, trust_local: bool = False) -> None:
    @mcp.tool(
        name="list_metrics",
        description="List configured metrics and catalog version for one explicit domain/database.",
    )
    async def list_metrics(domain: str, db_id: str) -> dict[str, Any]:
        from app.mcp.query_projection import query_error
        from app.workflow.metric_runtime import metric_catalog_view

        try:
            await require_metric_operator(trust_local)
            return {"status": "completed", **(await metric_catalog_view(domain, db_id))}
        except Exception as exc:
            return query_error(exc)

    @mcp.tool(
        name="metric_query",
        description=(
            "Resolve catalog metrics through fixed validation and optional read-only execution. "
            "No model calls; execute defaults to false. Discover names/version with list_metrics."
        ),
    )
    async def metric_query(
        domain: str,
        db_id: str,
        metrics: list[str],
        metric_catalog_version: str,
        metric_dimensions: list[str] | None = None,
        metric_filters: dict[str, str] | None = None,
        execute: bool = False,
        metric_grain: Literal["", "day", "week", "month"] = "",
    ) -> dict[str, Any]:
        from app.api.query import QueryRequest, submit_query
        from app.mcp.query_projection import project_query, query_error

        try:
            await require_metric_operator(trust_local)
            response = await submit_query(QueryRequest(
                nl_text="Explicit catalog metric query", plan_id="metric_query",
                domain=domain, database=db_id, metrics=metrics,
                metric_catalog_version=metric_catalog_version,
                metric_dimensions=metric_dimensions or [], metric_filters=metric_filters or {},
                execute=execute, num_candidates=1, metric_grain=metric_grain,
            ))
            return project_query(response, require_execution=execute, include_plan_output=True)
        except Exception as exc:
            return query_error(exc)
