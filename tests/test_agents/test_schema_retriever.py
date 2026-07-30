"""Tests for SchemaRetrievalAgent — wraps SchemaRetriever + Glossary + Rules."""

from __future__ import annotations

import pytest

from app.agents.base import AgentStatus
from app.agents.schema_retriever import SchemaRetrievalAgent


class TestSchemaRetrievalAgent:
    @pytest.fixture
    def agent(self):
        return SchemaRetrievalAgent()

    @pytest.mark.anyio
    async def test_name_and_description(self, agent):
        assert agent.name == "schema_retriever"
        assert agent.description

    @pytest.mark.anyio
    async def test_execute_with_sqr(self, agent):
        from app.models.query import SQR
        sqr = SQR(raw_text="find all users", language="en")
        result = await agent.execute(sqr, {})
        assert result.status == AgentStatus.DONE
        assert isinstance(result.data, dict)
        assert "tables" in result.data

    @pytest.mark.anyio
    async def test_execute_with_string_input(self, agent):
        result = await agent.execute("find orders", {})
        assert result.status == AgentStatus.DONE
        assert isinstance(result.data, dict)

    @pytest.mark.anyio
    async def test_execute_with_dict_input(self, agent):
        result = await agent.execute({"nl_text": "统计产品销量"}, {})
        assert result.status == AgentStatus.DONE
        assert isinstance(result.data, dict)

    @pytest.mark.anyio
    async def test_execute_with_dict_containing_sqr(self, agent):
        from app.models.query import SQR
        sqr = SQR(raw_text="query", language="zh")
        result = await agent.execute({"sqr": sqr}, {})
        assert result.status == AgentStatus.DONE
        assert isinstance(result.data, dict)

    @pytest.mark.anyio
    async def test_no_retriever_returns_empty_tables(self, agent):
        result = await agent.execute("some query", {})
        assert result.data["tables"] == []
        assert result.data["terms"] == []
        assert result.data["rules"] == []

    @pytest.mark.anyio
    async def test_metadata_includes_counts(self, agent):
        result = await agent.execute("test", {})
        assert "table_count" in result.metadata
        assert "term_count" in result.metadata
        assert "rule_count" in result.metadata

    @pytest.mark.anyio
    async def test_metadata_includes_latency(self, agent):
        result = await agent.execute("test", {})
        assert "latency_ms" in result.metadata
        assert result.metadata["latency_ms"] >= 0

    @pytest.mark.anyio
    async def test_with_schema_in_context(self, agent):
        from app.models.schema import SchemaSnapshot
        schema = SchemaSnapshot(database_type="sqlite", database_name="test", tables={})
        result = await agent.execute("query", {"schema": schema})
        assert result.status == AgentStatus.DONE
        assert result.data["filtered_schema"] is None  # no retriever, so None

    @pytest.mark.anyio
    async def test_reset(self, agent):
        await agent.execute("test", {})
        agent.reset()
        assert agent.status == AgentStatus.IDLE
