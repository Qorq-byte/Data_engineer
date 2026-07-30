"""Tests for ValidationAgent — wraps SQLValidator."""

from __future__ import annotations

import pytest

from app.agents.base import AgentStatus
from app.agents.sql_validator import ValidationAgent


class TestValidationAgent:
    @pytest.fixture
    def agent(self):
        return ValidationAgent()

    @pytest.mark.anyio
    async def test_name_and_description(self, agent):
        assert agent.name == "sql_validator"
        assert agent.description

    @pytest.mark.anyio
    async def test_execute_valid_sql(self, agent):
        result = await agent.execute("SELECT 1", {})
        assert result.status == AgentStatus.DONE

    @pytest.mark.anyio
    async def test_execute_returns_report_dict(self, agent):
        result = await agent.execute("SELECT * FROM dual", {})
        assert isinstance(result.data, dict)
        assert "report" in result.data
        assert "passed" in result.data

    @pytest.mark.anyio
    async def test_execute_invalid_sql(self, agent):
        result = await agent.execute("SELEC 1", {})
        assert result.status == AgentStatus.DONE
        # Should detect syntax error
        assert result.data["passed"] is False

    @pytest.mark.anyio
    async def test_execute_via_dict(self, agent):
        result = await agent.execute({"sql": "SELECT 2"}, {})
        assert result.status == AgentStatus.DONE
        assert result.data["passed"] is True

    @pytest.mark.anyio
    async def test_execute_empty_input(self, agent):
        result = await agent.execute("", {})
        assert result.status == AgentStatus.DONE
        # Empty SQL should fail syntax
        assert result.data["passed"] is False

    @pytest.mark.anyio
    async def test_metadata_includes_score(self, agent):
        result = await agent.execute("SELECT 1", {})
        assert "score" in result.metadata

    @pytest.mark.anyio
    async def test_metadata_includes_latency(self, agent):
        result = await agent.execute("SELECT 1", {})
        assert "latency_ms" in result.metadata
        assert result.metadata["latency_ms"] >= 0

    @pytest.mark.anyio
    async def test_metadata_includes_error_count(self, agent):
        result = await agent.execute("SELEC * FORM users WHERE x = 1", {})
        assert "error_count" in result.metadata

    @pytest.mark.anyio
    async def test_reset(self, agent):
        await agent.execute("SELECT 1", {})
        agent.reset()
        assert agent.status == AgentStatus.IDLE
