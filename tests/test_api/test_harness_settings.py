"""Harness status must describe actual policy, never acknowledge unwired writes."""

import copy
import os
import sqlite3
from dataclasses import asdict

import pytest
import yaml
from fastapi.testclient import TestClient

from app.api import create_app
from app.api import settings as api_settings
from app.auth.security import create_access_token
from app.config.settings import settings
from app.harness.config_loader import ConfigLoader
from app.harness.constraint import ConstraintEnforcer, ConstraintLimits
from app.harness.plan_loader import PlanLoader
from app.nodes.base import NodeInput
from app.nodes.execute_sql import ExecuteSQLNode
from app.workflow import query_service
from tests.api_client import authenticated_client


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(api_settings, "_get_store", lambda: None)
    monkeypatch.setattr(
        api_settings, "_settings_store", copy.deepcopy(api_settings._DEFAULT_SETTINGS)
    )
    return authenticated_client(create_app())


def test_unwired_harness_write_is_rejected(client):
    before = copy.deepcopy(api_settings._settings_store)
    response = client.put("/api/v1/settings/harness", json={"read_only": False})
    assert response.status_code == 409
    assert api_settings._settings_store == before


def test_status_uses_actual_policy_not_stored_settings(client):
    api_settings._settings_store["harness"].update(read_only=False, max_llm_calls=99)
    response = client.get("/api/v1/settings/harness")
    assert response.status_code == 200
    data = response.json()
    assert data["mode"] == "read_only"
    assert data["limits"]["read_only"]["value"] is True
    assert data["limits"]["max_llm_calls"]["value"] == 10
    assert data["limits"]["max_retries"]["value"] == 0


@pytest.mark.parametrize("payload", [{}, {"unknown": 1}, {"max_llm_calls": 1}, None])
def test_every_legacy_write_is_explicitly_rejected(client, payload):
    response = client.put("/api/v1/settings/harness", json=payload)
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "HARNESS_SETTINGS_READ_ONLY"


def test_rejected_write_never_reads_or_writes_settings_database(client, monkeypatch, tmp_path):
    connection = sqlite3.connect(tmp_path / "legacy.sqlite")
    connection.execute("CREATE TABLE settings(value TEXT)")
    connection.execute("INSERT INTO settings VALUES ('legacy-private-content')")
    connection.commit()
    accesses = []

    def forbidden_store_access():
        accesses.append(True)
        raise AssertionError("Harness status must not access settings storage")

    monkeypatch.setattr(api_settings, "_get_store", forbidden_store_access)
    try:
        assert client.get("/api/v1/settings/harness").status_code == 200
        assert client.put("/api/v1/settings/harness", json={"read_only": False}).status_code == 409
        assert accesses == []
        assert connection.execute("SELECT value FROM settings").fetchone()[0] == (
            "legacy-private-content"
        )
    finally:
        connection.close()


@pytest.fixture
def policy_files(tmp_path, monkeypatch):
    root = tmp_path / "app"
    plans = root / "workflow/plans"
    plans.mkdir(parents=True)
    config = root / "agent.yml"
    source_plan = query_service._ROOT / "workflow/plans/request_query.yml"
    plan = plans / "request_query.yml"
    plan.write_text(source_plan.read_text(encoding="utf-8"), encoding="utf-8")
    config.write_text("agent:\n  constraints:\n    max_llm_calls_per_query: 4\n", encoding="utf-8")
    monkeypatch.setattr(query_service, "_ROOT", root)
    monkeypatch.setattr(query_service, "_CONFIG_LOADER", ConfigLoader(str(config)))
    monkeypatch.setattr(query_service, "_PLAN_LOADER", PlanLoader(str(plans)))
    monkeypatch.setattr(query_service, "_PLAN_MTIME", -1)
    return config, plan


def rewrite(path, edit):
    stamp = path.stat().st_mtime_ns
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    edit(data)
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    os.utime(path, ns=(stamp + 1000000, stamp + 1000000))


def test_same_loaders_reread_config_and_plan(client, policy_files):
    config, plan = policy_files
    first = client.get("/api/v1/settings/harness").json()
    assert first["limits"]["max_llm_calls"]["value"] == 4
    assert first["workflow"]["max_iterations"] == 5
    rewrite(config, lambda data: data["agent"]["constraints"].update(max_llm_calls_per_query=2))
    rewrite(plan, lambda data: data["plan"].update(max_iterations=7))
    current = client.get("/api/v1/settings/harness").json()
    assert current["limits"]["max_llm_calls"]["value"] == 2
    assert current["workflow"]["max_iterations"] == 7
    enforcer = ConstraintEnforcer(
        ConstraintLimits(**asdict(query_service._CONFIG_LOADER.load().constraints))
    )
    assert enforcer.can_call_llm()
    enforcer.record_llm_call()
    enforcer.record_llm_call()
    assert not enforcer.can_call_llm()


@pytest.mark.parametrize("global_retries,plan_retries,expected", [(3, 0, 0), (1, 3, 1), (3, 1, 1)])
def test_retry_budget_is_minimum_of_plan_and_global(
    client, policy_files, global_retries, plan_retries, expected
):
    config, plan = policy_files
    rewrite(
        config, lambda data: data["agent"]["constraints"].update(max_node_retries=global_retries)
    )
    rewrite(plan, lambda data: data["plan"]["failure_policy"].update(max_retries=plan_retries))
    assert (
        client.get("/api/v1/settings/harness").json()["limits"]["max_retries"]["value"] == expected
    )


@pytest.mark.parametrize("path_index", [0, 1])
@pytest.mark.parametrize("failure", ["missing", "bad_yaml"])
def test_source_failure_reports_unknown_without_old_values(
    client, policy_files, path_index, failure
):
    path = policy_files[path_index]
    assert client.get("/api/v1/settings/harness").status_code == 200
    if failure == "missing":
        path.unlink()
    else:
        path.write_text("[malformed-private-content", encoding="utf-8")
    response = client.get("/api/v1/settings/harness")
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "HARNESS_POLICY_UNAVAILABLE"
    assert "limits" not in response.json()
    assert "private-content" not in response.text
    assert str(path) not in response.text


def test_settings_sources_and_units_are_separate(client, monkeypatch):
    monkeypatch.setattr(settings, "max_result_rows", 7)
    monkeypatch.setattr(settings, "statement_timeout_ms", 1700)
    monkeypatch.setattr(settings, "query_timeout_seconds", 9.5)
    monkeypatch.setattr(settings, "session_ttl_seconds", 81)
    monkeypatch.setattr(settings, "read_only", False)
    response = client.get("/api/v1/settings/harness").json()
    limits = response["limits"]
    assert limits["max_result_rows"]["value"] == 7
    assert limits["statement_timeout_ms"]["value"] == 1700
    assert limits["query_timeout_s"]["value"] == 9.5
    assert limits["session_ttl_s"]["value"] == 81
    assert limits["read_only"]["value"] is True
    assert all(isinstance(entry["source"], str) and entry["source"] for entry in limits.values())
    assert response["editable_fields"] == []
    assert response["legacy_stored_settings_applied"] is False
    assert (
        "direct SQL" in response["direct_sql_scope"] or "Direct SQL" in response["direct_sql_scope"]
    )
    assert [node["node"] for node in response["workflow"]["node_order"]] == [
        "parse_nl",
        "schema_linking",
        "generate_sql",
        "validate_sql",
        "execute_sql",
    ]


@pytest.mark.parametrize("global_rows,env_rows,expected", [(2, 7, 2), (7, 2, 2)])
async def test_reported_row_cap_matches_real_sqlite_execution(
    client, policy_files, monkeypatch, global_rows, env_rows, expected
):
    rewrite(
        policy_files[0],
        lambda data: data["agent"]["constraints"].update(max_result_rows=global_rows),
    )
    monkeypatch.setattr(settings, "max_result_rows", env_rows)
    policy = await api_settings.get_harness_settings()
    connection = sqlite3.connect(":memory:", check_same_thread=False)
    try:
        connection.executescript("CREATE TABLE t(n); INSERT INTO t VALUES (1),(2),(3),(4);")
        output = await ExecuteSQLNode().execute(
            NodeInput(
                query_text="SELECT n FROM t",
                context={"sql": "SELECT n FROM t", "connection": connection},
                config={"max_rows": global_rows},
            )
        )
        assert output.metadata["status"] == "success"
        assert (
            output.result["row_count"] == policy["limits"]["max_result_rows"]["value"] == expected
        )
        blocked = await ExecuteSQLNode().execute(
            NodeInput(
                query_text="DELETE FROM t",
                context={"sql": "DELETE FROM t", "connection": connection},
            )
        )
        assert blocked.metadata["status"] == "blocked"
        assert connection.execute("SELECT COUNT(*) FROM t").fetchone()[0] == 4
    finally:
        connection.close()


async def test_statement_cap_matches_real_sqlite_interrupt(client, policy_files, monkeypatch):
    rewrite(
        policy_files[0],
        lambda data: data["agent"]["constraints"].update(statement_timeout_ms=15, read_only=False),
    )
    monkeypatch.setattr(settings, "statement_timeout_ms", 1)
    monkeypatch.setattr(settings, "read_only", False)
    policy = await api_settings.get_harness_settings()
    assert policy["limits"]["statement_timeout_ms"]["value"] == 1
    assert policy["limits"]["read_only"]["value"] is True
    connection = sqlite3.connect(":memory:", check_same_thread=False)
    sql = (
        "WITH RECURSIVE numbers(n) AS (SELECT 1 UNION ALL SELECT n+1 FROM numbers "
        "WHERE n<10000000) SELECT SUM(n) FROM numbers"
    )
    try:
        output = await ExecuteSQLNode().execute(
            NodeInput(
                query_text=sql,
                context={"sql": sql, "connection": connection},
                config={"timeout_ms": 15},
            )
        )
        assert output.metadata["status"] == "execution_error"
        assert any("interrupted" in error for error in output.errors)
        assert connection.execute("SELECT 1").fetchone()[0] == 1
        blocked = await ExecuteSQLNode().execute(
            NodeInput(
                query_text="CREATE TABLE forbidden(n)",
                context={"sql": "CREATE TABLE forbidden(n)", "connection": connection},
            )
        )
        assert blocked.metadata["status"] == "blocked"
    finally:
        connection.close()


def test_aggregate_settings_cannot_reintroduce_legacy_values(client):
    api_settings._settings_store["harness"] = {"read_only": False, "private": "legacy-only"}
    response = client.get("/api/v1/settings").json()["settings"]["harness"]
    assert response == client.get("/api/v1/settings/harness").json()
    assert "legacy-only" not in str(response)


@pytest.mark.parametrize("method", ["get", "put"])
@pytest.mark.parametrize("user_id,expected", [(None, 401), (999999, 403)])
def test_harness_status_and_write_keep_operator_boundary(client, method, user_id, expected):
    headers = (
        {}
        if user_id is None
        else {"Authorization": f"Bearer {create_access_token(user_id, 'non-operator')}"}
    )
    response = getattr(TestClient(create_app()), method)(
        "/api/v1/settings/harness", headers=headers
    )
    assert response.status_code == expected
