"""Tests for MCP transport layer — configs and factory functions."""

from __future__ import annotations

import pytest

from app.mcp.transport import MCPTransportConfig, create_transport

# ═══════════════════════════════════════════════════════════════════════════════
# MCPTransportConfig
# ═══════════════════════════════════════════════════════════════════════════════


class TestMCPTransportConfig:
    def test_defaults(self):
        config = MCPTransportConfig()
        assert config.transport == "sse"
        assert config.url == ""
        assert config.timeout == 5.0
        assert config.sse_read_timeout == 300.0

    def test_sse_config(self):
        config = MCPTransportConfig(
            transport="sse",
            url="http://localhost:8081/sse",
            timeout=10.0,
        )
        assert config.transport == "sse"
        assert config.url == "http://localhost:8081/sse"
        assert config.timeout == 10.0

    def test_stdio_config(self):
        config = MCPTransportConfig(
            transport="stdio",
            command="python",
            args=["-m", "my_server"],
            env={"DEBUG": "1"},
        )
        assert config.transport == "stdio"
        assert config.command == "python"
        assert config.args == ["-m", "my_server"]
        assert config.env == {"DEBUG": "1"}

    def test_headers_optional(self):
        config = MCPTransportConfig()
        assert config.headers is None

    def test_headers_custom(self):
        config = MCPTransportConfig(
            headers={"Authorization": "Bearer token123"}
        )
        assert config.headers == {"Authorization": "Bearer token123"}


# ═══════════════════════════════════════════════════════════════════════════════
# create_transport factory
# ═══════════════════════════════════════════════════════════════════════════════


class TestCreateTransport:
    def test_sse_returns_context_manager(self):
        config = MCPTransportConfig(
            transport="sse",
            url="http://localhost:9999/sse",
        )
        transport = create_transport(config)
        assert transport is not None
        # Should be an async context manager
        assert hasattr(transport, "__aenter__")
        assert hasattr(transport, "__aexit__")

    def test_stdio_returns_context_manager(self):
        config = MCPTransportConfig(
            transport="stdio",
            command="python",
            args=["-c", "print('hello')"],
        )
        transport = create_transport(config)
        assert transport is not None
        assert hasattr(transport, "__aenter__")
        assert hasattr(transport, "__aexit__")

    def test_invalid_transport_raises(self):
        config = MCPTransportConfig(transport="invalid")
        with pytest.raises(ValueError, match="Unsupported transport type"):
            create_transport(config)

    def test_unknown_transport_message(self):
        config = MCPTransportConfig(transport="grpc")
        with pytest.raises(ValueError, match="grpc"):
            create_transport(config)
