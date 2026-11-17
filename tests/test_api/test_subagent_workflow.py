"""REST subagent scope and safety, exercised through the real SQLite Harness."""

import json
import os
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.api import create_app, query
from app.auth.security import create_access_token
from app.config.settings import settings
from app.db.connections import ConnectionFactory, DatabaseType
from app.models.subagent import AccessControl, SubagentConfig, SubagentContext
from app.subagent.instance import SubagentStatus
from app.subagent.manager import SubagentManager
from tests.api_client import authenticated_client
from tests.test_api.test_plan_execution import ScriptedRouter

PATH = "/api/v1/subagents/scoped/query"


@pytest.fixture
async def scoped_env(test_sqlite_db, tmp_path, monkeypatch):
    from app.subagent import manager

    monkeypatch.setattr(ConnectionFactory, "_connections", {})
    monkeypatch.setattr(ConnectionFactory, "_infos", {})
    monkeypatch.setenv("DE_TRACE_DIR", str(tmp_path / "traces"))
    monkeypatch.setenv("DE_PLAN_HISTORY", str(tmp_path / "plans.db"))
    registry = SubagentManager(str(tmp_path))
    monkeypatch.setattr(manager, "subagent_manager", registry)
    config = SubagentConfig(
        name="scoped", domain="ecommerce", capabilities=["nl_query"],
        context=SubagentContext(domain="ecommerce", databases=[{"db_id": "scoped-db"}]),
        access_control=AccessControl(allowed_users=["1"]),
    )
    inst = registry.register(config)
    await inst.initialize()
    await ConnectionFactory.create(
        DatabaseType.SQLITE, {"path": test_sqlite_db}, db_id="scoped-db"
    )
    model = ScriptedRouter(["SELECT id FROM orders ORDER BY id"])
    monkeypatch.setattr(query, "get_default_router", lambda: model)
    app = create_app()
    yield authenticated_client(app), config, model, inst, app, test_sqlite_db
    await ConnectionFactory.aclose("scoped-db")
    await registry.deactivate_all()


async def test_fixed_workflow_reads_real_rows_and_persists_trace(scoped_env):
    client, _, model, _, _, _ = scoped_env
    response = client.post(PATH, json={"nl_text": "list order ids", "execute": True})
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["execution_backend"] == "workflow_runner"
    assert payload["status"] == "completed"
    assert payload["data"]["execution"]["rows"] == [[1], [2], [3], [4], [5]]
    assert payload["data"]["plan_id"] == "request_query"
    assert payload["data"]["plan_output"]["node_names"] == [
        "parse_nl", "schema_linking", "generate_sql", "validate_sql", "execute_sql",
    ]
    assert payload["data"]["llm_calls"] == len(model.calls) == 1
    # workflow_ids identify persisted Harness traces, not the separate UI summary IDs.
    trace = Path(os.environ["DE_TRACE_DIR"]) / f"{payload['trace_id']}.jsonl"
    lines = [json.loads(line) for line in trace.read_text(encoding="utf-8").splitlines()]
    assert lines[0]["workflow_id"] == payload["trace_id"]
    assert lines[0]["status"] == "completed"
    assert [line["node_name"] for line in lines[1:]] == payload["data"]["plan_output"]["node_names"]


async def test_generation_defaults_to_no_execution(scoped_env):
    client, *_ = scoped_env
    payload = client.post(PATH, json={"nl_text": "list orders"}).json()
    assert payload["status"] == "completed"
    assert payload["data"]["execution"] is None


@pytest.mark.parametrize("extra, status", [
    ({"domain": "finance"}, 403),
    ({"database": "another-db"}, 403),
    ({"context": {"domain": "finance"}}, 400),
    ({"context": {"sql": "DELETE FROM orders"}}, 400),
    ({"context": {"databases": ["another-db"]}}, 400),
])
async def test_overrides_rejected_before_model_call(scoped_env, extra, status):
    client, _, model, *_ = scoped_env
    response = client.post(PATH, json={"nl_text": "orders", **extra})
    assert response.status_code == status
    assert model.calls == []


@pytest.mark.parametrize("setting, body", [
    ("enable_execution", {"execute": True}),
    ("enable_multi_candidate", {"num_candidates": 2}),
])
async def test_configuration_disables_execution_and_multiple_candidates(scoped_env, setting, body):
    client, config, model, *_ = scoped_env
    setattr(config.context, setting, False)
    assert client.post(PATH, json={"nl_text": "orders", **body}).status_code == 403
    assert model.calls == []


async def test_unbound_disconnected_and_inactive_fail_before_query(scoped_env):
    client, config, model, inst, *_ = scoped_env
    config.context.databases = [{"alias": "main_db", "connection_ref": "connections/ecommerce_pg"}]
    assert client.post(PATH, json={"nl_text": "orders"}).status_code == 409
    config.context.databases = [{"db_id": "disconnected"}]
    assert client.post(PATH, json={"nl_text": "orders"}).status_code == 409
    inst.status = SubagentStatus.INACTIVE
    assert client.post(PATH, json={"nl_text": "orders"}).status_code == 409
    assert model.calls == []


async def test_non_nl_capability_is_not_claimed_executable(scoped_env):
    client, config, model, *_ = scoped_env
    config.capabilities = ["metric_query"]
    assert client.post(PATH, json={"nl_text": "revenue"}).status_code == 409
    assert model.calls == []


async def test_acl_filters_listing_details_and_execution(scoped_env):
    client, config, model, *_ = scoped_env
    config.access_control.allowed_users = ["2"]
    assert client.get("/api/v1/subagents").json()["subagents"] == []
    assert client.get("/api/v1/subagents/scoped").status_code == 403
    assert client.post(PATH, json={"nl_text": "orders"}).status_code == 403
    assert model.calls == []


async def test_authentication_and_operator_gate_still_required(scoped_env):
    _, _, model, _, app, _ = scoped_env
    client = TestClient(app)
    assert client.post(PATH, json={"nl_text": "orders"}).status_code == 401
    client.headers["Authorization"] = f"Bearer {create_access_token(99, 'outsider')}"
    assert client.post(PATH, json={"nl_text": "orders"}).status_code == 403
    assert model.calls == []


async def test_main_row_cap_and_read_only_policy_apply(scoped_env, monkeypatch):
    client, _, model, _, _, database = scoped_env
    monkeypatch.setattr(settings, "max_result_rows", 2)
    result = client.post(PATH, json={"nl_text": "orders", "execute": True}).json()
    assert result["data"]["execution"]["row_count"] == 2
    model.replies = ["DELETE FROM orders"]
    result = client.post(PATH, json={"nl_text": "orders", "execute": True}).json()
    assert result["status"] == "failed"
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT COUNT(*) FROM orders").fetchone()[0] == 5


async def test_main_llm_budget_applies_before_provider_call(scoped_env, monkeypatch):
    from app.workflow.query_service import _CONFIG_LOADER

    client, _, model, *_ = scoped_env
    monkeypatch.setattr(_CONFIG_LOADER.load().constraints, "max_llm_calls_per_query", 0)
    response = client.post(PATH, json={"nl_text": "orders", "execute": True})
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "failed"
    assert payload["data"]["llm_calls"] == 0
    assert model.calls == []
