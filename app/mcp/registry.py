"""MCP Server registry — tracks all registered MCP servers.

See SPEC §3.3 (MCP Integration) for the full specification.

The registry maintains the list of MCP servers (both internal and external)
that the system can connect to. Each entry includes tools and resources.
"""

from datetime import datetime

from app.models.mcp import MCPResourceDef, MCPServerConfig, MCPToolDef


class MCPServerRegistry:
    """Registry of all MCP server instances."""

    _servers: dict[str, MCPServerConfig] = {}

    @classmethod
    def register(cls, config: MCPServerConfig) -> None:
        cls._servers[config.name] = config

    @classmethod
    def unregister(cls, name: str) -> MCPServerConfig | None:
        return cls._servers.pop(name, None)

    @classmethod
    def get(cls, name: str) -> MCPServerConfig:
        server = cls._servers.get(name)
        if server is None:
            raise KeyError(
                f"MCP Server '{name}' not found. "
                f"Available: {cls.list_names()}"
            )
        return server

    @classmethod
    def list_all(cls) -> list[MCPServerConfig]:
        return list(cls._servers.values())

    @classmethod
    def list_names(cls) -> list[str]:
        return sorted(cls._servers.keys())

    @classmethod
    def list_active(cls) -> list[MCPServerConfig]:
        return [s for s in cls._servers.values() if s.status == "active"]

    @classmethod
    def get_server_tools(cls, name: str) -> list[MCPToolDef]:
        return cls.get(name).tools

    @classmethod
    def get_server_resources(cls, name: str) -> list[MCPResourceDef]:
        return cls.get(name).resources

    @classmethod
    def update_health(cls, name: str, status: str) -> None:
        server = cls.get(name)
        server.status = status  # type: ignore
        server.last_health_check = datetime.now()

    @classmethod
    def reset(cls) -> None:
        """Clear all registered servers. Useful for testing."""
        cls._servers.clear()


# Global singleton
mcp_registry = MCPServerRegistry()
