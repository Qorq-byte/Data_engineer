"""Workflow management API endpoints.

See SPEC 6.5 (Workflow Orchestration API) for the full specification.
"""
from __future__ import annotations

import contextlib
import time
from datetime import datetime
from typing import Any

from fastapi import APIRouter, HTTPException

router = APIRouter()

_WORKFLOW_PLANS: list[dict[str, Any]] = [
    {"id": "gensql_agentic", "name": "标准 NL→SQL 生成", "description": "单轮 NL→SQL，含 Schema 链接、生成、验证、执行", "node_count": 4, "nodes": [{"name": "schema_linking", "label": "Schema 链接", "icon": "\U0001f517"}, {"name": "generate_sql", "label": "SQL 生成", "icon": "\U0001f916"}, {"name": "validate_sql", "label": "SQL 验证", "icon": "\u2705"}, {"name": "execute_sql", "label": "SQL 执行", "icon": "\u25b6\ufe0f"}], "category": "standard"},
    {"id": "ez_query", "name": "快速查询通道", "description": "跳过验证的快速查询", "node_count": 3, "nodes": [{"name": "schema_linking", "label": "Schema 链接", "icon": "\U0001f517"}, {"name": "generate_sql", "label": "SQL 生成", "icon": "\U0001f916"}, {"name": "execute_sql", "label": "SQL 执行", "icon": "\u25b6\ufe0f"}], "category": "express"},
    {"id": "reflection", "name": "反射式生成", "description": "生成→执行→反思→修正", "node_count": 5, "nodes": [{"name": "schema_linking", "label": "Schema 链接", "icon": "\U0001f517"}, {"name": "generate_sql", "label": "SQL 生成", "icon": "\U0001f916"}, {"name": "execute_sql", "label": "SQL 执行", "icon": "\u25b6\ufe0f"}, {"name": "reflection", "label": "反思修正", "icon": "\U0001f504"}, {"name": "validate_sql", "label": "SQL 验证", "icon": "\u2705"}], "category": "deep"},
    {"id": "chat_agentic", "name": "多轮对话查询", "description": "多轮对话增量查询", "node_count": 5, "nodes": [{"name": "parse_nl", "label": "NL 解析", "icon": "\U0001f4dd"}, {"name": "hybrid_search", "label": "混合检索", "icon": "\U0001f50d"}, {"name": "generate_sql", "label": "SQL 生成", "icon": "\U0001f916"}, {"name": "validate_sql", "label": "SQL 验证", "icon": "\u2705"}, {"name": "respond", "label": "结果响应", "icon": "\U0001f4ac"}], "category": "chat"},
    {"id": "explore", "name": "Schema 探索", "description": "快速探索数据库 Schema", "node_count": 3, "nodes": [{"name": "parse_nl", "label": "NL 解析", "icon": "\U0001f4dd"}, {"name": "hybrid_search", "label": "混合检索", "icon": "\U0001f50d"}, {"name": "format", "label": "格式化输出", "icon": "\U0001f4cb"}], "category": "explore"},
    {"id": "metric_query", "name": "MetricFlow 指标查询", "description": "通过 MetricFlow 语义层查询", "node_count": 4, "nodes": [{"name": "metric_resolve", "label": "指标解析", "icon": "\U0001f4d0"}, {"name": "generate_sql", "label": "SQL 生成", "icon": "\U0001f916"}, {"name": "validate_sql", "label": "SQL 验证", "icon": "\u2705"}, {"name": "execute_sql", "label": "SQL 执行", "icon": "\u25b6\ufe0f"}], "category": "metric"},
]

# In-memory trace store — populated by real query executions
_TRACE_STORE: list[dict[str, Any]] = []
_next_trace_id = 1


def record_trace(
    plan: str,
    status: str,
    duration_ms: int,
    nl_input: str = "",
    sql_generated: str = "",
    row_count: int = 0,
    nodes_executed: list[str] | None = None,
    error: str = "",
) -> None:
    """Record a workflow execution trace. Called from query.py after each query."""
    global _next_trace_id
    now = datetime.now()
    trace_id = f"tr_{now.strftime('%Y%m%d')}_{_next_trace_id:06x}"
    _next_trace_id += 1
    _TRACE_STORE.append({
        "id": trace_id,
        "plan": plan,
        "status": status,
        "duration": f"{duration_ms / 1000:.2f}s",
        "duration_ms": duration_ms,
        "time": now.strftime("%H:%M:%S"),
        "nl_input": nl_input,
        "sql_generated": sql_generated,
        "row_count": row_count,
        "nodes_executed": nodes_executed or [],
        "error": error,
    })
    # Keep only last 100 traces
    if len(_TRACE_STORE) > 100:
        _TRACE_STORE[:] = _TRACE_STORE[-100:]


@router.get("/workflows")
async def list_workflows() -> dict:
    return {
        "plans": _WORKFLOW_PLANS,
        "total": len(_WORKFLOW_PLANS),
        "categories": sorted({p["category"] for p in _WORKFLOW_PLANS}),
    }


@router.get("/workflows/stats")
async def workflow_stats() -> dict:
    success_traces = [t for t in _TRACE_STORE if t["status"] == "success"]
    durations = []
    for t in _TRACE_STORE:
        d = t["duration"].replace("s", "")
        with contextlib.suppress(ValueError):
            durations.append(float(d))
    avg_duration = sum(durations) / len(durations) if durations else 0.0
    return {
        "total_executions": len(_TRACE_STORE),
        "success_rate": round(len(success_traces) / len(_TRACE_STORE) * 100, 1) if _TRACE_STORE else 0.0,
        "avg_duration_ms": round(avg_duration * 1000),
        "failed_count": len([t for t in _TRACE_STORE if t["status"] == "failed"]),
        "plans_used": sorted({t["plan"] for t in _TRACE_STORE}),
    }


@router.get("/workflows/traces")
async def workflow_traces(limit: int = 20, plan: str = "") -> dict:
    traces = _TRACE_STORE
    if plan:
        traces = [t for t in traces if t["plan"] == plan]
    return {"traces": list(reversed(traces[-limit:])), "total": len(traces)}


@router.get("/workflows/{workflow_id}/history")
async def workflow_history(workflow_id: str, limit: int = 20) -> dict:
    traces = [t for t in _TRACE_STORE if t["plan"].startswith(workflow_id)]
    if not traces:
        raise HTTPException(status_code=404, detail=f"No history found for workflow '{workflow_id}'")
    return {"workflow_id": workflow_id, "traces": list(reversed(traces[-limit:])), "total": len(traces)}


@router.post("/workflows/custom", status_code=201)
async def create_custom_workflow(body: dict) -> dict:
    """Create a custom workflow template.

    Request body:
        {
            "id": "my_workflow",
            "name": "My Custom Workflow",
            "description": "...",
            "node_order": ["node_a", "node_b", "node_c"],
            "category": "custom"
        }
    """
    workflow_id = body.get("id", "").strip()
    if not workflow_id:
        raise HTTPException(status_code=400, detail="Workflow 'id' is required")
    if any(p["id"] == workflow_id for p in _WORKFLOW_PLANS):
        raise HTTPException(status_code=409, detail=f"Workflow '{workflow_id}' already exists")

    nodes = body.get("node_order", [])
    new_plan = {
        "id": workflow_id,
        "name": body.get("name", workflow_id),
        "description": body.get("description", "Custom workflow"),
        "node_count": len(nodes),
        "nodes": [{"name": n, "label": n, "icon": "\u2699\ufe0f"} for n in nodes],
        "category": body.get("category", "custom"),
    }
    _WORKFLOW_PLANS.append(new_plan)
    return {"status": "created", "workflow": new_plan}


@router.post("/workflows/simulate")
async def simulate_routing(body: dict) -> dict:
    """Simulate workflow routing for a given NL query.

    Request body: {"nl_text": "...", "domain": "..."}
    """
    nl_text = body.get("nl_text", "")
    domain = body.get("domain", "default")
    # Simple heuristic routing simulation
    nl_lower = nl_text.lower()
    if any(kw in nl_lower for kw in ["trend", "trending", "over time", "monthly", "weekly"]):
        selected = "reflection"
    elif any(kw in nl_lower for kw in ["metric", "kpi", "gmv", "arpu"]):
        selected = "metric_query"
    elif len(nl_text.split()) < 5:
        selected = "ez_query"
    elif "?" in nl_lower or any(kw in nl_lower for kw in ["what about", "and also", "previous"]):
        selected = "chat_agentic"
    else:
        selected = "gensql_agentic"

    plan = next((p for p in _WORKFLOW_PLANS if p["id"] == selected), _WORKFLOW_PLANS[0])
    return {
        "status": "ok",
        "nl_text": nl_text,
        "domain": domain,
        "selected_workflow": selected,
        "workflow_name": plan["name"],
        "estimated_cost": "medium",
        "estimated_nodes": plan["node_count"],
        "reason": "Matched based on query complexity and keywords",
    }
