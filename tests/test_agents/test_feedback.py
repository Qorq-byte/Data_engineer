"""Tests for FeedbackAgent — feedback collection and diff extraction."""

from __future__ import annotations

import pytest

from app.agents.base import AgentStatus
from app.agents.feedback import FeedbackAgent, FeedbackRecord


class TestFeedbackRecord:
    def test_defaults(self):
        r = FeedbackRecord()
        assert r.rating == 0
        assert r.comment == ""
        assert r.diff_ops == []

    def test_full_record(self):
        r = FeedbackRecord(
            turn_id="t1",
            rating=4,
            comment="good",
            original_sql="SELECT * FROM users",
            final_sql="SELECT id, name FROM users",
            diff_ops=[{"op": "remove", "line": "SELECT * FROM users"}],
        )
        assert r.turn_id == "t1"
        assert r.rating == 4


class TestFeedbackAgent:
    @pytest.fixture
    def agent(self):
        return FeedbackAgent()

    @pytest.mark.anyio
    async def test_name_and_description(self, agent):
        assert agent.name == "feedback_processor"
        assert agent.description

    @pytest.mark.anyio
    async def test_execute_non_dict_returns_error(self, agent):
        result = await agent.execute("not a dict", {})
        assert result.status == AgentStatus.ERROR

    @pytest.mark.anyio
    async def test_execute_valid_rating(self, agent):
        result = await agent.execute({"rating": 4, "comment": "nice"}, {})
        assert result.status == AgentStatus.DONE
        assert result.data["acknowledged"] is True
        assert result.data["rating"] == 4

    @pytest.mark.anyio
    async def test_execute_rating_too_low(self, agent):
        result = await agent.execute({"rating": 0}, {})
        assert result.status == AgentStatus.ERROR
        assert "1–5" in result.errors[0]

    @pytest.mark.anyio
    async def test_execute_rating_too_high(self, agent):
        result = await agent.execute({"rating": 6}, {})
        assert result.status == AgentStatus.ERROR

    @pytest.mark.anyio
    async def test_execute_sql_diff(self, agent):
        result = await agent.execute(
            {
                "rating": 3,
                "original_sql": "SELECT * FROM users",
                "final_sql": "SELECT id, name FROM users",
            },
            {},
        )
        assert result.status == AgentStatus.DONE
        assert result.metadata["has_diff"] is True

    @pytest.mark.anyio
    async def test_execute_no_diff_same_sql(self, agent):
        result = await agent.execute(
            {
                "rating": 5,
                "original_sql": "SELECT 1",
                "final_sql": "SELECT 1",
            },
            {},
        )
        assert result.metadata["has_diff"] is False

    @pytest.mark.anyio
    async def test_convenience_record_feedback(self, agent):
        result = await agent.record_feedback(
            turn_id="turn_123",
            rating=5,
            comment="perfect",
        )
        assert result.status == AgentStatus.DONE
        assert result.data["rating"] == 5

    @pytest.mark.anyio
    async def test_extract_diff_returns_ops(self, agent):
        ops = await agent.extract_diff("SELECT a", "SELECT b")
        assert isinstance(ops, list)
        assert len(ops) > 0

    @pytest.mark.anyio
    async def test_extract_diff_same_text_empty(self, agent):
        ops = await agent.extract_diff("same", "same")
        assert ops == []

    @pytest.mark.anyio
    async def test_record_count_increments(self, agent):
        await agent.record_feedback(rating=5)
        await agent.record_feedback(rating=4)
        assert agent.record_count == 2

    @pytest.mark.anyio
    async def test_reset_clears_records(self, agent):
        await agent.record_feedback(rating=3)
        agent.reset()
        assert agent.record_count == 0
        assert agent.status == AgentStatus.IDLE
