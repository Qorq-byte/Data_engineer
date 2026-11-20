"""One request service for JSON, SSE and MCP; YAML/Runner own all node transitions."""

from __future__ import annotations

import asyncio
import time
import uuid
from contextlib import suppress
from dataclasses import asdict, replace
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from app.audit.store import UsageAuditStore
from app.auth.tenant import DEFAULT_TENANT_ID, get_current_tenant_id
from app.core.query_evidence import candidate_evidence, supplied_schema
from app.core.sql_validator import SQLValidator
from app.db.query_targets import resolve_query_targets
from app.harness.config_loader import ConfigLoader
from app.harness.constraint import ConstraintEnforcer, ConstraintLimits
from app.harness.events import NodeEvent, NodeEventStatus
from app.harness.plan_loader import PlanLoader
from app.harness.runner import WorkflowRunner
from app.harness.session_keys import scoped_session_key
from app.harness.shared_query_runtime import current_tenant_release_snapshot, shared_query_scope
from app.knowledge.metric_catalog import MetricSelection
from app.llm.budget import active_budget
from app.llm.factory import get_default_router
from app.llm.protocols import CompletionRouter
from app.llm.usage import active_usage
from app.models.domain import DomainConfig
from app.models.schema import SchemaSnapshot
from app.models.workflow import WorkflowPlan
from app.workflow.contracts import (
    QueryCapacityError,
    QueryEventHandler,
    QueryEventName,
    QueryInput,
    QueryPlanError,
)
from app.workflow.plan_runtime import (
    NodeDependencies,
    build_registry,
    plan_blocker,
    plan_has_node,
    plan_output,
    plan_stages,
    request_overrides,
    stage_for,
)
from app.workflow.query_audit import QueryAuditState, finish_audit
from app.workflow.query_domain import resolve_query_domain

if TYPE_CHECKING:
    from app.knowledge.schema_retriever import SchemaRetriever

_ROOT = Path(__file__).resolve().parents[1]
_CONFIG_LOADER = ConfigLoader(str(_ROOT / "config/agent.yml"))
_PLAN_LOADER = PlanLoader(str(_ROOT / "workflow/plans"))
_PLAN_MTIME = -1
_PLAN_STAMPS: dict[tuple[int, str], int] = {}


def _request_plan() -> WorkflowPlan:
    global _PLAN_MTIME
    stamp = (_ROOT / "workflow/plans/request_query.yml").stat().st_mtime_ns
    if stamp != _PLAN_MTIME:
        plan = _PLAN_LOADER.reload("request_query")
        _PLAN_MTIME = stamp
        return plan
    return _PLAN_LOADER.load("request_query")


def _plan_for(plan_id: str) -> WorkflowPlan:
    """Load *plan_id* with the same mtime-based refresh as request_query."""
    if plan_id == "request_query":
        return _request_plan()
    path = _ROOT / "workflow/plans" / f"{plan_id}.yml"
    if not path.exists():
        raise QueryPlanError(f"未知工作流计划: {plan_id}")
    key = (id(_PLAN_LOADER), plan_id)
    stamp = path.stat().st_mtime_ns
    if _PLAN_STAMPS.get(key) != stamp:
        plan = _PLAN_LOADER.reload(plan_id)
        _PLAN_STAMPS[key] = stamp
        return plan
    return _PLAN_LOADER.load(plan_id)


async def _resolve_plan(body: Any) -> tuple[WorkflowPlan, Any]:
    """Resolve the workflow plan for a request and validate it can run.

    Returns ``(plan, decision)`` where ``decision`` is a ``PlanDecision`` when
    automatic routing ran, or ``None`` for an explicit/default request.
    """
    from app.workflow.plan_selection import select_plan

    requested = (getattr(body, "plan_id", "") or "request_query").strip()
    decision = None

    if getattr(body, "auto_plan", False):
        decision = await select_plan(body.nl_text, getattr(body, "domain", "") or "")
        plan_id = decision.plan_id
    else:
        plan_id = requested

    if plan_id == "metric_query":
        from app.config.settings import settings
        from app.harness.metric_catalog_runtime import configured_metric_catalog
        from app.knowledge.metric_catalog import CatalogError

        if settings.shared_worker_release:
            # Infrastructure drift is 503, not a bad metric name/version (400).
            with suppress(CatalogError):
                configured_metric_catalog()

    blocker = plan_blocker(plan_id)
    if blocker:
        raise QueryPlanError(f"工作流计划 {plan_id} 当前不可执行：{blocker}")

    plan = _plan_for(plan_id)

    if plan_has_node(plan, "reflection") and not (
        body.execute and str(getattr(body, "database", "")).strip()
    ):
        raise QueryPlanError(
            f"工作流计划 {plan_id} 含执行反馈回路，需要 execute=true 且指定 database"
        )

    if decision is not None:
        decision.requested = requested
        if plan_id != requested and requested != "request_query":
            decision.reason = f"{decision.reason}; requested '{requested}'"
    return plan, decision


def describe_query_policy() -> dict[str, Any]:
    """Read the same policy sources as requests; never apply stored UI settings.

    This is a current-process snapshot for new requests, not a health check or
    the policy captured by an already running request. Keep execution unchanged.
    """
    from app.config.settings import settings

    constraints = _CONFIG_LOADER.load().constraints
    plan = _request_plan()

    def entry(value: bool | int | float, source: str) -> dict[str, Any]:
        return {"value": value, "source": source}

    return {
        "scope": (
            "JSON/SSE default request_query; plan_id selects another executable template "
            "and auto_plan enables capability-filtered selection; MCP nl_query shares this path"
        ),
        "workflow": {
            "plan_id": plan.id,
            "source": "app/workflow/plans/request_query.yml",
            "controller": "WorkflowRunner",
            "node_order": [{"id": node.id, "node": node.node} for node in plan.node_order],
            "max_iterations": max(0, plan.max_iterations),
        },
        "limits": {
            "read_only": entry(True, "ExecuteSQLNode: unconditional read-only SQL check"),
            "max_llm_calls": entry(
                max(0, constraints.max_llm_calls_per_query),
                "agent.yml constraints.max_llm_calls_per_query; shared across query targets",
            ),
            "max_retries": entry(
                max(0, min(plan.failure_policy.max_retries, constraints.max_node_retries)),
                "min(request_query.failure_policy.max_retries, agent.yml max_node_retries)",
            ),
            "max_result_rows": entry(
                max(1, min(constraints.max_result_rows, settings.max_result_rows)),
                "max(1, min(agent.yml max_result_rows, Settings.max_result_rows)); per execution",
            ),
            "statement_timeout_ms": entry(
                max(1, min(constraints.statement_timeout_ms, settings.statement_timeout_ms)),
                "max(1, min(agent.yml statement_timeout_ms, Settings.statement_timeout_ms))",
            ),
            "query_timeout_s": entry(
                settings.query_timeout_seconds, "Settings.query_timeout_seconds; JSON/SSE deadline"
            ),
            "session_ttl_s": entry(
                settings.session_ttl_seconds,
                "Settings.session_ttl_seconds; process-local query session eviction",
            ),
        },
        "candidate_policy": "request.num_candidates; MCP nl_query requests one candidate",
        "direct_sql_scope": (
            "Direct SQL uses ExecuteSQLNode read-only checks and Settings row/statement caps, "
            "not this workflow's LLM/retry budget or HTTP query deadline"
        ),
        "snapshot_scope": "current process, read time; not in-flight requests or driver health",
    }


async def run_query(
    body: QueryInput,
    emit: QueryEventHandler | None = None,
    *,
    llm_router: CompletionRouter | None = None,
    schema_retriever: SchemaRetriever | None = None,
) -> dict[str, Any]:
    from app.config.settings import settings
    from app.db.runtime import gate

    # Plan resolution happens before admission so a bad plan never occupies a
    # slot, creates a session, or makes an LLM call.
    plan, decision = await _resolve_plan(body)
    from app.workflow.metric_runtime import metric_selection

    selected_metrics = metric_selection(body, plan.id)

    admission = gate("query-admission", max(1, settings.max_concurrent_queries))
    if admission.locked():
        raise QueryCapacityError("Query capacity reached; retry explicitly later")
    async with admission:
        domain = await resolve_query_domain(body.domain)
        if not body.session_id:
            body.session_id = f"s_{uuid.uuid4().hex[:16]}"
        session_key = scoped_session_key(body.session_id)
        async with shared_query_scope(body.session_id), gate(("session", session_key)):
            audit = QueryAuditState(
                capture_candidate_digests=settings.llm_audit_candidate_digests,
            )
            if settings.llm_audit_path.strip():
                audit.store = UsageAuditStore(
                    settings.llm_audit_path, settings.llm_audit_max_records
                )
            result: dict[str, Any] | None = None
            try:
                result = await _run_query(
                    body,
                    emit,
                    llm_router=llm_router,
                    audit=audit,
                    schema_retriever=schema_retriever,
                    plan=plan,
                    decision=decision,
                    selected_metrics=selected_metrics,
                    domain=domain,
                )
                status = result.get("status")
                audit.outcome = (
                    status
                    if isinstance(status, str) and status in ("completed", "failed")
                    else "error"
                )
            except asyncio.CancelledError:
                audit.outcome = "cancelled"
                raise
            finally:
                persistence = await finish_audit(
                    audit,
                    body.session_id,
                    result.get("candidates") if result is not None else None,
                )
            assert result is not None
            result["llm_audit"] = {"status": persistence, "query_id": audit.query_id}
            if persistence == "failed":
                result.setdefault("warnings", []).append("LLM usage audit was not persisted")
            _record_plan_use(body, plan, result, decision)
            return result


def _record_plan_use(
    body: QueryInput, plan: WorkflowPlan, result: dict[str, Any], decision: Any
) -> None:
    """Feed plan history only when selection can use it; never fail a request."""
    if decision is None and plan.id == "request_query":
        return
    from app.workflow.plan_selection import record_decision

    record_decision(
        body.nl_text,
        plan.id,
        domain=str(getattr(body, "domain", "") or ""),
        success=result.get("status") == "completed",
    )


async def _run_query(
    body: QueryInput,
    emit: QueryEventHandler | None = None,
    *,
    llm_router: CompletionRouter | None = None,
    audit: QueryAuditState,
    domain: DomainConfig | None,
    schema_retriever: SchemaRetriever | None = None,
    plan: WorkflowPlan,
    decision: Any = None,
    selected_metrics: MetricSelection | None = None,
) -> dict[str, Any]:
    """Execute the same pipeline for every protocol; emit only actual node events."""
    from app.api.query import _commit_query_session, _detect_metadata_query, _load_query_session
    from app.api.workflows import record_trace
    from app.storage.mysql_store import get_store

    started = time.perf_counter()
    query_id = audit.query_id
    session_id = body.session_id or f"s_{uuid.uuid4().hex[:16]}"
    warnings: list[str] = []
    errors: list[str] = []
    if body.execute and not plan_has_node(plan, "validate_sql"):
        warnings.append(
            f"计划 {plan.id} 不包含校验节点；执行仍强制只读、行数上限与语句超时"
        )
    config = _CONFIG_LOADER.load()
    limits = ConstraintEnforcer(ConstraintLimits(**asdict(config.constraints)))
    router = llm_router
    if router is None and selected_metrics is None:
        if get_current_tenant_id() == DEFAULT_TENANT_ID:
            router = get_default_router()
        else:
            from app.api.settings import resolve_tenant_query_router

            router = await resolve_tenant_query_router(body.model)
    from app.knowledge.schema_retriever import SchemaRetriever
    from app.rag.engine import get_schema_rag

    retriever = schema_retriever
    if retriever is None and selected_metrics is None:
        try:
            schema_rag = get_schema_rag()
            retriever = SchemaRetriever(schema_rag)
            provider = getattr(getattr(schema_rag, "_embedding", None), "provider", None)
            if type(provider).__name__ == "MockEmbeddingProvider":
                warnings.append("MOCK embeddings: retrieval is not a semantic quality guarantee")
        except RuntimeError:
            warnings.append("RAG unavailable; schema linking uses keyword matching")
    if getattr(router, "mock_mode", False) and selected_metrics is None:
        warnings.append("MOCK LLM: output is a test placeholder, not a verified database answer")
    if body.domain not in {"", "default"} and domain is None:
        warnings.append(f"Domain '{body.domain}' not found")

    domain_rules = list(domain.rules) if domain else []
    prompt_rules = domain_rules
    validation_rules = domain_rules
    applied_rules: list[dict[str, Any]] = []
    try:
        from app.learning.rule_registry import learned_rule_registry

        domain_name = domain.name if domain is not None else body.domain
        learned = (
            learned_rule_registry.business_rules(domain_name)
            if current_tenant_release_snapshot() is None else []
        )
        if learned_rule_registry.last_error:
            warnings.append("学习规则存储不可读；本次未应用已审批规则")
        matched = learned_rule_registry.match(body.nl_text, learned)
        if matched:
            # PromptBuilder filters by pattern against the question, so the
            # matched rules keep their patterns.  SQLValidator filters by
            # pattern against the SQL, so enforcement is scoped to queries the
            # rule already matched and the pattern is cleared for that check.
            prompt_rules = [*domain_rules, *matched]
            scoped = [replace(rule, pattern="") for rule in matched if rule.enforce]
            validation_rules = [*domain_rules, *scoped]
            applied_rules = [
                {
                    "id": rule.id,
                    "description": rule.description,
                    "pattern": rule.pattern,
                    "source": "learned",
                }
                for rule in matched
            ]
    except Exception:
        warnings.append("学习规则不可用；本次仅使用领域配置规则")

    prompt_domain = domain
    if applied_rules:
        # PromptBuilder reads rules from the domain object, so learned rules
        # must be merged there to reach generation rather than validation only.
        if domain is not None:
            prompt_domain = replace(domain, rules=prompt_rules)
        else:
            prompt_domain = DomainConfig(name="", rules=prompt_rules)

    resolved_targets = (
        await resolve_query_targets(body.database, warnings) if body.link_schema else {}
    )
    schemas = {
        db_id: (target.schema, target.database_name, target.dialect)
        for db_id, target in resolved_targets.items()
    }
    if selected_metrics is not None and set(schemas) != {selected_metrics.database}:
        raise QueryPlanError("metric_query could not capture its connected schema and database")
    user_schema = None
    ddl = getattr(body, "schema_ddl", "").strip()
    notes = getattr(body, "schema_notes", "").strip()
    if ddl and not schemas:
        try:
            user_schema = supplied_schema(ddl, body.dialect)
        except Exception as exc:
            # Reject malformed declarations instead of silently inventing a replacement.
            raise ValueError("用户提供的表结构无法解析，请检查 CREATE TABLE 声明") from exc
    if not schemas:
        warnings.append("No connected schema; generated SQL is not an executed database answer")
    source = "database" if schemas else ("user_schema" if ddl or notes else "inferred")
    source_note = (
        "依据所选数据库结构；缓存命中与验证范围见候选检查依据" if schemas else
        "依据用户提供的结构或说明生成，未经真实数据库验证" if ddl or notes else
        "基于需求推断生成，未经真实数据库验证"
    )
    session, session_revision = await _load_query_session(session_id)
    history = [{"nl_input": t["nl_input"], "sql": t["sql"]} for t in session["turns"][-8:]]
    all_candidates = []
    linked_tables: list[str] = []
    linked_columns: list[dict[str, Any]] = []
    primary_validation = None
    execution = None
    sqr = None
    last_context: dict[str, Any] = {}
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
            "plan_id": plan.id,
            "stages": plan_stages(plan)
            or ["parse", "retrieve", "generate", "validate", "execute"],
        },
    )
    collector = audit.collector
    usage_token = active_usage.set(collector)
    budget_token = active_budget.set(limits)
    try:
        targets: list[tuple[str, tuple[SchemaSnapshot | None, str, str]]] = [
            (db_id, (schema, db_name, dialect))
            for db_id, (schema, db_name, dialect) in schemas.items()
        ] or [("", (user_schema, "", body.dialect))]
        for db_id, (schema, db_name, dialect) in targets:
            execution_error = ""
            metadata = (
                _detect_metadata_query(body.nl_text, {db_id: (schema, db_name, dialect)})
                if schema and db_id and plan_has_node(plan, "generate_sql")
                else None
            )
            dependencies = NodeDependencies(
                retriever=retriever, router=router, dialect=dialect,
                metric_selection=selected_metrics,
            )
            registry = build_registry(plan, dependencies)
            node_configs = request_overrides(
                plan,
                body=body,
                limits=limits,
                db_id=db_id,
                dialect=dialect,
                db_name=db_name,
                prepared=metadata,
                notes=notes,
            )
            for node_name in registry.list_all():
                node = registry.get(node_name)
                registered = config.nodes.get(node_name)
                if registered and hasattr(node, "agentic_config"):
                    node.agentic_config.permission_rules = [registered.permissions]
                for definition in plan.node_order:
                    if definition.node != node_name:
                        continue
                    node_configs.setdefault(definition.id, {})
                    if registered:
                        node_configs[definition.id].update(
                            {"tools": registered.tools, "permissions": registered.permissions}
                        )

            async def on_node_event(
                status: NodeEventStatus, data: NodeEvent, target_db_id: str = db_id
            ) -> None:
                nonlocal execution_error
                node_name = data["node"]
                stage = stage_for(node_name)
                output = data.get("output")
                if (
                    node_name == "execute_sql" and output and output.errors
                    and output.metadata.get("status") == "execution_error"
                ):
                    execution_error = "; ".join(output.errors)
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
                actual_nodes.append(node_name)
                if node_name == "parse_nl" and output.result is not None:
                    parsed = output.result
                    await publish(
                        "parse",
                        {
                            "language": parsed.language,
                            "intent": parsed.intent.value,
                            "confidence": parsed.confidence,
                        },
                    )
                elif node_name == "schema_linking":
                    await publish("schema", output.result or {})
                elif node_name in {"generate_sql", "metric_resolve"}:
                    await publish(
                        "sql",
                        {
                            "primary_sql": (
                                output.context.get("primary_sql") if node_name == "metric_resolve"
                                else (output.result or {}).get("primary_sql")
                            ),
                            "db_id": target_db_id,
                        },
                    )
                elif node_name == "validate_sql":
                    report = output.context.get("validation_report")
                    if report:
                        await publish("validation", asdict(report))
                elif node_name == "execute_sql" and not output.errors:
                    await publish("execution", {"db_id": target_db_id, **(output.result or {})})

            runner = WorkflowRunner(plan, registry, constraints=limits)
            result = await runner.run(
                body.nl_text,
                session_id,
                node_configs=node_configs,
                on_node_event=on_node_event,
                reset_constraints=False,
                schema=schema,
                domain=prompt_domain,
                history=history,
                connection=resolved_targets[db_id].connection if db_id else None,
                db_id=db_id,
                require_validation=bool(body.execute and plan_has_node(plan, "validate_sql")),
                business_rules=validation_rules,
            )
            context = result.context
            last_context = context
            statuses.append(result.status)
            sqr = context.get("sqr") or sqr
            if result.trace:
                traces.append(result.trace.workflow_id)
                if result.trace.persistence_error:
                    warnings.append("Workflow trace could not be persisted")
            if result.error:
                errors.append(f"{db_id or source}: {result.error}")
            linked_tables.extend(
                context.get("linked_tables") or context.get("relevant_tables", [])
            )
            linked_columns.extend(context.get("linked_columns", []))
            for warning in context.get("schema_linking_warnings", []):
                if warning not in warnings:
                    warnings.append(warning)
            for warning in context.get("generation_warnings", []):
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
            basis = (
                "cached_schema" if schema and schema.from_cache else "live_schema"
            ) if db_id else source
            for index, candidate in enumerate(candidates):
                candidate.confidence = 0.0  # Legacy field, no calibrated probability exists.
                candidate.dialect = dialect
                if index > 0 and (body.run_validation or body.execute):
                    candidate.validation = asdict(await SQLValidator(dialect).validate(
                        candidate.sql_text, schema=schema, rules=validation_rules,
                    ))
                candidate.evidence = candidate_evidence(
                    candidate, None if metadata else schema, basis,
                    validation_rules, notes=notes,
                    ambiguities=[a.description for a in sqr.ambiguities] if sqr else [],
                    execution_error=execution_error if index == 0 else "",
                )
            all_candidates.extend(candidates)
    finally:
        audit.llm_calls = limits.llm_calls_used
        active_budget.reset(budget_token)
        active_usage.reset(usage_token)
    if body.execute and not schemas:
        warnings.append("无可用数据库，仅生成 SQL；未执行，未经真实数据库验证")
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
        "plan_id": plan.id,
        "workflow_ids": traces,
        "llm_calls": limits.llm_calls_used,
        "llm_usage": collector.summary(),
        "nl_text": body.nl_text,
        "language": sqr.language if sqr else "",
        "intent": sqr.intent.value if sqr else "UNKNOWN",
        "confidence": 0.0,
        "candidates": [asdict(c) for c in all_candidates],
        "primary_sql": primary_sql,
        "validation": primary_validation,
        "execution": execution,
        "linked_tables": list(dict.fromkeys(linked_tables)),
        "linked_columns": linked_columns,
        "plan_output": plan_output(plan, last_context),
        "applied_rules": applied_rules,
        "warnings": warnings,
        "errors": errors,
        "source": source,
        "source_note": source_note,
    }
    if decision is not None:
        response["plan_selection"] = decision.to_dict()
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
    await _commit_query_session(session_id, session, session_revision)
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
        plan=plan.id,
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
