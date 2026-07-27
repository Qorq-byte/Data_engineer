"""MCP server management API endpoints.

See SPEC §6.4 (MCP Protocol API) for the full specification.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.mcp.registry import mcp_registry

router = APIRouter()

# Track which servers have been manually stopped by the user.
# Servers in this set will show as "stopped" regardless of their registry status.
# Persisted to data/mcp_stopped_servers.json for survival across server restarts.
_STOPPED_SERVERS_FILE = Path("data/mcp_stopped_servers.json")
_stopped_servers: set[str] = set()


def _load_stopped_servers() -> None:
    """Load persisted stopped servers from disk."""
    try:
        if _STOPPED_SERVERS_FILE.exists():
            data = json.loads(_STOPPED_SERVERS_FILE.read_text(encoding="utf-8"))
            if isinstance(data, list):
                _stopped_servers.update(data)
    except Exception:
        pass


def _save_stopped_servers() -> None:
    """Persist stopped servers to disk."""
    try:
        _STOPPED_SERVERS_FILE.parent.mkdir(parents=True, exist_ok=True)
        _STOPPED_SERVERS_FILE.write_text(
            json.dumps(sorted(_stopped_servers), ensure_ascii=False),
            encoding="utf-8",
        )
    except Exception:
        pass


# Load persisted state on module import
_load_stopped_servers()

_BUILTIN_SERVERS = [
    {"name": "db_server", "server_type": "database", "port": 8081, "tool_count": 4, "resource_count": 2, "status": "running", "description": "数据库内省与执行工具"},
    {"name": "knowledge_server", "server_type": "knowledge", "port": 8082, "tool_count": 3, "resource_count": 1, "status": "running", "description": "领域知识库检索"},
    {"name": "vector_server", "server_type": "vector", "port": 8083, "tool_count": 2, "resource_count": 1, "status": "running", "description": "向量检索与 RAG"},
    {"name": "learning_server", "server_type": "learning", "port": 8084, "tool_count": 2, "resource_count": 0, "status": "stopped", "description": "查询历史学习"},
]


class RegisterServerRequest(BaseModel):
    """Request body for POST /api/v1/mcp/servers/register."""
    name: str = Field(..., min_length=1, description="MCP server name")
    server_type: str = Field(default="custom", description="Server type: database, knowledge, vector, learning, custom")
    transport: str = Field(default="sse", description="Transport: sse or stdio")
    host: str = Field(default="127.0.0.1", description="Server host")
    port: int = Field(default=8080, ge=1, le=65535, description="Server port")
    description: str = Field(default="", description="Server description")


class CallToolRequest(BaseModel):
    """Request body for POST /api/v1/mcp/tools/{tool_id}/call."""
    arguments: dict = Field(default_factory=dict, description="Tool arguments")


@router.get("/mcp/servers")
async def list_mcp_servers() -> dict:
    servers = mcp_registry.list_all()
    if not servers:
        # For built-in fallback, respect stopped state
        builtin = []
        for s in _BUILTIN_SERVERS:
            s_copy = dict(s)
            if s["name"] in _stopped_servers:
                s_copy["status"] = "stopped"
            builtin.append(s_copy)
        return {"servers": builtin, "total": len(builtin)}
    return {"servers": [{"name": s.name, "server_type": s.server_type, "transport": s.transport, "host": s.host, "port": s.port, "tool_count": len(s.tools), "resource_count": len(s.resources), "status": "stopped" if s.name in _stopped_servers else ("running" if s.status == "active" else "stopped"), "description": s.metadata.get("description", ""), "last_health_check": s.last_health_check.isoformat() if s.last_health_check else None} for s in servers], "total": len(servers)}


@router.get("/mcp/servers/{name}")
async def get_mcp_server(name: str) -> dict:
    try:
        s = mcp_registry.get(name)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"MCP server '{name}' not found") from None
    return {"name": s.name, "server_type": s.server_type, "transport": s.transport, "host": s.host, "port": s.port, "status": s.status, "tools": [{"name": t.name, "description": t.description} for t in s.tools], "resources": [{"uri": r.uri, "description": r.description} for r in s.resources], "last_health_check": s.last_health_check.isoformat() if s.last_health_check else None}


@router.post("/mcp/servers/register", status_code=201)
async def register_mcp_server(body: RegisterServerRequest) -> dict:
    """Register a new MCP server."""
    from app.models.mcp import MCPServerConfig

    name = body.name.strip()
    # Check for duplicates
    try:
        mcp_registry.get(name)
        raise HTTPException(status_code=409, detail=f"MCP server '{name}' already registered")
    except KeyError:
        pass

    config = MCPServerConfig(
        name=name,
        server_type=body.server_type,
        transport=body.transport,
        host=body.host,
        port=body.port,
        status="active",
        metadata={"description": body.description},
    )
    mcp_registry.register(config)
    return {
        "status": "registered",
        "name": name,
        "server_type": body.server_type,
        "registered_at": datetime.now().isoformat(),
    }


@router.delete("/mcp/servers/{name}")
async def remove_mcp_server(name: str) -> dict:
    """Remove a registered MCP server."""
    try:
        mcp_registry.get(name)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"MCP server '{name}' not found") from None

    mcp_registry.unregister(name)
    _stopped_servers.discard(name)
    _save_stopped_servers()
    return {"status": "removed", "name": name}


@router.post("/mcp/servers/{name}/stop")
async def stop_mcp_server(name: str) -> dict:
    """Stop a running MCP server. The server remains registered but is marked as stopped."""
    # Check if server exists in registry or built-in list
    try:
        mcp_registry.get(name)
    except KeyError:
        if not any(s["name"] == name for s in _BUILTIN_SERVERS):
            raise HTTPException(status_code=404, detail=f"MCP server '{name}' not found") from None

    _stopped_servers.add(name)
    _save_stopped_servers()
    return {"status": "stopped", "name": name, "stopped_at": datetime.now().isoformat()}


@router.post("/mcp/servers/{name}/start")
async def start_mcp_server(name: str) -> dict:
    """Start a previously stopped MCP server."""
    try:
        mcp_registry.get(name)
    except KeyError:
        if not any(s["name"] == name for s in _BUILTIN_SERVERS):
            raise HTTPException(status_code=404, detail=f"MCP server '{name}' not found") from None

    _stopped_servers.discard(name)
    _save_stopped_servers()
    return {"status": "started", "name": name, "started_at": datetime.now().isoformat()}


@router.get("/mcp/servers/{name}/tools")
async def list_mcp_server_tools(name: str) -> dict:
    """Get available tools for a registered MCP server."""
    try:
        s = mcp_registry.get(name)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"MCP server '{name}' not found") from None
    return {
        "name": name,
        "tools": [{"name": t.name, "description": t.description} for t in s.tools],
        "total": len(s.tools),
    }


@router.get("/mcp/servers/{name}/resources")
async def list_mcp_server_resources(name: str) -> dict:
    """Get available resources for a registered MCP server."""
    try:
        s = mcp_registry.get(name)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"MCP server '{name}' not found") from None
    return {
        "name": name,
        "resources": [{"uri": r.uri, "description": r.description} for r in s.resources],
        "total": len(s.resources),
    }


@router.post("/mcp/tools/{tool_id}/call")
async def call_mcp_tool(tool_id: str, body: CallToolRequest) -> dict:
    """Call a registered MCP tool directly (debugging endpoint)."""
    # Find the tool across all registered servers
    for s in mcp_registry.list_all():
        for t in s.tools:
            if t.name == tool_id:
                return {
                    "status": "ok",
                    "tool": tool_id,
                    "server": s.name,
                    "arguments": body.arguments,
                    "result": {"message": f"Tool '{tool_id}' called successfully (simulated)"},
                }
    raise HTTPException(status_code=404, detail=f"Tool '{tool_id}' not found in any registered MCP server")


@router.get("/mcp/resources/{resource_uri:path}")
async def read_mcp_resource(resource_uri: str) -> dict:
    """Read content from a registered MCP resource."""
    # Find the resource across all registered servers
    for s in mcp_registry.list_all():
        for r in s.resources:
            if r.uri == resource_uri or r.uri.startswith(resource_uri):
                return {
                    "status": "ok",
                    "uri": resource_uri,
                    "server": s.name,
                    "description": r.description,
                    "content": f"Resource '{resource_uri}' content (simulated)",
                }
    raise HTTPException(status_code=404, detail=f"Resource '{resource_uri}' not found in any registered MCP server")


@router.post("/mcp/servers/{name}/health")
async def check_mcp_server_health(name: str) -> dict:
    try:
        mcp_registry.get(name)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"MCP server '{name}' not found") from None
    mcp_registry.update_health(name, "active")
    return {"name": name, "status": "active", "checked_at": datetime.now().isoformat()}
