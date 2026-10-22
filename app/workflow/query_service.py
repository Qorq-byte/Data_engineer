"""One request service for JSON, SSE and MCP; YAML/Runner own all node transitions."""

from __future__ import annotations

import time
import uuid
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any

from app.db.query_targets import resolve_query_targets
from app.harness.config_loader import ConfigLoader
from app.harness.constraint import ConstraintEnforcer, ConstraintLimits
from app.harness.events import NodeEvent, NodeEventStatus
from app.harness.plan_loader import PlanLoader
from app.harness.runner import WorkflowRunner
from app.llm.budget import BudgetedRouter, active_budget
from app.llm.factory import get_default_router
from app.llm.protocols import CompletionRouter
from app.models.schema import SchemaSnapshot
from app.models.workflow import WorkflowPlan
from app.nodes.execute_sql import ExecuteSQLNode
from app.nodes.generate_sql import GenerateSQLNode
from app.nodes.parse_nl import ParseNLNode
from app.nodes.registry import NodeRegistry
from app.nodes.schema_linking import SchemaLinkingNode
from app.nodes.validate_sql import ValidateSQLNode
from app.workflow.contracts import QueryCapacityError, QueryEventHandler, QueryEventName, QueryInput

_ROOT = Path(__file__).resolve().parents[1]
_CONFIG_LOADER = ConfigLoader(str(_ROOT / "config/agent.yml"))
_PLAN_LOADER = PlanLoader(str(_ROOT / "workflow/plans"))
_PLAN_MTIME = -1


def _request_plan() -> WorkflowPlan:
    global _PLAN_MTIME
    stamp = (_ROOT / "workflow/plans/request_query.yml").stat().st_mtime_ns
    if stamp != _PLAN_MTIME:
        plan = _PLAN_LOADER.reload("request_query")
        _PLAN_MTIME = stamp
        return plan
    return _PLAN_LOADER.load("request_query")


async def run_query(
    body: QueryInput,
    emit: QueryEventHandler | None = None,
    *,
    llm_router: CompletionRouter | None = None,
) -> dict[str, Any]:
    from app.config.settings import settings
    from app.db.runtime import gate

    admission = gate("query-admission", max(1, settings.max_concurrent_queries))
    if admission.locked():
        raise QueryCapacityError("Query capacity reached; retry explicitly later")
    async with admission:
        if not body.session_id:
            body.session_id = f"s_{uuid.uuid4().hex[:16]}"
        async with gate(("session", body.session_id)):
            return await _run_query(body, emit, llm_router=llm_router)


async def _run_query(
    body: QueryInput,
    emit: QueryEventHandler | None = None,
    *,
    llm_router: CompletionRouter | None = None,
) -> dict[str, Any]:
    """Execute the same pipeline for every protocol; emit only actual node events."""
    from app.api.domains import _domain_manager
    from app.api.query import _detect_metadata_query, _get_or_create_session
    from app.api.workflows import record_trace
    from app.storage.mysql_store import get_store

    started = time.perf_counter()
    query_id = f"q_{uuid.uuid4().hex[:12]}"
    session_id = body.session_id or f"s_{uuid.uuid4().hex[:16]}"
    warnings: list[str] = []
    errors: list[str] = []
    config = _CONFIG_LOADER.load()
    plan = _request_plan()
    limits = ConstraintEnforcer(ConstraintLimits(**asdict(config.constraints)))
    router = llm_router or get_default_router()
    from app.knowledge.schema_retriever import SchemaRetriever
    from app.rag.engine import get_schema_rag

    retriever = None
    try:
        schema_rag = get_schema_rag()
        retriever = SchemaRetriever(schema_rag)
        provider = getattr(getattr(schema_rag, "_embedding", None), "provider", None)
        if type(provider).__name__ == "MockEmbeddingProvider":
            warnings.append("MOCK embeddings: retrieval is not a semantic quality guarantee")
    except RuntimeError:
        warnings.append("RAG unavailable; schema linking uses keyword matching")
    if getattr(router, "mock_mode", False):
        warnings.append("MOCK LLM: output is a test placeholder, not a verified database answer")
    domain = _domain_manager.get(body.domain) if body.domain else None
    if body.domain not in {"", "default"} and domain is None:
        warnings.append(f"Domain '{body.domain}' not found")
    resolved_targets = (
        await resolve_query_targets(body.database, warnings) if body.link_schema else {}
    )
    schemas = {
        db_id: (target.schema, target.database_name, target.dialect)
        for db_id, target in resolved_targets.items()
    }
    if not schemas:
        warnings.append("No connected schema; generated SQL is not an executed database answer")
    source = "database" if schemas else ("domain" if domain else "llm_auto")
    source_note = (
        "" if schemas else "SQL is based on domain/model knowledge; no connected database result"
    )
    session = _get_or_create_session(session_id)
    history = [{"nl_input": t["nl_input"], "sql": t["sql"]} for t in session["turns"][-8:]]
    all_candidates = []
    linked_tables: list[str] = []
    linked_columns: list[dict[str, Any]] = []
    primary_validation = None
    execution = None
    sqr = None
    traces = []
    statuses = []
    actual_nodes: list[str] = []

    async def publish(event: QueryEventName, payload: dict[str, Any]) -> None:
        if emit:
            await emit(event, {"query_id": query_id, "session_id": session_id, **payload})

    await publish(
        "workflow_start",
        {
            "workflow_id": query_id,
            "plan_id": "request_query",
            "stages": ["parse", "retrieve", "generate", "validate", "execute"],
        },
    )
    budget_token = active_budget.set(limits)
    try:
        targets: list[tuple[str, tuple[SchemaSnapshot | None, str, str]]] = [
            (db_id, (schema, db_name, dialect))
            for db_id, (schema, db_name, dialect) in schemas.items()
        ] or [("", (None, "", body.dialect))]
        for db_id, (schema, db_name, dialect) in targets:
            metadata = (
                _detect_metadata_query(body.nl_text, {db_id: (schema, db_name, dialect)})
                if schema
                else None
            )
            registry = NodeRegistry()
            nodes = [
                ParseNLNode(),
                SchemaLinkingNode(retriever=retriever),
                GenerateSQLNode(router=BudgetedRouter(router)),
                ValidateSQLNode(dialect=dialect),
                ExecuteSQLNode(),
            ]
            node_configs: dict[str, dict[str, Any]] = {}
            ids = ["parse", "link", "gen", "validate", "exec"]
            for node_id, node in zip(ids, nodes, strict=True):
                node_configs[node_id] = {}
                registered = config.nodes.get(node.name)
                if registered:
                    if hasattr(node, "agentic_config"):
                        node.agentic_config.permission_rules = [registered.permissions]
                    node_configs[node_id] = {
                        "tools": registered.tools,
                        "permissions": registered.permissions,
                    }
                registry.register(node)
            node_configs["link"].update({"enabled": body.link_schema, "db_id": db_id})
            node_configs["gen"].update(
                {
                    "num_candidates": body.num_candidates,
                    "model": body.model,
                    "dialect": dialect,
                    "database_name": db_name,
                    "prepared_candidates": metadata,
                }
            )
            node_configs["validate"].update(
                {
                    "enabled": body.run_validation or body.execute,
                    "dialect": dialect,
                    "skip_schema": bool(metadata),
                }
            )
            node_configs["exec"].update(
                {
                    "enabled": bool(body.execute and db_id),
                    "max_rows": limits.limits.max_result_rows,
                    "timeout_ms": limits.limits.statement_timeout_ms,
                }
            )

            async def on_node_event(
                status: NodeEventStatus, data: NodeEvent, target_db_id: str = db_id
            ) -> None:
                node_id = data["node_id"]
                stage = {
                    "parse": "parse",
                    "link": "retrieve",
                    "gen": "generate",
                    "validate": "validate",
                    "exec": "execute",
                }[node_id]
                output = data.get("output")
                await publish(
                    "stage",
                    {
                        "stage": stage,
                        "status": status,
                        "skipped": bool(output and output.metadata.get("status") == "skipped"),
                        "db_id": target_db_id,
                        "duration_ms": data.get("latency_ms", 0),
                        "attempt": data["attempt"],
                    },
                )
                if output is None or output.metadata.get("status") == "skipped":
                    return
                actual_nodes.append(data["node"])
                if node_id == "parse" and output.result is not None:
                    parsed = output.result
                    await publish(
                        "parse",
                        {
                            "language": parsed.language,
                            "intent": parsed.intent.value,
                            "confidence": parsed.confidence,
                        },
                    )
                elif node_id == "link":
                    await publish("schema", output.result or {})
                elif node_id == "gen":
                    await publish(
                        "sql",
                        {
                            "primary_sql": (output.result or {}).get("primary_sql"),
                            "db_id": target_db_id,
                        },
                    )
                elif node_id == "validate":
                    report = output.context.get("validation_report")
                    if report:
                        await publish("validation", asdict(report))
                elif node_id == "exec" and not output.errors:
                    await publish("execution", {"db_id": target_db_id, **(output.result or {})})

            runner = WorkflowRunner(plan, registry, constraints=limits)
            result = await runner.run(
                body.nl_text,
                session_id,
                node_configs=node_configs,
                on_node_event=on_node_event,
                reset_constraints=False,
                schema=schema,
                domain=domain,
                history=history,
                connection=resolved_targets[db_id].connection if db_id else None,
                db_id=db_id,
                require_validation=body.execute,
                business_rules=domain.rules if domain else [],
            )
            context = result.context
            statuses.append(result.status)
            sqr = context.get("sqr") or sqr
            if result.trace:
                traces.append(result.trace.workflow_id)
                if result.trace.persistence_error:
                    warnings.append("Workflow trace could not be persisted")
            if result.error:
                errors.append(f"{db_id or source}: {result.error}")
            linked_tables.extend(context.get("linked_tables", []))
            linked_columns.extend(context.get("linked_columns", []))
            for warning in context.get("schema_linking_warnings", []):
                if warning not in warnings:
                    warnings.append(warning)
            candidates = context.get("candidates", [])
            report = context.get("validation_report")
            for candidate in candidates:
                candidate.db_id = db_id
            if candidates and report:
                candidates[0].validation = asdict(report)
                if primary_validation is None:
                    primary_validation = asdict(report)
            if candidates and context.get("last_execution") is not None:
                candidates[0].execution = context["last_execution"]
                if execution is None:
                    execution = context["last_execution"]
            all_candidates.extend(candidates)
    finally:
        active_budget.reset(budget_token)
    if body.execute and not schemas:
        errors.append("No connected database is available for execution")
    status = (
        "completed"
        if statuses and all(s == "completed" for s in statuses) and not errors
        else "failed"
    )
    primary_sql = all_candidates[0].sql_text if all_candidates else None
    response = {
        "query_id": query_id,
        "session_id": session_id,
        "status": status,
        "plan_id": "request_query",
        "workflow_ids": traces,
        "llm_calls": limits.llm_calls_used,
        "nl_text": body.nl_text,
        "language": sqr.language if sqr else "",
        "intent": sqr.intent.value if sqr else "UNKNOWN",
        "confidence": sqr.confidence if sqr else 0,
        "candidates": [asdict(c) for c in all_candidates],
        "primary_sql": primary_sql,
        "validation": primary_validation,
        "execution": execution,
        "linked_tables": list(dict.fromkeys(linked_tables)),
        "linked_columns": linked_columns,
        "warnings": warnings,
        "errors": errors,
        "source": source,
        "source_note": source_note,
    }
    turn = {
        "turn_id": query_id,
        "nl_input": body.nl_text,
        "sql": primary_sql or "",
        "source": source,
        "status": status,
        "timestamp": datetime.now().isoformat(),
    }
    from app.config.settings import settings

    session["turns"].append(turn)
    session["turns"][:] = session["turns"][-max(1, settings.session_max_turns) :]
    session["updated_at"] = turn["timestamp"]
    store = get_store()
    if store is not None:
        try:
            await store.save_query(
                {
                    "session_id": session_id,
                    "query_id": query_id,
                    "nl_text": body.nl_text,
                    "sql_generated": primary_sql or "",
                    "domain": body.domain,
                    "db_id": body.database,
                    "response_time_ms": int((time.perf_counter() - started) * 1000),
                }
            )
        except Exception:
            warnings.append("Query history was not persisted; session is process-local")
    record_trace(
        plan="request_query",
        status="success" if status == "completed" else "failed",
        duration_ms=int((time.perf_counter() - started) * 1000),
        nl_input=body.nl_text,
        sql_generated=primary_sql or "",
        row_count=(execution or {}).get("row_count", 0),
        nodes_executed=actual_nodes,
        error="; ".join(errors),
    )
    if errors:
        await publish("error", {"code": "WORKFLOW_FAILED", "message": "; ".join(errors)})
    return response
