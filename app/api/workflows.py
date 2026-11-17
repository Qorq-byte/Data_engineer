"""Workflow management API endpoints.

See SPEC 6.5 (Workflow Orchestration API) for the full specification.
"""

from __future__ import annotations

import contextlib
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException

router = APIRouter()


def _load_plan_catalog() -> list[dict[str, Any]]:
    from app.harness.plan_loader import PlanLoader
    from app.workflow.plan_runtime import plan_blocker

    loader = PlanLoader(str(Path(__file__).resolve().parents[1] / "workflow/plans"))
    plans = []
    for plan_id in loader.list_available():
        plan = loader.load(plan_id)
        blocker = plan_blocker(plan_id)
        plans.append(
            {
                "id": plan.id,
                "name": plan.name,
                "description": plan.description,
                "node_count": len(plan.node_order),
                "nodes": [
                    {"name": node.node, "label": node.node, "icon": "⚙"}
                    for node in plan.node_order
                ],
                "category": "active" if not blocker else "template",
                "query_executable": not blocker,
                "blocker": blocker,
            }
        )
    return plans


_WORKFLOW_PLANS = _load_plan_catalog()

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
    _TRACE_STORE.append(
        {
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
        }
    )
    # Keep only last 100 traces
    if len(_TRACE_STORE) > 100:
        _TRACE_STORE[:] = _TRACE_STORE[-100:]


@router.get("/workflows")
async def list_workflows() -> dict[str, Any]:
    from app.workflow.plan_runtime import plan_blocker

    plans = []
    for item in _WORKFLOW_PLANS:
        if item["id"] == "metric_query":
            blocker = plan_blocker("metric_query")
            item = {**item, "query_executable": not blocker, "blocker": blocker,
                    "category": "template" if blocker else "active"}
        plans.append(item)
    return {
        "plans": plans,
        "total": len(plans),
        "categories": sorted({p["category"] for p in plans}),
    }


@router.get("/workflows/stats")
async def workflow_stats() -> dict[str, Any]:
    success_traces = [t for t in _TRACE_STORE if t["status"] == "success"]
    durations = []
    for t in _TRACE_STORE:
        d = t["duration"].replace("s", "")
        with contextlib.suppress(ValueError):
            durations.append(float(d))
    avg_duration = sum(durations) / len(durations) if durations else 0.0
    return {
        "total_executions": len(_TRACE_STORE),
        "success_rate": round(len(success_traces) / len(_TRACE_STORE) * 100, 1)
        if _TRACE_STORE
        else 0.0,
        "avg_duration_ms": round(avg_duration * 1000),
        "failed_count": len([t for t in _TRACE_STORE if t["status"] == "failed"]),
        "plans_used": sorted({t["plan"] for t in _TRACE_STORE}),
    }


@router.get("/workflows/traces")
async def workflow_traces(limit: int = 20, plan: str = "") -> dict[str, Any]:
    traces = _TRACE_STORE
    if plan:
        traces = [t for t in traces if t["plan"] == plan]
    return {"traces": list(reversed(traces[-limit:])), "total": len(traces)}


@router.get("/workflows/{workflow_id}/history")
async def workflow_history(workflow_id: str, limit: int = 20) -> dict[str, Any]:
    traces = [t for t in _TRACE_STORE if t["plan"].startswith(workflow_id)]
    if not traces:
        raise HTTPException(
            status_code=404, detail=f"No history found for workflow '{workflow_id}'"
        )
    return {
        "workflow_id": workflow_id,
        "traces": list(reversed(traces[-limit:])),
        "total": len(traces),
    }


@router.post("/workflows/custom", status_code=201)
async def create_custom_workflow(body: dict[str, Any]) -> dict[str, Any]:
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
        "query_executable": False,
        "persistence": "process-local catalog only",
    }
    _WORKFLOW_PLANS.append(new_plan)
    return {"status": "created", "workflow": new_plan}


@router.post("/workflows/simulate")
async def simulate_routing(body: dict[str, Any]) -> dict[str, Any]:
    """Explain which plan a query would use under automatic routing.

    Uses the same capability-filtered selector as the query path, so the
    answer can be compared with a real request instead of a separate guess.

    Request body: {"nl_text": "...", "domain": "..."}
    """
    from app.workflow.plan_selection import plan_catalog_state, select_plan

    nl_text = body.get("nl_text", "")
    domain = body.get("domain", "default")
    decision = await select_plan(nl_text, "" if domain in {"", "default"} else domain)
    executable, blocker = plan_catalog_state(decision.plan_id)
    plan = next(
        (item for item in _load_plan_catalog() if item["id"] == decision.plan_id),
        None,
    )
    return {
        "status": "simulation",
        "actual_query_plan": decision.plan_id if executable else "request_query",
        "nl_text": nl_text,
        "domain": domain,
        "selected_workflow": decision.plan_id,
        "workflow_name": plan["name"] if plan else decision.plan_id,
        "estimated_cost": "low" if decision.plan_id == "ez_query" else "medium",
        "estimated_nodes": plan["node_count"] if plan else 0,
        "reason": decision.reason,
        "layer": decision.layer,
        "confidence": decision.confidence,
        "query_executable": executable,
        "execution_note": blocker or "executable via request plan_id",
    }
