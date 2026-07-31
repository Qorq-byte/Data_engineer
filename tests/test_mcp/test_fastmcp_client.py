"""Tests for MCP Client — connection manager and client connection."""

from __future__ import annotations

import pytest

from app.mcp.fastmcp_client import MCPClientConnection, MCPClientManager
from app.mcp.transport import MCPTransportConfig
from app.models.mcp import MCPServerConfig

# ── Test fixtures ───────────────────────────────────────────────────────


@pytest.fixture
def server_config():
    return MCPServerConfig(
        name="Test External DB",
        server_type="external",
        transport="sse",
        host="127.0.0.1",
        port=9999,
    )


@pytest.fixture
def client_manager():
    manager = MCPClientManager()
    yield manager
    manager.reset()


# ═══════════════════════════════════════════════════════════════════════════════
# MCPClientConnection
# ═══════════════════════════════════════════════════════════════════════════════


class TestMCPClientConnection:
    def test_creates_with_config(self, server_config):
        conn = MCPClientConnection(config=server_config)
        assert conn.config.name == "Test External DB"
        assert conn.is_connected is False

    def test_creates_with_transport_config(self, server_config):
        transport = MCPTransportConfig(
            transport="sse",
            url="http://127.0.0.1:9999/sse",
        )
        conn = MCPClientConnection(
            config=server_config,
            transport_config=transport,
        )
        assert conn.transport_config.url == "http://127.0.0.1:9999/sse"

    def test_is_connected_defaults_false(self, server_config):
        conn = MCPClientConnection(config=server_config)
        assert conn.is_connected is False

    def test_disconnect_not_connected_no_error(self, server_config):
        conn = MCPClientConnection(config=server_config)
        # Should not raise even when not connected
        import asyncio

        asyncio.run(conn.disconnect())
        assert conn.is_connected is False


# ═══════════════════════════════════════════════════════════════════════════════
# MCPClientManager — registration
# ═══════════════════════════════════════════════════════════════════════════════


class TestMCPClientManagerRegistration:
    def test_register_server(self, client_manager, server_config):
        client_manager.register_server(server_config)
        assert "Test External DB" in client_manager.list_registered()

    def test_register_multiple_servers(self, client_manager):
        s1 = MCPServerConfig(
            name="Server A",
            server_type="external",
            transport="sse",
            host="0.0.0.0",
            port=8001,
        )
        s2 = MCPServerConfig(
            name="Server B",
            server_type="external",
            transport="stdio",
            host="0.0.0.0",
            port=8002,
        )
        client_manager.register_server(s1)
        client_manager.register_server(s2)
        assert len(client_manager.list_registered()) == 2
        assert "Server A" in client_manager.list_registered()
        assert "Server B" in client_manager.list_registered()

    def test_unregister_server(self, client_manager, server_config):
        client_manager.register_server(server_config)
        assert "Test External DB" in client_manager.list_registered()
        client_manager.unregister_server("Test External DB")
        assert "Test External DB" not in client_manager.list_registered()

    def test_unregister_nonexistent_no_error(self, client_manager):
        client_manager.unregister_server("nonexistent")
        # Should not raise

    def test_register_overwrites_same_name(self, client_manager):
        s1 = MCPServerConfig(
            name="Same Name",
            server_type="external",
            transport="sse",
            host="0.0.0.0",
            port=9001,
        )
        s2 = MCPServerConfig(
            name="Same Name",
            server_type="external",
            transport="stdio",
            host="0.0.0.0",
            port=9002,
        )
        client_manager.register_server(s1)
        client_manager.register_server(s2)
        # Latest wins
        conn = client_manager.get_connection("Same Name")
        assert conn is not None
        assert conn.config.port == 9002

    def test_register_with_custom_transport(self, client_manager, server_config):
        transport = MCPTransportConfig(
            transport="stdio",
            command="my_server",
        )
        client_manager.register_server(server_config, transport_config=transport)
        conn = client_manager.get_connection("Test External DB")
        assert conn is not None
        assert conn.transport_config.transport == "stdio"
        assert conn.transport_config.command == "my_server"


# ═══════════════════════════════════════════════════════════════════════════════
# MCPClientManager — connect/disconnect
# ═══════════════════════════════════════════════════════════════════════════════


class TestMCPClientManagerConnect:
    def test_connect_unregistered_raises(self, client_manager):
        with pytest.raises(KeyError, match="not registered"):
            import asyncio

            asyncio.run(client_manager.connect("nonexistent"))

    def test_disconnect_unregistered_no_error(self, client_manager):
        import asyncio

        asyncio.run(client_manager.disconnect("nonexistent"))
        # Should not raise

    def test_disconnect_all(self, client_manager, server_config):
        s2 = MCPServerConfig(
            name="Server 2",
            server_type="external",
            transport="sse",
            host="0.0.0.0",
            port=9002,
        )
        client_manager.register_server(server_config)
        client_manager.register_server(s2)
        import asyncio

        asyncio.run(client_manager.disconnect_all())
        # Should not raise — all disconnected gracefully


# ═══════════════════════════════════════════════════════════════════════════════
# MCPClientManager — get_connection
# ═══════════════════════════════════════════════════════════════════════════════


class TestMCPClientManagerGetConnection:
    def test_get_existing(self, client_manager, server_config):
        client_manager.register_server(server_config)
        conn = client_manager.get_connection("Test External DB")
        assert conn is not None
        assert isinstance(conn, MCPClientConnection)

    def test_get_nonexistent(self, client_manager):
        conn = client_manager.get_connection("nonexistent")
        assert conn is None

    def test_connection_has_config(self, client_manager, server_config):
        client_manager.register_server(server_config)
        conn = client_manager.get_connection("Test External DB")
        assert conn is not None
        assert conn.config.name == "Test External DB"
        assert conn.config.port == 9999


# ═══════════════════════════════════════════════════════════════════════════════
# MCPClientManager — reset
# ═══════════════════════════════════════════════════════════════════════════════


class TestMCPClientManagerReset:
    def test_reset_clears_all(self, client_manager, server_config):
        client_manager.register_server(server_config)
        assert len(client_manager.list_registered()) == 1
        client_manager.reset()
        assert len(client_manager.list_registered()) == 0

    def test_reset_on_empty_no_error(self, client_manager):
        client_manager.reset()
        assert len(client_manager.list_registered()) == 0


# ═══════════════════════════════════════════════════════════════════════════════
# MCPClientManager — singleton
# ═══════════════════════════════════════════════════════════════════════════════


class TestMCPClientManagerSingleton:
    def test_module_level_singleton_exists(self):
        from app.mcp.fastmcp_client import mcp_client_manager

        assert isinstance(mcp_client_manager, MCPClientManager)

    def test_singleton_is_shared(self):
        from app.mcp.fastmcp_client import mcp_client_manager

        mgr1 = mcp_client_manager
        from app.mcp.fastmcp_client import mcp_client_manager as mgr2

        assert mgr1 is mgr2
