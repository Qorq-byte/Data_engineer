"""Tests for NL2SQL MCP Server — server creation, tools, resources, helpers."""

from __future__ import annotations

# ═══════════════════════════════════════════════════════════════════════════════
# Server creation
# ═══════════════════════════════════════════════════════════════════════════════


class TestCreateNL2SQLServer:
    def test_creates_fastmcp_instance(self):
        from app.mcp.fastmcp_server import create_nl2sql_server

        server = create_nl2sql_server(port=8090)
        from mcp.server.fastmcp import FastMCP

        assert isinstance(server, FastMCP)

    def test_default_name(self):
        from app.mcp.fastmcp_server import create_nl2sql_server

        server = create_nl2sql_server()
        assert server.name is not None
        assert "NL2SQL" in (server.name or "")

    def test_custom_name(self):
        from app.mcp.fastmcp_server import create_nl2sql_server

        server = create_nl2sql_server(name="My Custom Agent")
        assert server.name == "My Custom Agent"

    def test_custom_port(self):
        from app.mcp.fastmcp_server import create_nl2sql_server

        server = create_nl2sql_server(port=9999)
        assert server.settings.port == 9999  # type: ignore[attr-defined]

    def test_different_instance_each_call(self):
        from app.mcp.fastmcp_server import create_nl2sql_server

        s1 = create_nl2sql_server()
        s2 = create_nl2sql_server()
        assert s1 is not s2


# ═══════════════════════════════════════════════════════════════════════════════
# Tool presence
# ═══════════════════════════════════════════════════════════════════════════════


class TestToolRegistration:
    """Verify that all 5 expected tools are registered on the server."""

    EXPECTED_TOOLS = {
        "nl_query",
        "explain_sql",
        "translate_sql",
        "validate_sql",
        "search_schema",
    }

    def test_all_five_tools_registered(self):
        from app.mcp.fastmcp_server import create_nl2sql_server

        server = create_nl2sql_server()

        # FastMCP stores tools in _tool_manager
        tool_manager = server._tool_manager  # type: ignore[attr-defined]
        registered = set(tool_manager._tools.keys())  # type: ignore[attr-defined]

        for tool_name in self.EXPECTED_TOOLS:
            assert tool_name in registered, f"Tool '{tool_name}' not registered"

    def test_nl_query_has_description(self):
        from app.mcp.fastmcp_server import create_nl2sql_server

        server = create_nl2sql_server()
        tool_manager = server._tool_manager  # type: ignore[attr-defined]
        tool = tool_manager._tools["nl_query"]  # type: ignore[attr-defined]
        assert tool.description, "nl_query should have a description"


# ═══════════════════════════════════════════════════════════════════════════════
# Resource presence
# ═══════════════════════════════════════════════════════════════════════════════


class TestResourceRegistration:
    """Verify that all 4 expected resources are registered on the server."""

    EXPECTED_RESOURCES = {
        "nlsql://domains/",
        "nlsql://nodes/",
        "nlsql://workflows/",
    }

    EXPECTED_TEMPLATES = {
        "nlsql://schema/{db_id}",
    }

    def test_all_resources_registered(self):
        from app.mcp.fastmcp_server import create_nl2sql_server

        server = create_nl2sql_server()

        resource_manager = server._resource_manager  # type: ignore[attr-defined]
        registered = set(resource_manager._resources.keys())  # type: ignore[attr-defined]

        for resource_uri in self.EXPECTED_RESOURCES:
            assert resource_uri in registered, (
                f"Resource '{resource_uri}' not registered"
            )

    def test_all_templates_registered(self):
        from app.mcp.fastmcp_server import create_nl2sql_server

        server = create_nl2sql_server()

        resource_manager = server._resource_manager  # type: ignore[attr-defined]
        registered_templates = set(resource_manager._templates.keys())  # type: ignore[attr-defined]

        for template_uri in self.EXPECTED_TEMPLATES:
            assert template_uri in registered_templates, (
                f"Template '{template_uri}' not registered"
            )


# ═══════════════════════════════════════════════════════════════════════════════
# Helper functions
# ═══════════════════════════════════════════════════════════════════════════════


class TestExtractSQLFromResponse:
    def test_str_response(self):
        from app.mcp.fastmcp_server import _extract_sql_from_response

        result = _extract_sql_from_response("SELECT * FROM users")
        assert result == "SELECT * FROM users"

    def test_dict_with_content(self):
        from app.mcp.fastmcp_server import _extract_sql_from_response

        result = _extract_sql_from_response({"content": "SELECT 1"})
        assert result == "SELECT 1"

    def test_dict_with_text(self):
        from app.mcp.fastmcp_server import _extract_sql_from_response

        result = _extract_sql_from_response({"text": "SELECT 1"})
        assert result == "SELECT 1"

    def test_dict_empty(self):
        from app.mcp.fastmcp_server import _extract_sql_from_response

        result = _extract_sql_from_response({})
        assert result == ""

    def test_markdown_fence(self):
        from app.mcp.fastmcp_server import _extract_sql_from_response

        result = _extract_sql_from_response("```sql\nSELECT * FROM users\n```")
        assert "SELECT * FROM users" in result
        assert "```" not in result

    def test_markdown_fence_no_lang(self):
        from app.mcp.fastmcp_server import _extract_sql_from_response

        result = _extract_sql_from_response("```\nSELECT * FROM users\n```")
        assert "SELECT * FROM users" in result
        assert "```" not in result

    def test_object_with_content(self):
        from app.mcp.fastmcp_server import _extract_sql_from_response

        class FakeResponse:
            content = "SELECT 1"
            text = "SELECT 2"

        result = _extract_sql_from_response(FakeResponse())
        assert result == "SELECT 1"


class TestExtractTableNames:
    def test_from_clause(self):
        from app.mcp.fastmcp_server import _extract_table_names

        tables = _extract_table_names("select * from users")
        assert "users" in tables

    def test_join_clause(self):
        from app.mcp.fastmcp_server import _extract_table_names

        tables = _extract_table_names(
            "select * from users join orders on users.id = orders.user_id"
        )
        assert "users" in tables
        assert "orders" in tables

    def test_insert_into(self):
        from app.mcp.fastmcp_server import _extract_table_names

        tables = _extract_table_names(
            "insert into products (name, price) values ('x', 1)"
        )
        assert "products" in tables

    def test_update(self):
        from app.mcp.fastmcp_server import _extract_table_names

        tables = _extract_table_names(
            "update orders set status = 'done'"
        )
        assert "orders" in tables

    def test_multiple_tables_deduplicated(self):
        from app.mcp.fastmcp_server import _extract_table_names

        tables = _extract_table_names(
            "select * from users u join users u2 on u.id = u2.parent_id"
        )
        # "users" may appear twice but should be deduplicated
        assert tables.count("users") <= 1

    def test_no_from_returns_empty(self):
        from app.mcp.fastmcp_server import _extract_table_names

        tables = _extract_table_names("select 1")
        assert tables == []
