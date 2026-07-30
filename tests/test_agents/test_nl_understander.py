"""Tests for NLUnderstandingAgent — wraps NLParser (5-step NL→SQR pipeline)."""

from __future__ import annotations

import pytest

from app.agents.base import AgentStatus
from app.agents.nl_understander import NLUnderstandingAgent


class TestNLUnderstandingAgent:
    @pytest.fixture
    def agent(self):
        return NLUnderstandingAgent()

    @pytest.mark.anyio
    async def test_name_and_description(self, agent):
        assert agent.name == "nl_understander"
        assert agent.description

    @pytest.mark.anyio
    async def test_execute_basic_chinese(self, agent):
        result = await agent.execute("查询所有用户", {})
        assert result.status == AgentStatus.DONE
        from app.models.query import SQR
        assert isinstance(result.data, SQR)
        assert result.data.raw_text == "查询所有用户"

    @pytest.mark.anyio
    async def test_execute_basic_english(self, agent):
        result = await agent.execute("find all users", {})
        assert result.status == AgentStatus.DONE
        from app.models.query import SQR
        assert isinstance(result.data, SQR)
        assert result.data.language == "en"

    @pytest.mark.anyio
    async def test_execute_via_dict_input(self, agent):
        result = await agent.execute({"nl_text": "统计订单数"}, {})
        assert result.status == AgentStatus.DONE
        assert result.data is not None

    @pytest.mark.anyio
    async def test_execute_via_query_text_key(self, agent):
        result = await agent.execute({"query_text": "show products"}, {})
        assert result.status == AgentStatus.DONE
        assert result.data is not None

    @pytest.mark.anyio
    async def test_execute_via_text_key(self, agent):
        result = await agent.execute({"text": "count users"}, {})
        assert result.status == AgentStatus.DONE
        assert result.data is not None

    @pytest.mark.anyio
    async def test_execute_empty_input(self, agent):
        result = await agent.execute({}, {})
        assert result.status == AgentStatus.DONE
        # Empty input should still produce an SQR
        assert result.data is not None

    @pytest.mark.anyio
    async def test_execute_non_str_non_dict(self, agent):
        result = await agent.execute(12345, {})
        assert result.status == AgentStatus.DONE  # gracefully handled
        assert result.data is not None

    @pytest.mark.anyio
    async def test_detects_chinese_language(self, agent):
        result = await agent.execute("上月订单总数多少", {})
        assert result.data is not None
        assert result.data.language == "zh"

    @pytest.mark.anyio
    async def test_detects_english_language(self, agent):
        result = await agent.execute("total orders last month", {})
        assert result.data is not None
        assert result.data.language in ("en", "mixed")

    @pytest.mark.anyio
    async def test_metadata_includes_latency(self, agent):
        result = await agent.execute("hello", {})
        assert "latency_ms" in result.metadata
        assert result.metadata["latency_ms"] >= 0

    @pytest.mark.anyio
    async def test_metadata_includes_language(self, agent):
        result = await agent.execute("你好", {})
        assert "language" in result.metadata

    @pytest.mark.anyio
    async def test_reset_returns_to_idle(self, agent):
        await agent.execute("test", {})
        agent.reset()
        assert agent.status == AgentStatus.IDLE
