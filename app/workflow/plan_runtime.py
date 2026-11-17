"""Plan-driven node assembly for every workflow template.

The YAML plan and ``WorkflowRunner`` own node order.  This module only maps a
plan's ``node`` names to concrete node instances and request-scoped overrides,
so the query API can execute a template without hard-coding a five-node list.

Capability reporting is deliberately explicit: a template is executable only
when every node it references is constructible here.  ``metric_query`` stays
blocked because ``MetricResolveNode`` requires explicit metric names and the
deployment has no metric catalog.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.llm.budget import BudgetedRouter
from app.llm.protocols import CompletionRouter
from app.models.workflow import WorkflowPlan
from app.nodes.base import BaseNode
from app.nodes.dialect_translate import DialectTranslateNode
from app.nodes.execute_sql import ExecuteSQLNode
from app.nodes.explain_plan import ExplainPlanNode
from app.nodes.format_sql import FormatSQLNode
from app.nodes.generate_sql import GenerateSQLNode
from app.nodes.hybrid_search import HybridSearchNode
from app.nodes.metric_resolve import MetricResolveNode
from app.nodes.parse_nl import ParseNLNode
from app.nodes.reflection import ReflectionNode
from app.nodes.registry import NodeRegistry
from app.nodes.respond import RespondNode
from app.nodes.schema_linking import SchemaLinkingNode
from app.nodes.validate_sql import ValidateSQLNode

# ── Stage mapping ──────────────────────────────────────────────────────

# SSE consumers group node events into stages.  Every node that can appear in
# an executable plan must have an entry; an unknown node reports its own name
# rather than inventing a stage.
STAGE_BY_NODE: dict[str, str] = {
    "parse_nl": "parse",
    "schema_linking": "retrieve",
    "hybrid_search": "retrieve",
    "generate_sql": "generate",
    "validate_sql": "validate",
    "execute_sql": "execute",
    "reflection": "reflect",
    "respond": "respond",
    "metric_resolve": "resolve",
    "format_sql": "format",
    "explain_plan": "explain",
    "dialect_translate": "translate",
}

# Plans the request API may execute.  A non-empty reason blocks the plan with a
# 400 instead of failing halfway through a run.
PLAN_BLOCKERS: dict[str, str] = {
    "metric_query": (
        "metric_query needs explicit metric names and a configured metric catalog; "
        "none is configured in this deployment"
    ),
}

# Plans automatic routing may choose at the API boundary.  ``gensql_agentic``
# omits parse_nl, so the selector's "full pipeline" verdict maps to
# ``request_query`` (parse + link + generate + validate + execute) instead of
# silently dropping language and intent extraction.
AUTO_SELECTABLE: tuple[str, ...] = ("request_query", "ez_query")

DEFAULT_PLAN = "request_query"


def stage_for(node_name: str) -> str:
    """Return the SSE stage for a plan node name."""
    return STAGE_BY_NODE.get(node_name, node_name)


def plan_blocker(plan_id: str) -> str:
    """Return why *plan_id* cannot run, or an empty string when it can."""
    return PLAN_BLOCKERS.get(plan_id, "")


def executable_plan_ids(available: list[str] | tuple[str, ...]) -> list[str]:
    """Filter *available* plan IDs down to executable templates, sorted."""
    return sorted(plan_id for plan_id in available if not plan_blocker(plan_id))


def plan_stages(plan: WorkflowPlan) -> list[str]:
    """Return the ordered, de-duplicated stage list for a plan."""
    stages: list[str] = []
    for definition in plan.node_order:
        stage = stage_for(definition.node)
        if stage not in stages:
            stages.append(stage)
    return stages


def plan_has_node(plan: WorkflowPlan, node_name: str) -> bool:
    """Return True when the plan references *node_name*."""
    return any(definition.node == node_name for definition in plan.node_order)


# ── Node dependencies ──────────────────────────────────────────────────


@dataclass
class NodeDependencies:
    """Request-scoped collaborators injected into plan nodes.

    Everything is optional so a plan can be assembled for a request without a
    database, RAG index, or LLM provider; the nodes degrade explicitly.
    """

    retriever: Any = None
    router: CompletionRouter | None = None
    dialect: str = "ansi"
    metric_engine: Any = None


def _build_node(name: str, deps: NodeDependencies) -> BaseNode:
    """Construct a single node instance for *name*.

    Raises:
        ValueError: when the name is not part of the executable node set.
    """
    match name:
        case "parse_nl":
            return ParseNLNode()
        case "schema_linking":
            return SchemaLinkingNode(retriever=deps.retriever)
        case "hybrid_search":
            schema_rag, metric_rag, document_store = _rag_backends()
            return HybridSearchNode(
                schema_rag=schema_rag, metric_rag=metric_rag, document_store=document_store
            )
        case "generate_sql":
            router = BudgetedRouter(deps.router) if deps.router is not None else None
            return GenerateSQLNode(router=router)
        case "validate_sql":
            return ValidateSQLNode(dialect=deps.dialect)
        case "execute_sql":
            return ExecuteSQLNode()
        case "metric_resolve":
            return MetricResolveNode(engine=deps.metric_engine)
        case "reflection":
            return ReflectionNode()
        case "respond":
            router = BudgetedRouter(deps.router) if deps.router is not None else None
            return RespondNode(router=router)
        case "format_sql":
            return FormatSQLNode()
        case "explain_plan":
            return ExplainPlanNode()
        case "dialect_translate":
            return DialectTranslateNode()
        case _:
            raise ValueError(f"Node type '{name}' is not executable")


def _rag_backends() -> tuple[Any, Any, Any]:
    """Return configured RAG backends, or ``(None, None, None)`` when absent."""
    from app.rag import engine as rag_engine

    backends: list[Any] = []
    for getter in (
        rag_engine.get_schema_rag,
        rag_engine.get_metric_rag,
        rag_engine.get_document_store,
    ):
        try:
            backends.append(getter())
        except RuntimeError:
            backends.append(None)
        except Exception:
            backends.append(None)
    return backends[0], backends[1], backends[2]


def build_registry(plan: WorkflowPlan, deps: NodeDependencies) -> NodeRegistry:
    """Register one instance per distinct node name referenced by *plan*.

    ``WorkflowRunner`` resolves nodes by name, so repeated occurrences (for
    example ``generate_sql`` twice in the reflection plan) share one instance.
    """
    registry = NodeRegistry()
    for definition in plan.node_order:
        if definition.node in registry:
            continue
        registry.register(_build_node(definition.node, deps))
    return registry


# ── Request-scoped overrides ───────────────────────────────────────────


def request_overrides(
    plan: WorkflowPlan,
    *,
    body: Any,
    limits: Any,
    db_id: str,
    dialect: str,
    db_name: str,
    prepared: Any = None,
    notes: str = "",
) -> dict[str, dict[str, Any]]:
    """Build per-node-id overrides from the request and constraint limits.

    The plan still supplies temperature/mode/top_k/max_rows defaults; this
    function only injects values that come from the request or from enforced
    limits.  Only the first ``generate_sql`` node receives prepared candidates
    so a reflection ``revise`` step can never bypass generation.
    """
    overrides: dict[str, dict[str, Any]] = {}
    first_generate = True

    for definition in plan.node_order:
        config: dict[str, Any] = {}
        match definition.node:
            case "schema_linking":
                config = {"enabled": bool(body.link_schema), "db_id": db_id}
            case "generate_sql":
                config = {"dialect": dialect, "database_name": db_name, "schema_notes": notes}
                if first_generate:
                    config.update(
                        {
                            "num_candidates": body.num_candidates,
                            "model": body.model,
                            "prepared_candidates": prepared,
                        }
                    )
                    first_generate = False
            case "validate_sql":
                config = {
                    "enabled": bool(body.run_validation or body.execute),
                    "dialect": dialect,
                    "skip_schema": bool(prepared),
                }
            case "execute_sql":
                config = {
                    "enabled": bool(body.execute and db_id),
                    "max_rows": limits.limits.max_result_rows,
                    "timeout_ms": limits.limits.statement_timeout_ms,
                }
            case "hybrid_search":
                config = {"db_id": db_id}
        overrides[definition.id] = config

    return overrides


# ── Output summary ─────────────────────────────────────────────────────


def _jsonable(value: Any) -> Any:
    """Convert plan-specific node output into JSON-safe data."""
    from dataclasses import asdict, is_dataclass

    if value is None or isinstance(value, str | int | float | bool):
        return value
    if is_dataclass(value) and not isinstance(value, type):
        return {key: _jsonable(item) for key, item in asdict(value).items()}
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_jsonable(item) for item in value]
    return str(value)


def plan_output(plan: WorkflowPlan, context: dict[str, Any]) -> dict[str, Any]:
    """Summarise plan-specific results that the standard response omits.

    Only keys actually produced by the plan are included, so callers can tell
    "no response text" from "plan has no respond node".
    """
    output: dict[str, Any] = {}

    if "response" in context:
        output["response"] = _jsonable(context.get("response"))
    if "reflection" in context:
        output["reflection"] = _jsonable(context.get("reflection"))
    if "needs_revision" in context:
        output["needs_revision"] = bool(context.get("needs_revision"))
    if "fix_hints" in context:
        output["fix_hints"] = _jsonable(context.get("fix_hints"))

    search = context.get("hybrid_search_results")
    if search is not None:
        output["search"] = {
            "query": getattr(search, "query", ""),
            "target": getattr(search, "target", ""),
            "total_hits": getattr(search, "total_hits", 0),
            "schema_hits": len(getattr(search, "schema_results", []) or []),
            "metric_hits": len(getattr(search, "metric_results", []) or []),
            "document_hits": len(getattr(search, "document_results", []) or []),
            "errors": list(getattr(search, "errors", []) or []),
        }

    metric_sql = context.get("resolved_sql") or context.get("metric_sql")
    if metric_sql:
        output["resolved_sql"] = _jsonable(metric_sql)

    output["node_names"] = [definition.node for definition in plan.node_order]
    return output
