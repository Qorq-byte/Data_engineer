"""Tests for FeedbackAgent — feedback collection and diff extraction."""

from __future__ import annotations

from typing import Any

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


class _FakeStore:
    """Minimal stand-in for MySQLStore that records what it was asked to save."""

    def __init__(self, fail: bool = False) -> None:
        self.rows: list[dict[str, Any]] = []
        self.fail = fail

    async def save_feedback(self, row: dict[str, Any]) -> None:
        if self.fail:
            raise RuntimeError("mysql is down")
        self.rows.append(row)


class TestFeedbackStorage:
    """The agent must say where the record actually went, not always "ok"."""

    @pytest.mark.anyio
    async def test_no_store_reports_process_memory(self, monkeypatch):
        monkeypatch.setattr("app.storage.mysql_store.get_store", lambda: None)
        agent = FeedbackAgent()

        result = await agent.execute({"rating": 5, "comment": "ok"}, {})

        assert result.status == AgentStatus.DONE
        assert result.data["storage"] == "process_memory"
        assert result.data["persisted"] is False
        assert result.metadata["persist_error"] is None
        assert result.metadata["learning_pipeline"] is False

    @pytest.mark.anyio
    async def test_store_receives_rest_shaped_row(self, monkeypatch):
        store = _FakeStore()
        monkeypatch.setattr("app.storage.mysql_store.get_store", lambda: store)
        agent = FeedbackAgent()

        result = await agent.execute(
            {
                "turn_id": "turn_9",
                "rating": 4,
                "comment": "wrong filter",
                "original_sql": "SELECT 1",
                "final_sql": "SELECT 2",
            },
            {"session_id": "s-1", "nl_input": "上个月订单", "domain_id": "sales"},
        )

        assert result.data["storage"] == "mysql"
        assert result.data["persisted"] is True
        assert len(store.rows) == 1
        row = store.rows[0]
        # Keys must match what MySQLStore.save_feedback reads, otherwise the
        # INSERT silently stores empty strings.
        assert row["query_id"] == "turn_9"
        assert row["session_id"] == "s-1"
        assert row["nl_input"] == "上个月订单"
        assert row["sql_generated"] == "SELECT 1"
        assert row["sql_final"] == "SELECT 2"
        assert row["rating"] == 4
        assert row["feedback_text"] == "wrong filter"
        assert row["domain"] == "sales"
        assert row["feedback_id"].startswith("fb_agent_")

    @pytest.mark.anyio
    async def test_store_failure_keeps_record_and_reports_error(self, monkeypatch):
        monkeypatch.setattr("app.storage.mysql_store.get_store", lambda: _FakeStore(fail=True))
        agent = FeedbackAgent()

        result = await agent.execute({"rating": 2}, {})

        # A dead database must not lose the feedback silently, nor fail the call:
        # it degrades to the in-process copy and says so.
        assert result.status == AgentStatus.DONE
        assert result.data["storage"] == "process_memory"
        assert result.data["persisted"] is False
        assert agent.record_count == 1
        assert "RuntimeError" in result.metadata["persist_error"]

    @pytest.mark.anyio
    async def test_feedback_ids_are_unique_across_agents(self, monkeypatch):
        store = _FakeStore()
        monkeypatch.setattr("app.storage.mysql_store.get_store", lambda: store)

        await FeedbackAgent().record_feedback(turn_id="t1", rating=5)
        await FeedbackAgent().record_feedback(turn_id="t1", rating=5)

        # Two fresh agents would both count from zero; the primary key must not.
        assert len({row["feedback_id"] for row in store.rows}) == 2
