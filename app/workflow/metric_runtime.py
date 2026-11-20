"""Pre-admission metric scope/version checks; no workflow transitions or model calls."""

from typing import Any

from app.auth.tenant import get_current_tenant_id
from app.db.connections import ConnectionFactory
from app.harness.metric_catalog_runtime import (
    configured_metric_catalog,
    verify_metric_discovery_runtime,
)
from app.knowledge.metric_catalog import CatalogError, MetricSelection
from app.workflow.contracts import QueryPlanError


async def metric_catalog_view(domain: str, database: str) -> dict[str, Any]:
    """Describe one configured binding; configuration evidence, never schema certification."""
    if not domain.strip() or not database.strip() or len(domain) > 128 or len(database) > 128:
        raise QueryPlanError("Metric discovery requires an explicit short domain and database")
    await verify_metric_discovery_runtime()
    try:
        ConnectionFactory.get(database)
        ConnectionFactory.get_info(database)
    except KeyError:
        raise QueryPlanError("Metric discovery requires a connected database") from None
    try:
        catalog = configured_metric_catalog()
    except CatalogError as exc:
        raise QueryPlanError(str(exc)) from None
    binding = next(
        (
            item for item in catalog.bindings
            if (item.domain, item.database, item.tenant_id)
            == (domain, database, get_current_tenant_id())
        ),
        None,
    )
    if binding is None:
        raise QueryPlanError("No metric binding for the requested domain and database")
    return {
        "version": catalog.version, "sha256": catalog.sha256,
        "domain": domain, "database": database,
        "metrics": [
            {"name": metric.name, "table": metric.table, "column": metric.column,
             "aggregation": metric.aggregation, "dimensions": list(metric.dimensions)}
            for metric in sorted(binding.metrics, key=lambda item: item.name)
        ],
        "evidence": "configured_catalog; target_schema_and_business_semantics_unverified",
    }


def metric_selection(body: Any, plan_id: str) -> MetricSelection | None:
    fields = (
        getattr(body, "metrics", []), getattr(body, "metric_catalog_version", ""),
        getattr(body, "metric_dimensions", []), getattr(body, "metric_filters", {}),
    )
    if plan_id != "metric_query":
        if any(fields):
            raise QueryPlanError("Metric fields require plan_id=metric_query and auto_plan=false")
        return None
    if body.auto_plan or not body.database or not body.domain:
        raise QueryPlanError("metric_query requires domain/database and auto_plan=false")
    supplied_schema = (
        bool(getattr(body, "schema_ddl", "")) or bool(getattr(body, "schema_notes", ""))
    )
    if not body.link_schema or supplied_schema:
        raise QueryPlanError("metric_query requires connected schema without supplied DDL")
    if body.model or body.num_candidates != 1:
        raise QueryPlanError("metric_query requires num_candidates=1 and no model override")
    try:
        ConnectionFactory.get(body.database)
        ConnectionFactory.get_info(body.database)
    except KeyError:
        raise QueryPlanError("metric_query requires a connected database") from None
    try:
        return configured_metric_catalog().select(
            version=fields[1], domain=body.domain, database=body.database,
            metrics=fields[0], dimensions=fields[2], filters=fields[3],
            tenant_id=get_current_tenant_id(),
        )
    except CatalogError as exc:
        raise QueryPlanError(str(exc)) from None
