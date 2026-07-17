"""MCP protocol models — MCPServerConfig, MCPToolDef, MCPResourceDef.

See SPEC §3.3 (MCP Integration) for the full specification.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal


@dataclass
class MCPToolDef:
    """Definition of an MCP tool (exposed by a server)."""

    name: str
    description: str
    input_schema: dict[str, Any] = field(default_factory=dict)
    server_id: str = ""


@dataclass
class MCPResourceDef:
    """Definition of an MCP resource (exposed by a server)."""

    uri: str
    description: str
    content_type: str = "application/json"
    server_id: str = ""


@dataclass
class MCPServerConfig:
    """Configuration for an MCP server instance."""

    name: str
    server_type: Literal["database", "knowledge", "vector", "learning", "external"]
    transport: Literal["stdio", "sse"] = "sse"
    host: str = "localhost"
    port: int = 8081
    tools: list[MCPToolDef] = field(default_factory=list)
    resources: list[MCPResourceDef] = field(default_factory=list)
    status: Literal["active", "inactive", "error"] = "active"
    registered_at: datetime = field(default_factory=datetime.now)
    last_health_check: datetime | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
