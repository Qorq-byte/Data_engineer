"""Feedback API must report where the record actually went.

``POST /api/v1/feedback`` used to answer "反馈已记录至学习库" no matter what
happened to the MySQL write or the learning collector. The response now carries
``storage`` / ``persisted`` / ``learning`` so the caller can tell a durable
record from a process-local one.
"""

from __future__ import annotations

import importlib
from typing import Any

import pytest

from app.api import feedback as feedback_api

# ``app.learning`` re-exports the *instance* under the name ``feedback_collector``,
# which shadows the submodule — go through importlib to get the module itself.
_COLLECTOR_MODULE = importlib.import_module("app.learning.feedback_collector")


class _FakeStore:
    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []

    async def save_feedback(self, row: dict[str, Any]) -> None:
        self.rows.append(row)


@pytest.fixture(autouse=True)
def isolated_feedback_state(monkeypatch: pytest.MonkeyPatch):
    """Keep the module-level fallback list and the SQLite collector out of tests."""
    monkeypatch.setattr(feedback_api, "_feedback_store", [])
    monkeypatch.setattr(_COLLECTOR_MODULE.feedback_collector, "collect", lambda **_: "fb_test")


def _body(**overrides: Any) -> feedback_api.FeedbackRequest:
    payload = {
        "query_id": "q-1",
        "session_id": "s-1",
        "nl_input": "上个月订单总额",
        "sql_generated": "SELECT SUM(amount) FROM orders",
        "sql_final": "SELECT SUM(amount) FROM orders WHERE status='paid'",
        "rating": 4,
        "feedback_text": "缺少状态过滤",
        "domain": "sales",
    }
    payload.update(overrides)
    return feedback_api.FeedbackRequest(**payload)


async def test_response_reports_process_memory_when_no_store(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(feedback_api, "get_store", lambda: None)

    data = await feedback_api.submit_feedback(_body())

    assert data["status"] == "recorded"
    assert data["storage"] == "process_memory"
    assert data["persisted"] is False
    assert data["learning"] is True
    assert "进程内" in data["message"]


async def test_response_reports_mysql_and_row_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    store = _FakeStore()
    monkeypatch.setattr(feedback_api, "get_store", lambda: store)

    data = await feedback_api.submit_feedback(_body())

    assert data["storage"] == "mysql"
    assert data["persisted"] is True
    assert len(store.rows) == 1
    row = store.rows[0]
    assert row["query_id"] == "q-1"
    assert row["sql_final"] == "SELECT SUM(amount) FROM orders WHERE status='paid'"
    assert row["feedback_text"] == "缺少状态过滤"
    assert row["domain"] == "sales"


async def test_store_failure_degrades_instead_of_claiming_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _BrokenStore:
        async def save_feedback(self, row: dict[str, Any]) -> None:
            raise RuntimeError("mysql is down")

    monkeypatch.setattr(feedback_api, "get_store", lambda: _BrokenStore())

    data = await feedback_api.submit_feedback(_body())

    # The request still succeeds (in-process fallback), but it no longer claims
    # the feedback reached the database.
    assert data["status"] == "recorded"
    assert data["storage"] == "process_memory"
    assert data["persisted"] is False
    assert len(feedback_api._feedback_store) == 1


async def test_collector_failure_is_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(feedback_api, "get_store", lambda: None)

    def _boom(**_: Any) -> str:
        raise RuntimeError("collector is down")

    monkeypatch.setattr(_COLLECTOR_MODULE.feedback_collector, "collect", _boom)

    data = await feedback_api.submit_feedback(_body())

    assert data["learning"] is False
    assert data["storage"] == "process_memory"
    assert "学习队列" in data["message"]
