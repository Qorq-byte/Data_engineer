"""Tests for ToolExecutionAgent — unified SQL + MCP gateway."""

from __future__ import annotations

import pytest

from app.agents.base import AgentStatus
from app.agents.tool_executor import ToolExecutionAgent


class TestToolExecutionAgent:
    @pytest.fixture
    def agent(self):
        return ToolExecutionAgent()

    @pytest.mark.anyio
    async def test_name_and_description(self, agent):
        assert agent.name == "tool_executor"
        assert agent.description

    @pytest.mark.anyio
    async def test_execute_with_non_dict_returns_error(self, agent):
        result = await agent.execute("not a dict", {})
        assert result.status == AgentStatus.ERROR

    @pytest.mark.anyio
    async def test_execute_empty_input_returns_error(self, agent):
        result = await agent.execute({}, {})
        assert result.status == AgentStatus.ERROR
        assert "Unknown action" in result.errors[0]

    @pytest.mark.anyio
    async def test_execute_sql_without_connection_returns_error(self, agent):
        result = await agent.execute({"sql": "SELECT 1"}, {})
        assert result.status == AgentStatus.ERROR
        assert "No database connection" in result.errors[0]

    @pytest.mark.anyio
    async def test_execute_empty_sql_returns_error(self, agent):
        result = await agent.execute({"sql": ""}, {})
        assert result.status == AgentStatus.ERROR
        assert "Empty SQL" in result.errors[0]

    @pytest.mark.anyio
    async def test_execute_sql_with_connection(self, agent, sqlite_connection):
        conn = sqlite_connection
        result = await agent.execute(
            {"sql": "SELECT 1 AS num"},
            {"connection": conn},
        )
        assert result.status == AgentStatus.DONE
        assert "columns" in result.data
        assert result.data["columns"] == ["num"]
        assert result.data["rows"][0][0] == 1

    @pytest.mark.anyio
    async def test_execute_sql_with_truncation(self, agent, sqlite_connection):
        result = await agent.execute(
            {"sql": "SELECT 1", "max_rows": 5},
            {"connection": sqlite_connection},
        )
        assert result.status == AgentStatus.DONE
        assert result.data["truncated"] is False
        assert result.data["row_count"] == 1

    @pytest.mark.anyio
    async def test_execute_sql_error_handling(self, agent, sqlite_connection):
        result = await agent.execute(
            {"sql": "SELECT * FROM nonexistent_table"},
            {"connection": sqlite_connection},
        )
        assert result.status == AgentStatus.ERROR
        assert "SQL execution failed" in result.errors[0]

    @pytest.mark.anyio
    async def test_execute_mcp_tool_without_client_returns_stub(self, agent):
        result = await agent.execute(
            {"server": "db_server", "tool": "list_tables", "args": {}},
            {},
        )
        assert result.status == AgentStatus.DONE
        assert result.data.get("stub") is True

    @pytest.mark.anyio
    async def test_execute_get_schema_without_connection(self, agent):
        result = await agent.execute(
            {"action": "get_schema", "db_id": "main"},
            {},
        )
        assert result.status == AgentStatus.DONE
        # Returns schema from context or None

    @pytest.mark.anyio
    async def test_execute_get_schema_from_context(self, agent):
        from app.models.schema import SchemaSnapshot
        schema = SchemaSnapshot(database_type="sqlite", database_name="test", tables={})
        result = await agent.execute(
            {"action": "get_schema"},
            {"schema": schema},
        )
        assert result.status == AgentStatus.DONE
        assert result.data == schema
        assert result.metadata["source"] == "context"

    @pytest.mark.anyio
    async def test_convenience_execute_query(self, agent, sqlite_connection):
        result = await agent.execute_query(
            sql="SELECT 2 AS val",
            context={"connection": sqlite_connection},
        )
        assert result.status == AgentStatus.DONE
        assert result.data["rows"][0][0] == 2

    @pytest.mark.anyio
    async def test_convenience_call_mcp_tool(self, agent):
        result = await agent.call_mcp_tool("db", "list", {})
        assert result.status == AgentStatus.DONE
        assert result.data.get("stub") is True

    @pytest.mark.anyio
    async def test_reset(self, agent):
        agent.reset()
        assert agent.status == AgentStatus.IDLE
