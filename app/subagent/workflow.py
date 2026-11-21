"""Build scoped REST requests; execution remains owned by the fixed YAML Harness."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal

from app.models.subagent import SubagentConfig

if TYPE_CHECKING:
    from app.api.query import QueryRequest


def scoped_query(
    config: SubagentConfig,
    *,
    nl_text: str,
    domain: str,
    database: str,
    execute: bool,
    num_candidates: int,
    context: dict[str, Any] | None,
    metrics: list[str] | None = None,
    metric_catalog_version: str = "",
    metric_dimensions: list[str] | None = None,
    metric_filters: dict[str, str] | None = None,
    metric_grain: Literal["", "day", "week", "month"] = "",
) -> QueryRequest:
    """Translate only supported fields, rejecting overrides before any model/DB call.

    Only explicitly bound registry db_ids are accepted. Legacy connection_ref/alias
    declarations are descriptive and cannot silently authorize a target connection.
    """
    from app.api.query import QueryRequest
    from app.db.connections import ConnectionFactory

    use_metrics = any((
        metrics, metric_catalog_version, metric_dimensions, metric_filters, metric_grain,
    ))
    if use_metrics and "metric_query" not in config.capabilities:
        raise PermissionError("Subagent metric queries require the metric_query capability")
    if not use_metrics and "nl_query" not in config.capabilities:
        raise NotImplementedError("Subagent REST queries require the nl_query capability")
    if context:
        raise ValueError("Subagent REST context overrides are unsupported; use explicit fields")
    if domain and domain != config.domain:
        raise PermissionError("Subagent domain is fixed by configuration")
    scope = config.context
    allowed = {
        entry["db_id"] for entry in scope.databases
        if isinstance(entry.get("db_id"), str) and entry["db_id"]
    }
    if not allowed:
        raise LookupError("Subagent needs configured context.databases[].db_id bindings")
    if not database:
        if len(allowed) != 1:
            raise ValueError("Select an explicit database from the subagent scope")
        database = next(iter(allowed))
    if database not in allowed:
        raise PermissionError("Database is outside the configured subagent scope")
    try:
        ConnectionFactory.get_info(database)
        ConnectionFactory.get(database)
    except KeyError:
        raise LookupError("The configured subagent database is not connected") from None
    if execute and not scope.enable_execution:
        raise PermissionError("SQL execution is disabled for this subagent")
    if num_candidates > 1 and not scope.enable_multi_candidate:
        raise PermissionError("Multiple candidates are disabled for this subagent")
    return QueryRequest(
        nl_text=nl_text,
        domain=config.domain,
        database=database,
        execute=execute,
        num_candidates=num_candidates,
        plan_id="metric_query" if use_metrics else "request_query",
        auto_plan=False,
        metrics=metrics or [], metric_catalog_version=metric_catalog_version,
        metric_dimensions=metric_dimensions or [], metric_filters=metric_filters or {},
        metric_grain=metric_grain,
    )
