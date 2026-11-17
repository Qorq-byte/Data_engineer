"""Automatic plan selection through the query API and the workflow catalog."""

from __future__ import annotations

import pytest

from app.api import create_app, query
from app.db.connections import ConnectionFactory, DatabaseType
from app.llm.router import LiteLLMRouter, RouterConfig
from app.workflow import plan_selection
from tests.api_client import authenticated_client


class ScriptedRouter(LiteLLMRouter):
    def __init__(self, reply: str) -> None:
        super().__init__(RouterConfig(), mock_mode=True)
        self.reply = reply
        self.calls = 0

    async def complete(self, messages, **kwargs):
        self.calls += 1
        return {"choices": [{"message": {"content": self.reply}}]}


@pytest.fixture
async def auto_env(test_sqlite_db, monkeypatch, tmp_path):
    monkeypatch.setattr(ConnectionFactory, "_connections", {})
    monkeypatch.setattr(ConnectionFactory, "_infos", {})
    monkeypatch.setenv("DE_PLAN_HISTORY", str(tmp_path / "plan_history.db"))
    plan_selection.reset_runtime()
    await ConnectionFactory.create(
        DatabaseType.SQLITE, {"path": test_sqlite_db}, db_id="auto"
    )
    router = ScriptedRouter("SELECT id FROM orders ORDER BY id")
    monkeypatch.setattr(query, "get_default_router", lambda: router)
    client = authenticated_client(create_app())
    yield client
    ConnectionFactory.close("auto")
    plan_selection.reset_runtime()


async def test_auto_plan_routes_complex_query_to_full_pipeline(auto_env):
    response = auto_env.post(
        "/api/v1/query",
        json={
            "nl_text": "统计每个地区的订单数并关联用户表",
            "database": "auto",
            "execute": False,
            "num_candidates": 1,
            "auto_plan": True,
        },
    )
    assert response.status_code == 200, response.text
    payload = response.json()

    assert payload["plan_id"] == "request_query"
    assert payload["plan_selection"]["layer"] == "L1"
    assert "selector: gensql_agentic" in payload["plan_selection"]["reason"]
    assert payload["plan_selection"]["auto"] is True
    assert payload["plan_selection"]["requested"] == "request_query"
    assert payload["plan_selection"]["alternatives"] == ["ez_query"]


async def test_auto_plan_routes_simple_list_and_executes(auto_env):
    response = auto_env.post(
        "/api/v1/query",
        json={
            "nl_text": "列出订单",
            "database": "auto",
            "execute": True,
            "num_candidates": 1,
            "auto_plan": True,
        },
    )
    payload = response.json()

    assert payload["plan_id"] == "ez_query"
    assert payload["plan_selection"]["layer"] == "L1"
    assert payload["execution"]["rows"]


async def test_second_request_reuses_history(auto_env):
    body = {
        "nl_text": "orders please",
        "database": "auto",
        "execute": False,
        "num_candidates": 1,
        "auto_plan": True,
    }

    first = auto_env.post("/api/v1/query", json=body).json()
    second = auto_env.post("/api/v1/query", json=body).json()

    assert first["plan_selection"]["layer"] == "default"
    assert second["plan_selection"]["layer"] == "L2"
    assert second["plan_id"] == first["plan_id"]


async def test_explicit_plan_is_not_reported_as_auto(auto_env):
    payload = auto_env.post(
        "/api/v1/query",
        json={
            "nl_text": "列出订单",
            "database": "auto",
            "execute": False,
            "num_candidates": 1,
            "plan_id": "gensql_agentic",
        },
    ).json()

    assert payload["plan_id"] == "gensql_agentic"
    assert payload["plan_selection"] == {}


async def test_catalog_marks_only_real_blockers(auto_env):
    catalog = auto_env.get("/api/v1/workflows").json()["plans"]
    executable = {plan["id"] for plan in catalog if plan["query_executable"]}
    blocked = {plan["id"]: plan["blocker"] for plan in catalog if not plan["query_executable"]}

    assert "metric_query" not in executable
    assert "metric_query" in blocked
    assert blocked["metric_query"]
    assert executable == {
        "chat_agentic",
        "explore",
        "ez_query",
        "gensql_agentic",
        "reflection",
        "request_query",
    }


@pytest.mark.parametrize("nl_text", ["统计每个地区的订单数并关联用户表", "列出订单"])
async def test_simulation_agrees_with_real_selection(auto_env, nl_text):
    simulated = auto_env.post(
        "/api/v1/workflows/simulate", json={"nl_text": nl_text}
    ).json()

    real = auto_env.post(
        "/api/v1/query",
        json={
            "nl_text": nl_text,
            "database": "auto",
            "execute": False,
            "num_candidates": 1,
            "auto_plan": True,
        },
    ).json()

    assert simulated["selected_workflow"] == real["plan_id"]
    assert simulated["layer"] == real["plan_selection"]["layer"]
    assert simulated["query_executable"] is True
