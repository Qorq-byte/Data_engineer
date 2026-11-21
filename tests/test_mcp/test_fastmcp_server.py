"""Tests for NL2SQL MCP Server — server creation, tools, resources, helpers."""

from __future__ import annotations

import asyncio
import json
import socket

import httpx
import pytest
import uvicorn
from fastmcp import Client
from fastmcp.client.transports import SSETransport

from app.auth.security import create_access_token
from app.config.settings import settings
from tests.test_mcp.test_metric_tools import http_exchange

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
        assert server.name == "QueryHarness"

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


@pytest.mark.parametrize("user, tenant, status", [
    (None, 1, 401), (99999, 1, 403), (1, 2, 401), (3, 2, 403),
])
async def test_http_guards_ordinary_tools_and_resources(
    connected_mcp_server, monkeypatch, user, tenant, status,
):
    monkeypatch.setattr(settings, "admin_user_ids", [1, 3])
    token = create_access_token(user, "fixture", tenant) if user is not None else None
    sql = {"sql": "SELECT id FROM orders", "db_id": "mcp-test"}
    calls = [
        ("nl_query", {"nl_text": "list orders", "db_id": "mcp-test"}),
        ("explain_sql", sql),
        ("validate_sql", sql),
        ("search_schema", {"keywords": ["orders"], "db_id": "mcp-test"}),
        ("translate_sql", {"sql": "SELECT 1", "target_dialect": "postgresql"}),
    ]
    resources = ["schema/mcp-test", "domains/", "nodes/", "workflows/"]
    messages = [("tools/call", {"name": name, "arguments": args}) for name, args in calls]
    messages.extend(("resources/read", {"uri": f"nlsql://{uri}"}) for uri in resources)
    for method, params in messages:
        result = await http_exchange(connected_mcp_server, method, params, token)
        assert result.status_code == status and set(result.json()) == {"detail"}
        if status == 401:
            assert result.headers["www-authenticate"] == "Bearer"
        if token:
            assert token not in result.text


async def test_authenticated_http_query_and_schema(connected_mcp_server, monkeypatch):
    from app.api import query
    from app.llm.router import LiteLLMRouter, RouterConfig

    class ScriptedRouter(LiteLLMRouter):
        async def complete(self, messages, **kwargs):
            return {
                "choices": [{"message": {"content": "SELECT id FROM orders ORDER BY id"}}],
                "model": "scripted-test-model",
            }

    monkeypatch.setattr(settings, "admin_user_ids", [1])
    router = ScriptedRouter(RouterConfig(), mock_mode=True)
    monkeypatch.setattr(query, "get_default_router", lambda: router)
    token = create_access_token(1, "fixture")
    result = await http_exchange(connected_mcp_server, "tools/call", {
        "name": "nl_query", "arguments": {"nl_text": "list orders", "db_id": "mcp-test"},
    }, token)
    assert result.status_code == 200
    assert result.json()["result"]["structuredContent"]["rows"] == [[1], [2], [3], [4], [5]]

    result = await http_exchange(
        connected_mcp_server, "resources/read", {"uri": "nlsql://schema/mcp-test"}, token
    )
    assert result.status_code == 200
    schema = json.loads(result.json()["result"]["contents"][0]["text"])
    assert "orders" in schema["tables"]


async def test_streamable_http_session_id_does_not_replace_jwt(connected_mcp_server, monkeypatch):
    monkeypatch.setattr(settings, "admin_user_ids", [1])
    app = connected_mcp_server.http_app(path="/mcp", stateless_http=False, json_response=True)
    async with app.router.lifespan_context(app), httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://localhost",
        headers={"Accept": "application/json, text/event-stream"},
    ) as client:
        token = create_access_token(1, "fixture")
        opened = await client.post("/mcp", headers={"Authorization": f"Bearer {token}"}, json={
            "jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
                "protocolVersion": "2025-06-18", "capabilities": {},
                "clientInfo": {"name": "fixture", "version": "1"},
            },
        })
        assert opened.status_code == 200
        headers = {"Mcp-Session-Id": opened.headers["mcp-session-id"]}
        assert (await client.get("/mcp", headers=headers)).status_code == 401
        assert (await client.delete("/mcp", headers=headers)).status_code == 401
        replay = await client.post("/mcp", headers=headers, json={
            "jsonrpc": "2.0", "id": 2, "method": "resources/read",
            "params": {"uri": "nlsql://schema/mcp-test"},
        })
        assert replay.status_code == 401
        assert set(replay.json()) == {"detail"}


async def test_sse_requires_operator_for_stream_and_messages(connected_mcp_server, monkeypatch):
    monkeypatch.setattr(settings, "admin_user_ids", [1, 3])
    app = connected_mcp_server.http_app(transport="sse", path="/sse")
    server = uvicorn.Server(uvicorn.Config(app, log_level="error", lifespan="on"))
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        url = f"http://127.0.0.1:{listener.getsockname()[1]}"
        running = asyncio.create_task(server.serve(sockets=[listener]))
        try:
            async with asyncio.timeout(10):
                while not server.started:
                    if running.done():
                        await running
                        pytest.fail("SSE test host exited without starting")
                    await asyncio.sleep(0.01)
            async with httpx.AsyncClient(base_url=url, timeout=5) as http:
                assert (await http.get("/sse")).status_code == 401
                denied = {"Authorization": f"Bearer {create_access_token(3, 'fixture', 2)}"}
                assert (await http.get("/sse", headers=denied)).status_code == 403
                assert (await http.post("/messages/", json={})).status_code == 401
                assert (await http.post("/messages/", headers=denied, json={})).status_code == 403
            headers = {"Authorization": f"Bearer {create_access_token(1, 'fixture')}"}
            async with Client(SSETransport(url + "/sse", headers=headers), timeout=5) as client:
                result = await client.call_tool(
                    "search_schema", {"keywords": ["orders"], "db_id": "mcp-test"}
                )
                assert result.structured_content["tables"] == [
                    {"table": "orders", "match_type": "name"}
                ]
                schema = await client.read_resource("nlsql://schema/mcp-test")
                assert "orders" in json.loads(schema[0].text)["tables"]
        finally:
            server.should_exit = True
            await asyncio.wait_for(running, timeout=10)


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
