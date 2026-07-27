"""Subagent API endpoints — query subagents via REST API.

See SPEC §4.10.5 (delivery channels) for the full specification.
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

router = APIRouter()


class SubagentQueryRequest(BaseModel):
    """Request body for POST /api/v1/subagents/{name}/query."""
    nl_text: str = Field(..., description="Natural language query text", min_length=1)
    domain: str = Field(default="", description="Domain override")
    database: str = Field(default="", description="Database identifier")
    context: dict | None = Field(default=None, description="Additional context")


@router.post("/subagents/{name}/query")
async def query_subagent(name: str, body: SubagentQueryRequest) -> dict:
    """Execute a natural language query through a specific subagent.

    Routes the NL query to the named subagent, which applies its scoped
    domain context and capabilities.

    Args:
        name: Subagent name as registered in the manager.
    """
    from app.subagent.router import subagent_router

    try:
        result = await subagent_router.route_api(
            name=name,
            nl_text=body.nl_text,
            context=body.context or {},
        )
        return result
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e)) from None
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e)) from None
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Subagent query failed: {e}",
        ) from e


@router.get("/subagents")
async def list_subagents() -> dict:
    """List all registered subagents and their status."""
    from app.subagent.manager import subagent_manager

    details = subagent_manager.list_details()
    return {
        "status": "ok",
        "subagents": details,
        "total": len(details),
    }


@router.get("/subagents/{name}")
async def get_subagent(name: str) -> dict:
    """Get details for a specific subagent."""
    from app.subagent.manager import subagent_manager

    inst = subagent_manager.get(name)
    if inst is None:
        raise HTTPException(
            status_code=404,
            detail=f"Subagent '{name}' not found. Available: {subagent_manager.list_all()}",
        )
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
                {"type": d.type, "path": d.path or d.endpoint}
                for d in inst.config.delivery
            ],
        },
    }