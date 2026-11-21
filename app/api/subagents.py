"""Operator-only scoped REST queries executed by the fixed YAML workflow.

The experimental orchestrator is not an external execution backend.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.auth.access import require_operator
from app.config.settings import settings
from app.harness.mysql_coordination import CoordinationUnavailableError
from app.harness.shared_query_runtime import current_shared_runtime
from app.models.subagent import SubagentConfig
from app.subagent.instance import SubagentInstance
from app.subagent.manager import SubagentManager
from app.workflow.contracts import classify_query_failure

router = APIRouter()


class SubagentQueryRequest(BaseModel):
    """Request body for POST /api/v1/subagents/{name}/query."""

    nl_text: str = Field(
        ..., description="Natural language query text", min_length=1, max_length=10000
    )
    domain: str = Field(default="", description="Must match the configured subagent domain")
    database: str = Field(default="", description="Database identifier")
    context: dict[str, Any] | None = Field(
        default=None, description="Only empty context is supported"
    )
    execute: bool = False
    num_candidates: int = Field(default=1, ge=1, le=5)
    metrics: list[str] = Field(default_factory=list, max_length=8)
    metric_catalog_version: str = Field(default="", max_length=128)
    metric_dimensions: list[str] = Field(default_factory=list, max_length=32)
    metric_filters: dict[str, str] = Field(default_factory=dict, max_length=32)
    metric_grain: Literal["", "day", "week", "month"] = ""


def _visible(config: SubagentConfig, user: dict[str, Any]) -> bool:
    allowed = config.access_control.allowed_users
    return "*" in allowed or str(user["user_id"]) in allowed


async def _registry() -> SubagentManager | None:
    from app.subagent.manager import subagent_manager

    if not settings.shared_worker_release:
        return subagent_manager
    runtime = current_shared_runtime()
    try:
        if runtime is None or not runtime.ready or runtime.verify_configuration is None:
            raise CoordinationUnavailableError("Immutable subagent runtime is unavailable")
        await runtime.verify_configuration()
    except CoordinationUnavailableError as exc:
        failure = classify_query_failure(exc)
        raise HTTPException(status_code=failure.http_status, detail=failure.payload()) from None
    return runtime.subagents


async def _instance(name: str, user: dict[str, Any]) -> SubagentInstance:
    registry = await _registry()
    if registry is None:
        raise HTTPException(status_code=409, detail="Worker release has no sealed subagent catalog")
    inst = registry.get(name)
    if inst is None:
        raise HTTPException(status_code=404, detail="Subagent not found")
    if not _visible(inst.config, user):
        raise HTTPException(status_code=403, detail="Subagent access denied")
    return inst


@router.post("/subagents/{name}/query")
async def query_subagent(
    name: str, body: SubagentQueryRequest,
    user: Annotated[dict[str, Any], Depends(require_operator)],
) -> dict[str, Any]:
    """Execute a natural language query through a specific subagent.

    Routes the NL query to the named subagent, which applies its scoped
    domain context and capabilities.

    Args:
        name: Subagent name as registered in the manager.
    """
    from app.api.query import submit_query
    from app.subagent.instance import SubagentStatus
    from app.subagent.workflow import scoped_query

    inst = await _instance(name, user)
    if inst.status != SubagentStatus.ACTIVE:
        raise HTTPException(status_code=409, detail="Subagent is not active")
    try:
        request = scoped_query(
            inst.config,
            nl_text=body.nl_text,
            domain=body.domain,
            database=body.database,
            execute=body.execute,
            num_candidates=body.num_candidates,
            context=body.context,
            metrics=body.metrics, metric_catalog_version=body.metric_catalog_version,
            metric_dimensions=body.metric_dimensions, metric_filters=body.metric_filters,
            metric_grain=body.metric_grain,
        )
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e)) from None
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from None
    except (LookupError, NotImplementedError) as e:
        raise HTTPException(status_code=409, detail=str(e)) from None
    # Reuse HTTP deadline, admission, model budget, permissions, audit and trace.
    result = await submit_query(request)
    data = result.model_dump()
    return {
        "status": result.status, "data": data, "errors": result.errors,
        "trace_id": data["workflow_ids"][0] if data["workflow_ids"] else "",
        "execution_backend": "workflow_runner",
    }


@router.get("/subagents")
async def list_subagents(
    user: Annotated[dict[str, Any], Depends(require_operator)],
) -> dict[str, Any]:
    """List all registered subagents and their status."""
    registry = await _registry()
    details = [
        {
            **item,
            "label": inst.config.label_zh,
            "database_ids": sorted({
                row["db_id"] for row in inst.config.context.databases
                if isinstance(row.get("db_id"), str) and row["db_id"]
            }),
            "enable_execution": inst.config.context.enable_execution,
        }
        for item in registry.list_details()
        if (inst := registry.get(item["name"])) is not None
        and _visible(inst.config, user)
    ] if registry is not None else []
    return {
        "status": "ok",
        "subagents": details,
        "total": len(details),
    }


@router.get("/subagents/{name}")
async def get_subagent(
    name: str, user: Annotated[dict[str, Any], Depends(require_operator)],
) -> dict[str, Any]:
    """Get details for a specific subagent."""
    inst = await _instance(name, user)
    return {
        "status": "ok",
        "subagent": {
            "name": inst.config.name,
            "domain": inst.config.domain,
            "label_zh": inst.config.label_zh,
            "label_en": inst.config.label_en,
            "description": inst.config.description,
            "status": inst.status.value,
            "capabilities": inst.config.capabilities,
            "delivery": [
                {"type": d.type, "path": d.path or d.endpoint} for d in inst.config.delivery
            ],
        },
    }
