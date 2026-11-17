"""The query API executes plan templates with the same limits as request_query."""

from __future__ import annotations

import json

import pytest

from app.api import create_app, query
from app.db.connections import ConnectionFactory, DatabaseType
from app.llm.router import LiteLLMRouter, RouterConfig
from app.workflow import plan_selection
from tests.api_client import authenticated_client


class ScriptedRouter(LiteLLMRouter):
    """Returns scripted completions and records every prompt."""

    def __init__(self, replies: list[str]) -> None:
        super().__init__(RouterConfig(), mock_mode=True)
        self.replies = list(replies)
        self.calls: list[list[dict[str, str]]] = []

    async def complete(self, messages, **kwargs):
        self.calls.append(messages)
        text = self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]
        return {"choices": [{"message": {"content": text}}]}


def stream_events(response):
    events = []
    event = ""
    for line in response.text.splitlines():
        if line.startswith("event:"):
            event = line[6:].strip()
        elif line.startswith("data:"):
            events.append((event, json.loads(line[5:])))
    return events


@pytest.fixture
async def plan_env(test_sqlite_db, monkeypatch, tmp_path):
    monkeypatch.setattr(ConnectionFactory, "_connections", {})
    monkeypatch.setattr(ConnectionFactory, "_infos", {})
    monkeypatch.setenv("DE_PLAN_HISTORY", str(tmp_path / "plan_history.db"))
    plan_selection.reset_runtime()
    await ConnectionFactory.create(
        DatabaseType.SQLITE, {"path": test_sqlite_db}, db_id="plan"
    )

    app = create_app()

    def install(router: ScriptedRouter):
        monkeypatch.setattr(query, "get_default_router", lambda: router)
        return authenticated_client(app)

    yield install
    ConnectionFactory.close("plan")
    plan_selection.reset_runtime()


def _body(**overrides):
    values = {
        "nl_text": "list every order id",
        "database": "plan",
        "execute": True,
        "num_candidates": 1,
    }
    values.update(overrides)
    return values


async def test_ez_query_skips_validation_but_still_executes(plan_env):
    router = ScriptedRouter(["SELECT id FROM orders ORDER BY id"])
    client = plan_env(router)

    response = client.post("/api/v1/query", json=_body(plan_id="ez_query"))
    assert response.status_code == 200, response.text
    payload = response.json()

    assert payload["plan_id"] == "ez_query"
    assert payload["status"] == "completed"
    assert payload["execution"]["rows"]
    assert payload["validation"] is None
    assert payload["plan_output"]["node_names"] == [
        "schema_linking",
        "generate_sql",
        "execute_sql",
    ]
    assert any("不包含校验节点" in warning for warning in payload["warnings"])


async def test_gensql_plan_runs_validation_before_execution(plan_env):
    router = ScriptedRouter(["SELECT id FROM orders ORDER BY id"])
    client = plan_env(router)

    payload = client.post(
        "/api/v1/query", json=_body(plan_id="gensql_agentic")
    ).json()

    assert payload["status"] == "completed"
    assert payload["plan_id"] == "gensql_agentic"
    assert payload["validation"]["passed"] is True
    assert payload["plan_output"]["node_names"] == [
        "schema_linking",
        "generate_sql",
        "validate_sql",
        "execute_sql",
    ]


async def test_reflection_plan_revises_after_execution_error(plan_env):
    router = ScriptedRouter(
        ["SELECT missing_column FROM orders", "SELECT id FROM orders ORDER BY id"]
    )
    client = plan_env(router)

    payload = client.post("/api/v1/query", json=_body(plan_id="reflection")).json()

    assert payload["plan_id"] == "reflection"
    assert payload["status"] == "completed", json.dumps(payload, ensure_ascii=False)
    assert payload["execution"]["rows"]
    assert len(router.calls) >= 2

    traces = client.get("/api/v1/workflows/traces").json()["traces"]
    trace = next(item for item in traces if item["plan"] == "reflection")
    assert "reflection" in trace["nodes_executed"]
    assert trace["nodes_executed"].count("generate_sql") >= 2


async def test_chat_plan_returns_natural_language_response(plan_env):
    router = ScriptedRouter(["SELECT id FROM orders", "查询共返回订单编号列表。"])
    client = plan_env(router)

    payload = client.post(
        "/api/v1/query",
        json=_body(plan_id="chat_agentic", execute=False, run_validation=True),
    ).json()

    assert payload["status"] == "completed"
    assert payload["plan_output"]["response"] == "查询共返回订单编号列表。"
    assert payload["plan_output"]["node_names"] == [
        "parse_nl",
        "hybrid_search",
        "generate_sql",
        "validate_sql",
        "respond",
    ]


async def test_explore_plan_returns_search_summary_without_sql(plan_env):
    router = ScriptedRouter(["unused"])
    client = plan_env(router)

    payload = client.post(
        "/api/v1/query",
        json=_body(plan_id="explore", execute=False, link_schema=False),
    ).json()

    assert payload["status"] == "completed"
    assert payload["candidates"] == []
    assert payload["primary_sql"] is None
    assert payload["plan_output"]["search"]["target"] == "all"


async def test_unknown_plan_is_a_client_error(plan_env):
    client = plan_env(ScriptedRouter(["SELECT 1"]))

    response = client.post("/api/v1/query", json=_body(plan_id="not_a_plan"))

    assert response.status_code == 400
    detail = response.json()["detail"]
    assert detail["code"] == "INVALID_REQUEST"
    assert "not_a_plan" in detail["message"]


async def test_blocked_plan_reports_the_reason(plan_env):
    client = plan_env(ScriptedRouter(["SELECT 1"]))

    response = client.post("/api/v1/query", json=_body(plan_id="metric_query"))

    assert response.status_code == 400
    detail = response.json()["detail"]
    assert detail["code"] == "INVALID_REQUEST"
    assert "metric_query" in detail["message"]
    assert "不可执行" in detail["message"]


async def test_reflection_requires_execution_and_database(plan_env):
    client = plan_env(ScriptedRouter(["SELECT 1"]))

    response = client.post(
        "/api/v1/query", json=_body(plan_id="reflection", execute=False)
    )

    assert response.status_code == 400
    assert "execute" in response.json()["detail"]["message"]


async def test_sse_reports_the_selected_plan(plan_env):
    client = plan_env(ScriptedRouter(["SELECT id FROM orders ORDER BY id"]))

    response = client.post("/api/v1/query/stream", json=_body(plan_id="ez_query"))
    assert response.status_code == 200

    events = stream_events(response)
    done = next(value for event, value in events if event == "done")
    assert done["plan_id"] == "ez_query"

    start = next(value for event, value in events if event == "workflow_start")
    assert start["plan_id"] == "ez_query"
    assert start["stages"] == ["retrieve", "generate", "execute"]


async def test_default_request_still_reports_request_query(plan_env):
    client = plan_env(ScriptedRouter(["SELECT id FROM orders ORDER BY id"]))

    payload = client.post("/api/v1/query", json=_body()).json()

    assert payload["plan_id"] == "request_query"
    assert payload["plan_selection"] == {}
    assert payload["plan_output"]["node_names"] == [
        "parse_nl",
        "schema_linking",
        "generate_sql",
        "validate_sql",
        "execute_sql",
    ]
