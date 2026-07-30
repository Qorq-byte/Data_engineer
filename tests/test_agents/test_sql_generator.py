"""Tests for SQLGenerationAgent — wraps SQLGenerator + PromptBuilder."""

from __future__ import annotations

import pytest

from app.agents.base import AgentStatus
from app.agents.sql_generator import SQLGenerationAgent


class TestSQLGenerationAgent:
    @pytest.fixture
    def agent(self):
        return SQLGenerationAgent()

    @pytest.mark.anyio
    async def test_name_and_description(self, agent):
        assert agent.name == "sql_generator"
        assert agent.description

    @pytest.mark.anyio
    async def test_execute_without_generator_returns_empty(self, agent):
        result = await agent.execute({}, {})
        assert result.status == AgentStatus.DONE
        assert result.data == []

    @pytest.mark.anyio
    async def test_execute_with_dict_input(self, agent):
        result = await agent.execute(
            {"sqr": None, "schema": None, "domain": None},
            {},
        )
        assert result.status == AgentStatus.DONE
        assert isinstance(result.data, list)

    @pytest.mark.anyio
    async def test_strategy_default_temperature(self, agent):
        result = await agent.execute({"strategy": "temperature"}, {})
        assert result.metadata.get("strategy") == "temperature"

    @pytest.mark.anyio
    async def test_strategy_multi_perspective(self, agent):
        result = await agent.execute({"strategy": "multi_perspective"}, {})
        assert result.metadata.get("strategy") == "multi_perspective"

    @pytest.mark.anyio
    async def test_num_candidates_default(self, agent):
        result = await agent.execute({}, {})
        assert "candidate_count" in result.metadata

    @pytest.mark.anyio
    async def test_num_candidates_explicit(self, agent):
        result = await agent.execute({"num_candidates": 5}, {})
        # No generator, so count is 0, but metadata is set
        assert result.metadata["candidate_count"] == 0

    @pytest.mark.anyio
    async def test_retry_with_correction(self, agent):
        result = await agent.retry_with_correction(
            previous_sql="SELECT * FORM users",
            error="syntax error near FORM",
            context={},
        )
        assert result.status == AgentStatus.DONE
        assert result.metadata.get("retry") is True

    @pytest.mark.anyio
    async def test_metadata_includes_latency(self, agent):
        result = await agent.execute({}, {})
        assert "latency_ms" in result.metadata
        assert result.metadata["latency_ms"] >= 0

    @pytest.mark.anyio
    async def test_reset(self, agent):
        await agent.execute({}, {})
        agent.reset()
        assert agent.status == AgentStatus.IDLE
