"""MCP transport layer — SSE and stdio transport factories.

See SPEC §3.3.8 (MCP dual architecture) for the full specification.

Provides factory functions for creating MCP transports, wrapping the
underlying ``mcp.client.sse`` / ``mcp.client.stdio`` modules.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any

# ── Transport configuration ────────────────────────────────────────────────


@dataclass
class MCPTransportConfig:
    """Configuration for an MCP transport connection.

    Attributes:
        transport: ``"sse"`` or ``"stdio"``.
        url: SSE endpoint URL (for ``sse`` transport).
        headers: Optional HTTP headers for SSE connections.
        timeout: Connection timeout in seconds.
        sse_read_timeout: SSE read timeout in seconds.
        command: Command to launch (for ``stdio`` transport).
        args: Arguments for the command.
        env: Environment variables for the command.
    """

    transport: str = "sse"
    url: str = ""
    headers: dict[str, str] | None = None
    timeout: float = 5.0
    sse_read_timeout: float = 300.0
    command: str = ""
    args: list[str] = field(default_factory=list)
    env: dict[str, str] | None = None


# ── SSE transport ──────────────────────────────────────────────────────────


@asynccontextmanager
async def create_sse_transport(
    url: str,
    headers: dict[str, str] | None = None,
    timeout: float = 5.0,
    sse_read_timeout: float = 300.0,
) -> AsyncIterator[tuple[Any, Any]]:
    """Create an SSE client transport for connecting to a remote MCP server.

    Usage::

        async with create_sse_transport("http://localhost:8081/sse") as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                tools = await session.list_tools()

    Args:
        url: The SSE endpoint URL.
        headers: Optional HTTP headers.
        timeout: Connection timeout in seconds.
        sse_read_timeout: SSE read timeout in seconds.

    Yields:
        (read_stream, write_stream) tuple for ClientSession.
    """
    from mcp.client.sse import sse_client

    async with sse_client(
        url=url,
        headers=headers,
        timeout=timeout,
        sse_read_timeout=sse_read_timeout,
    ) as streams:
        yield streams


# ── stdio transport ────────────────────────────────────────────────────────


@asynccontextmanager
async def create_stdio_transport(
    command: str,
    args: list[str] | None = None,
    env: dict[str, str] | None = None,
) -> AsyncIterator[tuple[Any, Any]]:
    """Create a stdio client transport for connecting to a local MCP server.

    Usage::

        async with create_stdio_transport("python", ["-m", "my_mcp_server"]) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                ...

    Args:
        command: Executable or command to launch.
        args: Command-line arguments.
        env: Environment variables.

    Yields:
        (read_stream, write_stream) tuple for ClientSession.
    """
    from mcp.client.stdio import StdioServerParameters, stdio_client

    params = StdioServerParameters(
        command=command,
        args=args or [],
        env=env,
    )
    async with stdio_client(params) as streams:
        yield streams


# ── Transport factory ──────────────────────────────────────────────────────


def create_transport(
    config: MCPTransportConfig,
) -> Any:
    """Create a transport context manager from config.

    Returns an async context manager yielding (read, write) streams.
    """
    if config.transport == "sse":
        return create_sse_transport(
            url=config.url,
            headers=config.headers,
            timeout=config.timeout,
            sse_read_timeout=config.sse_read_timeout,
        )
    elif config.transport == "stdio":
        return create_stdio_transport(
            command=config.command,
            args=config.args,
            env=config.env,
        )
    else:
        raise ValueError(
            f"Unsupported transport type: {config.transport}. "
            f"Must be 'sse' or 'stdio'."
        )
