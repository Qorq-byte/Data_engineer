"""Tests for NL2SQL MCP Server — server creation, tools, resources, helpers."""

from __future__ import annotations

import json

import pytest

# ═══════════════════════════════════════════════════════════════════════════════
# Server creation
# ═══════════════════════════════════════════════════════════════════════════════


class TestCreateNL2SQLServer:
    def test_creates_fastmcp_instance(self):
        from app.mcp.fastmcp_server import create_nl2sql_server

        server = create_nl2sql_server(port=8090)
        from fastmcp import FastMCP

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

    async def test_custom_port(self, monkeypatch):
        from fastmcp import FastMCP

        from app.mcp.fastmcp_server import create_nl2sql_server

        received = {}

        async def capture_run(self, transport, show_banner, **kwargs):
            received.update(kwargs)

        monkeypatch.setattr(FastMCP, "run_async", capture_run)
        server = create_nl2sql_server(host="127.0.0.2", port=9999)
        await server.run_async(transport="sse")
        assert received == {"host": "127.0.0.2", "port": 9999}

    async def test_stdio_does_not_receive_http_bind_options(self, monkeypatch):
        from fastmcp import FastMCP

        from app.mcp.fastmcp_server import create_nl2sql_server

        received = {}

        async def capture_run(self, transport, show_banner, **kwargs):
            received.update(kwargs)

        monkeypatch.setattr(FastMCP, "run_async", capture_run)
        await create_nl2sql_server().run_async(transport="stdio")
        assert received == {}

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

    async def test_all_five_tools_registered(self):
        from app.mcp.fastmcp_server import create_nl2sql_server

        server = create_nl2sql_server()

        registered = {tool.name for tool in await server.list_tools()}

        for tool_name in self.EXPECTED_TOOLS:
            assert tool_name in registered, f"Tool '{tool_name}' not registered"

    async def test_nl_query_has_description(self):
        from app.mcp.fastmcp_server import create_nl2sql_server

        server = create_nl2sql_server()
        tool = next(tool for tool in await server.list_tools() if tool.name == "nl_query")
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

    async def test_all_resources_registered(self):
        from app.mcp.fastmcp_server import create_nl2sql_server

        server = create_nl2sql_server()

        registered = {str(resource.uri) for resource in await server.list_resources()}

        for resource_uri in self.EXPECTED_RESOURCES:
            assert resource_uri in registered, (
                f"Resource '{resource_uri}' not registered"
            )

    async def test_all_templates_registered(self):
        from app.mcp.fastmcp_server import create_nl2sql_server

        server = create_nl2sql_server()

        registered_templates = {
            resource.uri_template for resource in await server.list_resource_templates()
        }

        for template_uri in self.EXPECTED_TEMPLATES:
            assert template_uri in registered_templates, (
                f"Template '{template_uri}' not registered"
            )


# ═══════════════════════════════════════════════════════════════════════════════
# Helper functions
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.fixture
async def connected_mcp_server(test_sqlite_db, monkeypatch):
    from app.db.connections import ConnectionFactory, DatabaseType
    from app.mcp.fastmcp_server import create_nl2sql_server

    monkeypatch.setattr(ConnectionFactory, "_connections", {})
    monkeypatch.setattr(ConnectionFactory, "_infos", {})
    await ConnectionFactory.create(
        DatabaseType.SQLITE, {"path": test_sqlite_db}, db_id="mcp-test"
    )
    yield create_nl2sql_server()
    ConnectionFactory.close("mcp-test")


class TestToolExecution:
    async def test_schema_search_uses_live_tables_and_deduplicates(self, connected_mcp_server):
        result = await connected_mcp_server.call_tool(
            "search_schema", {"keywords": ["users", "USER", "id", "id"], "db_id": "mcp-test"}
        )
        data = result.structured_content
        assert "error" not in data
        assert {table["table"] for table in data["tables"]} == {"users"}
        for table in data["columns"]:
            assert len(table["columns"]) == len(set(table["columns"]))

    @pytest.mark.parametrize(
        "sql",
        ["DELETE FROM users", "SELECT 1; DROP TABLE users", "SELECT FROM users"],
    )
    async def test_validation_rejects_unsafe_or_invalid_sql(self, connected_mcp_server, sql):
        result = await connected_mcp_server.call_tool("validate_sql", {"sql": sql})
        assert result.structured_content["valid"] is False
        assert result.structured_content["errors"]

    async def test_validation_checks_live_schema(self, connected_mcp_server):
        valid = await connected_mcp_server.call_tool(
            "validate_sql", {"sql": "SELECT id FROM users", "db_id": "mcp-test"}
        )
        invalid = await connected_mcp_server.call_tool(
            "validate_sql", {"sql": "SELECT * FROM unknown_table", "db_id": "mcp-test"}
        )
        assert valid.structured_content["valid"] is True
        assert invalid.structured_content["valid"] is False
        assert any("unknown_table" in error for error in invalid.structured_content["errors"])

    async def test_missing_database_is_not_silently_valid(self, connected_mcp_server):
        result = await connected_mcp_server.call_tool(
            "validate_sql", {"sql": "SELECT 1", "db_id": "missing-db"}
        )
        assert result.structured_content["valid"] is False
        assert "missing-db" in " ".join(result.structured_content["errors"])

    async def test_explain_executes_actual_sqlite_plan(self, connected_mcp_server):
        result = await connected_mcp_server.call_tool(
            "explain_sql", {"sql": "SELECT id FROM users", "db_id": "mcp-test"}
        )
        data = result.structured_content
        assert "error" not in data
        assert "users" in data["raw_plan"]
        assert data["analysis"]["has_full_scan"] is True

    async def test_explain_cannot_execute_writes(self, connected_mcp_server):
        result = await connected_mcp_server.call_tool(
            "explain_sql", {"sql": "DELETE FROM users", "db_id": "mcp-test"}
        )
        assert result.structured_content["error"]
        validation = await connected_mcp_server.call_tool(
            "validate_sql", {"sql": "SELECT * FROM users", "db_id": "mcp-test"}
        )
        assert validation.structured_content["valid"] is True

    async def test_translate_tool_executes(self, connected_mcp_server):
        result = await connected_mcp_server.call_tool(
            "translate_sql",
            {"sql": "SELECT * FROM users LIMIT 2", "target_dialect": "postgresql"},
        )
        assert "error" not in result.structured_content
        assert "SELECT" in result.structured_content["translated_sql"]

    async def test_nl_query_reaches_production_pipeline(self, connected_mcp_server, monkeypatch):
        from app.api import query
        from app.llm.router import LiteLLMRouter, RouterConfig

        class ScriptedRouter(LiteLLMRouter):
            async def complete(self, messages, **kwargs):
                return {
                    "choices": [{"message": {"content": "SELECT id FROM orders ORDER BY id"}}],
                    "model": "scripted-test-model",
                }

        router = ScriptedRouter(RouterConfig(), mock_mode=True)
        monkeypatch.setattr(query, "get_default_router", lambda: router)
        result = await connected_mcp_server.call_tool(
            "nl_query", {"nl_text": "find all orders", "db_id": "mcp-test"}
        )
        data = result.structured_content
        assert "error" not in data, data
        assert data["sql"]
        assert data["columns"]
        assert data["row_count"] > 0
        assert data["rows"] == [[1], [2], [3], [4], [5]]


class TestResourceExecution:
    async def test_schema_resource_reads_actual_columns(self, connected_mcp_server):
        result = await connected_mcp_server.read_resource("nlsql://schema/mcp-test")
        data = json.loads(result.contents[0].content)
        assert data["db_id"] == "mcp-test"
        assert {column["name"] for column in data["tables"]["users"]} >= {"id", "name"}

    async def test_workflows_resource_serializes_node_definitions(self, connected_mcp_server):
        result = await connected_mcp_server.read_resource("nlsql://workflows/")
        data = json.loads(result.contents[0].content)
        assert "error" not in data
        assert data["workflows"]
        assert all(isinstance(node, dict) for node in data["workflows"][0]["node_order"])


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
