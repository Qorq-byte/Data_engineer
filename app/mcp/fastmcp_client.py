"""MCP Client — connect to external MCP servers and consume their tools/resources.

See SPEC §3.3.8 (MCP dual architecture) for the full specification.

This module allows the NL2SQL system to act as an MCP **Client**, consuming
tools and resources from external MCP servers (database servers, knowledge
bases, vector stores, etc.).

Key classes:
  - ``MCPClientConnection`` — a single connection to an external MCP server.
  - ``MCPClientManager`` — manages multiple connections.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.mcp.transport import MCPTransportConfig, create_transport
from app.models.mcp import MCPResourceDef, MCPServerConfig, MCPToolDef

# ── Client connection ───────────────────────────────────────────────────────


@dataclass
class MCPClientConnection:
    """An active (or activatable) connection to an external MCP server.

    Wraps an ``mcp.client.session.ClientSession`` and provides convenience
    methods for tool calling and resource reading.
    """

    config: MCPServerConfig
    transport_config: MCPTransportConfig = field(default_factory=MCPTransportConfig)
    _session: Any = None
    _connected: bool = False

    async def connect(self) -> None:
        """Establish the MCP session with the external server."""
        if self._connected:
            return

        from mcp.client.session import ClientSession

        try:
            transport = create_transport(self.transport_config)
            async with transport as (read_stream, write_stream):
                self._session = ClientSession(read_stream, write_stream)
                await self._session.initialize()
                self._connected = True
        except Exception as exc:
            self._connected = False
            raise ConnectionError(
                f"Failed to connect to MCP server '{self.config.name}': {exc}"
            ) from exc

    async def disconnect(self) -> None:
        """Close the MCP session."""
        self._connected = False
        self._session = None

    async def list_tools(self) -> list[MCPToolDef]:
        """List all tools exposed by the remote server."""
        if not self._session:
            await self.connect()
        if not self._session:
            return []

        result = await self._session.list_tools()
        return [
            MCPToolDef(
                name=t.name,
                description=t.description or "",
                input_schema=t.inputSchema if hasattr(t, "inputSchema") else {},
                server_id=self.config.name,
            )
            for t in result.tools
        ]

    async def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> Any:
        """Call a tool on the remote server and return the result."""
        if not self._session:
            await self.connect()
        if not self._session:
            raise RuntimeError(f"Not connected to server '{self.config.name}'")

        result = await self._session.call_tool(name, arguments or {})
        return result

    async def list_resources(self) -> list[MCPResourceDef]:
        """List all resources exposed by the remote server."""
        if not self._session:
            await self.connect()
        if not self._session:
            return []

        result = await self._session.list_resources()
        return [
            MCPResourceDef(
                uri=r.uri if hasattr(r, "uri") else str(r),
                description=r.description or "",
                server_id=self.config.name,
            )
            for r in result.resources
        ]

    async def read_resource(self, uri: str) -> Any:
        """Read a resource from the remote server."""
        if not self._session:
            await self.connect()
        if not self._session:
            raise RuntimeError(f"Not connected to server '{self.config.name}'")

        from pydantic import AnyUrl

        result = await self._session.read_resource(AnyUrl(uri))
        return result

    async def get_prompt(self, name: str, arguments: dict[str, str] | None = None) -> Any:
        """Get a prompt template from the remote server."""
        if not self._session:
            await self.connect()
        if not self._session:
            raise RuntimeError(f"Not connected to server '{self.config.name}'")

        result = await self._session.get_prompt(name, arguments or {})
        return result

    @property
    def is_connected(self) -> bool:
        return self._connected and self._session is not None


# ── Client manager ──────────────────────────────────────────────────────────


class MCPClientManager:
    """Manages multiple MCP client connections.

    Connections are created lazily — they aren't established until
    ``connect()`` is called on the manager or the individual connection.

    Usage::

        manager = MCPClientManager()
        manager.register_server(MCPTransportConfig(
            transport="sse",
            url="http://localhost:8081/sse",
        ))
        await manager.connect("server-name")
        tools = await manager.list_tools("server-name")
        result = await manager.call_tool("server-name", "my_tool", {"arg": "val"})
    """

    _connections: dict[str, MCPClientConnection]

    def __init__(self) -> None:
        self._connections = {}

    def register_server(
        self,
        server_config: MCPServerConfig,
        transport_config: MCPTransportConfig | None = None,
    ) -> None:
        """Register an external MCP server for later connection.

        Args:
            server_config: The MCP server configuration (from registry or manual).
            transport_config: Transport settings. Built from server_config if None.
        """
        if transport_config is None:
            transport_config = MCPTransportConfig(
                transport=server_config.transport,
                url=f"http://{server_config.host}:{server_config.port}/sse"
                if server_config.transport == "sse"
                else "",
            )

        self._connections[server_config.name] = MCPClientConnection(
            config=server_config,
            transport_config=transport_config,
        )

    def unregister_server(self, name: str) -> None:
        """Remove a registered server."""
        self._connections.pop(name, None)

    async def connect(self, name: str) -> MCPClientConnection:
        """Connect to a registered MCP server.

        Raises:
            KeyError: If the server is not registered.
            ConnectionError: If the connection fails.
        """
        conn = self._connections.get(name)
        if conn is None:
            raise KeyError(
                f"MCP client '{name}' not registered. "
                f"Available: {list(self._connections.keys())}"
            )
        await conn.connect()
        return conn

    async def disconnect(self, name: str) -> None:
        """Disconnect from a registered MCP server."""
        conn = self._connections.get(name)
        if conn:
            await conn.disconnect()

    async def disconnect_all(self) -> None:
        """Disconnect from all registered MCP servers."""
        for conn in self._connections.values():
            await conn.disconnect()

    async def list_tools(self, name: str) -> list[MCPToolDef]:
        """List tools from a connected MCP server."""
        conn = await self.connect(name)
        return await conn.list_tools()

    async def call_tool(
        self,
        server_name: str,
        tool_name: str,
        arguments: dict[str, Any] | None = None,
    ) -> Any:
        """Call a tool on a connected MCP server."""
        conn = await self.connect(server_name)
        return await conn.call_tool(tool_name, arguments)

    async def list_resources(self, name: str) -> list[MCPResourceDef]:
        """List resources from a connected MCP server."""
        conn = await self.connect(name)
        return await conn.list_resources()

    async def read_resource(self, server_name: str, uri: str) -> Any:
        """Read a resource from a connected MCP server."""
        conn = await self.connect(server_name)
        return await conn.read_resource(uri)

    async def get_prompt(
        self,
        server_name: str,
        prompt_name: str,
        arguments: dict[str, str] | None = None,
    ) -> Any:
        """Get a prompt from a connected MCP server."""
        conn = await self.connect(server_name)
        return await conn.get_prompt(prompt_name, arguments)

    def list_registered(self) -> list[str]:
        """Return names of all registered servers."""
        return list(self._connections.keys())

    def get_connection(self, name: str) -> MCPClientConnection | None:
        """Get a connection (may not be active)."""
        return self._connections.get(name)

    def reset(self) -> None:
        """Clear all registered connections. Useful for testing."""
        self._connections.clear()


# ── Module-level singleton ──────────────────────────────────────────────────

mcp_client_manager = MCPClientManager()
