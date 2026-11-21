"""End-to-end metric requests: fixed YAML, real SQLite, no provider calls."""

import hashlib
import json
import os
import sqlite3
from pathlib import Path

import pytest
import yaml

from app.api import create_app, query
from app.config.settings import settings
from app.db.connections import ConnectionFactory, DatabaseType
from app.workflow.query_service import _CONFIG_LOADER
from tests.api_client import authenticated_client
from tests.test_api.test_plan_execution import ScriptedRouter, stream_events

FIXTURE = Path("tests/fixtures/metric_catalog.yml")


@pytest.fixture
async def metric_env(test_sqlite_db, tmp_path, monkeypatch):
    path = tmp_path / "metrics.yml"
    path.write_bytes(FIXTURE.read_bytes())
    monkeypatch.setattr(settings, "metric_catalog_path", str(path))
    monkeypatch.setattr(ConnectionFactory, "_connections", {})
    monkeypatch.setattr(ConnectionFactory, "_infos", {})
    monkeypatch.setenv("DE_TRACE_DIR", str(tmp_path / "traces"))
    monkeypatch.setenv("DE_PLAN_HISTORY", str(tmp_path / "history.db"))
    monkeypatch.setattr(query, "_SESSION_STORE", {})
    await ConnectionFactory.create(DatabaseType.SQLITE, {"path": test_sqlite_db}, "metric-db")
    router = ScriptedRouter(["DELETE FROM orders"])
    monkeypatch.setattr(query, "get_default_router", lambda: router)
    client = authenticated_client(create_app())
    yield client, router, path, test_sqlite_db
    await ConnectionFactory.aclose("metric-db")


def body(**overrides):
    result = {
        "nl_text": "report approved revenue", "plan_id": "metric_query",
        "database": "metric-db", "domain": "ecommerce", "num_candidates": 1,
        "metrics": ["revenue", "order_count"], "metric_catalog_version": "synthetic-ecommerce-v1",
    }
    result.update(overrides)
    return result


async def test_aggregate_results_and_zero_llm_calls(metric_env, monkeypatch):
    client, router, _, database = metric_env
    monkeypatch.setattr(_CONFIG_LOADER.load().constraints, "max_llm_calls_per_query", 0)
    response = client.post("/api/v1/query", json=body(execute=True, metric_dimensions=["region"]))
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["status"] == "completed", payload
    with sqlite3.connect(database) as connection:
        expected = [list(row) for row in connection.execute(
            "SELECT region, SUM(amount), COUNT(*) FROM orders GROUP BY region ORDER BY region"
        )]
    assert payload["execution"]["rows"] == expected
    assert payload["validation"]["passed"] is True
    assert payload["llm_calls"] == 0 and router.calls == []
    assert payload["candidates"][0]["generation_source"] == "prepared"
    assert payload["plan_output"]["node_names"] == ["metric_resolve", "validate_sql", "execute_sql"]
    catalog = payload["plan_output"]["metric_catalog"]
    assert catalog["version"] == "synthetic-ecommerce-v1" and len(catalog["sha256"]) == 64
    trace = Path(os.environ["DE_TRACE_DIR"]) / f"{payload['workflow_ids'][0]}.jsonl"
    rows = [json.loads(line) for line in trace.read_text(encoding="utf-8").splitlines()]
    assert [row["node_name"] for row in rows[1:]] == payload["plan_output"]["node_names"]
    assert not any("MOCK LLM" in warning for warning in payload["warnings"])


async def test_v2_calendar_and_lookup_preserve_facts_and_require_live_primary_key(metric_env):
    client, router, path, database = metric_env
    with sqlite3.connect(database) as connection:
        connection.executescript("""
            CREATE TABLE metric_facts (booked DATE, amount INTEGER, branch_id INTEGER);
            CREATE TABLE metric_branches (id INTEGER PRIMARY KEY, sector TEXT);
            CREATE TABLE duplicated_branches (id INTEGER, sector TEXT);
            INSERT INTO metric_branches VALUES (1, 'Retail'), (2, 'Retail');
            INSERT INTO duplicated_branches VALUES (1, 'Retail'), (1, 'Wholesale');
            INSERT INTO metric_facts VALUES ('2025-12-28', 5, 1), ('2025-12-29', 10, 1),
              ('2026-01-04', 15, 2), ('2026-01-05', 20, NULL), (NULL, 25, 99);
        """)
    definition = {
        "format": "metric-catalog-v2", "version": "calendar-v2", "bindings": [{
            "domain": "ecommerce", "database": "metric-db",
            "metrics": [{"name": "revenue", "table": "metric_facts", "column": "amount",
                         "aggregation": "sum", "dimensions": ["sector"], "date_column": "booked"}],
            "lookup": {"fact_table": "metric_facts", "fact_key": "branch_id",
                       "table": "metric_branches", "key": "id", "dimensions": {"sector": "sector"}},
        }],
    }
    path.write_text(yaml.safe_dump(definition), encoding="utf-8")
    request = body(execute=True, metrics=["revenue"], metric_catalog_version="calendar-v2",
                   metric_dimensions=["sector"], metric_grain="week")
    payload = client.post("/api/v1/query", json=request).json()
    assert payload["status"] == "completed", payload
    result = payload["execution"]
    assert result["columns"] == ["period_start", "sector", "revenue"]
    assert result["rows"] == [
        [None, None, 25], ["2025-12-22", "Retail", 5], ["2025-12-29", "Retail", 25],
        ["2026-01-05", None, 20],
    ]
    assert payload["plan_output"]["metric_catalog"]["grain"] == "week"
    done = next(data for event, data in stream_events(client.post(
        "/api/v1/query/stream", json={**request, "metric_grain": "month"},
    )) if event == "done")
    assert done["execution"]["rows"] == [
        [None, None, 25], ["2025-12-01", "Retail", 15],
        ["2026-01-01", None, 20], ["2026-01-01", "Retail", 15],
    ]
    definition["bindings"][0]["lookup"]["table"] = "duplicated_branches"
    definition["version"] = "invalid-lookup"
    path.write_text(yaml.safe_dump(definition), encoding="utf-8")
    rejected = client.post("/api/v1/query", json={
        **request, "metric_catalog_version": "invalid-lookup",
    }).json()
    assert rejected["status"] == "failed" and rejected["execution"] is None
    assert router.calls == []


async def test_inactive_shared_runtime_rejects_metric_access_before_session(
    metric_env, monkeypatch,
):
    client, router, catalog, _ = metric_env
    monkeypatch.setattr(settings, "shared_worker_release", str(catalog.with_name("release.json")))
    responses = [
        client.get("/api/v1/metrics", params={"domain": "ecommerce", "database": "metric-db"}),
        client.post("/api/v1/query", json=body(execute=True)),
    ]
    for response in responses:
        assert response.status_code == 503, response.text
        assert response.json()["detail"]["code"] == "SHARED_STATE_UNAVAILABLE"
    stream = client.post("/api/v1/query/stream", json=body(execute=True))
    assert stream.status_code == 200
    assert 'event: error' in stream.text and '"code": "SHARED_STATE_UNAVAILABLE"' in stream.text
    assert 'event: done' not in stream.text
    assert router.calls == [] and query._SESSION_STORE == {}


async def test_no_execute_still_validates_and_row_cap_applies(metric_env, monkeypatch):
    client, router, *_ = metric_env
    payload = client.post("/api/v1/query", json=body(run_validation=False)).json()
    assert payload["status"] == "completed" and payload["execution"] is None
    assert payload["validation"]["passed"] is True
    monkeypatch.setattr(settings, "max_result_rows", 1)
    payload = client.post("/api/v1/query", json=body(
        execute=True, metric_dimensions=["region"], metric_filters={"status": "completed"}
    )).json()
    assert payload["status"] == "completed"
    assert payload["execution"]["row_count"] == 1
    assert payload["execution"]["truncated"] is True
    assert router.calls == []


@pytest.mark.parametrize("overrides", [
    {"metrics": []}, {"metrics": ["unknown"]}, {"domain": "finance"}, {"database": "other"},
    {"database": ""}, {"metric_catalog_version": ""}, {"metric_catalog_version": "old"},
    {"metric_dimensions": ["amount"]}, {"metric_filters": {"amount": "1"}},
    {"metrics": ["revenue", "product_count"]}, {"link_schema": False},
    {"schema_notes": "override"}, {"num_candidates": 2},
    {"plan_id": "request_query"}, {"auto_plan": True},
])
async def test_invalid_selection_rejected_before_session_or_provider(metric_env, overrides):
    client, router, *_ = metric_env
    response = client.post("/api/v1/query", json=body(**overrides))
    assert response.status_code == 400, response.text
    assert response.json()["detail"]["code"] == "INVALID_REQUEST"
    assert router.calls == [] and query._SESSION_STORE == {}


async def test_schema_mismatch_blocks_execution(metric_env):
    client, router, path, _ = metric_env
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    raw["bindings"][0]["metrics"][0]["column"] = "missing_amount"
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    payload = client.post("/api/v1/query", json=body(execute=True)).json()
    assert payload["status"] == "failed"
    assert payload["execution"] is None
    assert router.calls == []


async def test_injection_is_data_and_cannot_write(metric_env):
    client, router, _, database = metric_env
    payload = client.post("/api/v1/query", json=body(
        execute=True, metric_filters={"region": "East' OR 1=1; DELETE FROM orders;"}
    )).json()
    assert payload["status"] == "completed", payload
    assert payload["execution"]["rows"] == [[None, 0]]
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT COUNT(*) FROM orders").fetchone()[0] == 5
    assert router.calls == []


async def test_json_sse_and_runtime_catalog_state_match(metric_env, monkeypatch):
    client, router, _, _ = metric_env
    payload = client.post("/api/v1/query", json=body(execute=True)).json()
    events = stream_events(client.post("/api/v1/query/stream", json=body(execute=True)))
    done = next(data for event, data in events if event == "done")
    sql = next(data for event, data in events if event == "sql")
    assert sql["primary_sql"] == payload["primary_sql"] == done["primary_sql"]
    assert done["execution"] == payload["execution"]
    start = next(data for event, data in events if event == "workflow_start")
    assert start["stages"] == ["resolve", "validate", "execute"]
    def state():
        plans = client.get("/api/v1/workflows").json()["plans"]
        return next(p for p in plans if p["id"] == "metric_query")
    assert state()["query_executable"] is True
    monkeypatch.setattr(settings, "metric_catalog_path", "")
    assert state()["query_executable"] is False
    assert client.post("/api/v1/query", json=body()).status_code == 400
    assert router.calls == []


async def test_version_changes_and_permission_default_deny(metric_env, monkeypatch):
    client, router, path, _ = metric_env
    raw = path.read_text(encoding="utf-8").replace(
        "synthetic-ecommerce-v1", "synthetic-ecommerce-v2"
    )
    path.write_text(raw, encoding="utf-8")
    assert client.post("/api/v1/query", json=body()).status_code == 400
    payload = client.post("/api/v1/query", json=body(
        execute=True, metric_catalog_version="synthetic-ecommerce-v2"
    )).json()
    assert payload["status"] == "completed"
    config = _CONFIG_LOADER.load().nodes["execute_sql"]
    monkeypatch.setattr(config, "permissions", {})
    payload = client.post("/api/v1/query", json=body(
        execute=True, metric_catalog_version="synthetic-ecommerce-v2"
    )).json()
    assert payload["status"] == "failed" and payload["execution"] is None
    assert router.calls == []


async def test_in_flight_request_keeps_pre_admission_catalog_snapshot(metric_env):
    from app.workflow.query_service import run_query

    _, router, path, database = metric_env
    old_hash = hashlib.sha256(path.read_bytes()).hexdigest()

    async def change_catalog(event, data):
        if event == "workflow_start":
            raw = path.read_text(encoding="utf-8").replace("synthetic-ecommerce-v1", "new")
            path.write_text(raw.replace("aggregation: sum", "aggregation: max"), encoding="utf-8")

    result = await run_query(
        query.QueryRequest(**body(execute=True)), change_catalog, llm_router=router
    )
    assert result["status"] == "completed"
    assert result["plan_output"]["metric_catalog"]["sha256"] == old_hash
    with sqlite3.connect(database) as connection:
        expected = connection.execute("SELECT SUM(amount) FROM orders").fetchone()[0]
    assert result["execution"]["rows"][0][0] == expected
    assert router.calls == []


async def test_invalid_catalog_is_not_reported_as_executable(metric_env):
    client, router, path, _ = metric_env
    path.write_text("version: [malformed", encoding="utf-8")
    response = client.post("/api/v1/query", json=body())
    assert response.status_code == 400
    assert query._SESSION_STORE == {} and router.calls == []
    plans = client.get("/api/v1/workflows").json()["plans"]
    assert next(p for p in plans if p["id"] == "metric_query")["query_executable"] is False


def test_immutable_worker_metric_gate_fails_closed(monkeypatch):
    from app.workflow.plan_runtime import plan_blocker

    monkeypatch.setattr(settings, "shared_worker_release", "sealed-release.json")
    assert "runtime is not active" in plan_blocker("metric_query")


@pytest.mark.parametrize("extra", [
    {"metric_time_grain": "month"}, {"metric_catalog_path": "untrusted.yml"},
    {"formula": "DELETE FROM orders"}, {"sql": "DELETE FROM orders"},
])
async def test_unsupported_options_are_rejected_instead_of_ignored(metric_env, extra):
    client, router, *_ = metric_env
    response = client.post("/api/v1/query", json=body(**extra))
    assert response.status_code == 422
    assert query._SESSION_STORE == {} and router.calls == []
