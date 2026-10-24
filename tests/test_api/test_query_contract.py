"""JSON/SSE use identical real SQLite execution and session semantics."""

import json
from types import SimpleNamespace

import pytest

from app.api import create_app, query
from app.db.connections import ConnectionFactory, DatabaseType
from app.db.schema_extractor import SchemaExtractor
from app.llm.router import LiteLLMRouter, RouterConfig
from tests.api_client import authenticated_client


@pytest.fixture
async def connected_query(test_sqlite_db, monkeypatch):
    monkeypatch.setattr(ConnectionFactory, "_connections", {})
    monkeypatch.setattr(ConnectionFactory, "_infos", {})
    await ConnectionFactory.create(DatabaseType.SQLITE, {"path": test_sqlite_db}, db_id="contract")

    class ScriptedRouter(LiteLLMRouter):
        async def complete(self, messages, **kwargs):
            return {"choices": [{"message": {"content": "SELECT id FROM orders ORDER BY id"}}]}

    monkeypatch.setattr(
        query, "get_default_router", lambda: ScriptedRouter(RouterConfig(), mock_mode=True)
    )
    yield authenticated_client(create_app())
    ConnectionFactory.close("contract")


@pytest.mark.parametrize("protocol", ["json", "sse", "mcp"])
async def test_connection_replacement_never_executes_old_schema_on_new_database(
    connected_query, monkeypatch, protocol,
):
    original_extract = SchemaExtractor.cached_extract
    replaced = False

    async def replace_after_extract(cls, connection, *args, **kwargs):
        nonlocal replaced
        snapshot = await original_extract(connection, *args, **kwargs)
        if not replaced:
            replaced = True
            new = await ConnectionFactory.create(DatabaseType.SQLITE, {}, "contract")
            new.execute("CREATE TABLE orders (id INTEGER)")
            new.execute("INSERT INTO orders VALUES (999)")
        return snapshot

    monkeypatch.setattr(SchemaExtractor, "cached_extract", classmethod(replace_after_extract))
    body = {"nl_text": "find all orders", "database": "contract", "execute": True,
            "num_candidates": 1}

    async def request():
        if protocol == "mcp":
            return await mcp_query(nl_text="find all orders", db_id="contract")
        if protocol == "sse":
            events = stream_events(connected_query.post("/api/v1/query/stream", json=body))
            if first:
                assert not any(event == "execution" for event, _ in events)
            return next(value for event, value in events if event == "done")
        response = connected_query.post("/api/v1/query", json=body)
        assert response.status_code == 200
        return response.json()

    first = True
    failed = await request()
    assert failed["status"] == "failed", failed
    assert "Connection target changed" in str(failed)
    if protocol == "mcp":
        assert failed["rows"] == []
        assert failed["error_code"] == "WORKFLOW_FAILED"
    else:
        assert failed["execution"] is None
    # A new request may explicitly use the replacement; the old one cannot migrate.
    first = False
    current = await request()
    assert current["status"] == "completed", current
    execution = current if protocol == "mcp" else current["execution"]
    assert execution["rows"] == [[999]]


@pytest.mark.parametrize("protocol", ["json", "sse", "mcp"])
@pytest.mark.parametrize("change", ["ddl", "temp"])
async def test_sqlite_source_changes_fail_without_execution_then_fresh_query_recovers(
    connected_query, monkeypatch, protocol, change,
):
    changed = False

    class ChangingRouter(LiteLLMRouter):
        async def complete(self, messages, **kwargs):
            nonlocal changed
            if not changed:
                changed = True
                connection = ConnectionFactory.get("contract")
                if change == "ddl":
                    connection.execute("ALTER TABLE orders ADD COLUMN source_revision_fixture TEXT")
                else:
                    connection.execute("CREATE TEMP TABLE orders (id INT)")
                    connection.execute("INSERT INTO temp.orders VALUES (999)")
            return {"choices": [{"message": {"content": "SELECT id FROM orders ORDER BY id"}}]}

    monkeypatch.setattr(
        query, "get_default_router", lambda: ChangingRouter(RouterConfig(), mock_mode=True),
    )
    body = {"nl_text": "find all orders", "database": "contract", "execute": True,
            "num_candidates": 1}

    async def request():
        if protocol == "mcp":
            return await mcp_query(nl_text="find all orders", db_id="contract")
        if protocol == "sse":
            events = stream_events(connected_query.post("/api/v1/query/stream", json=body))
            done = next(value for event, value in events if event == "done")
            if done["status"] == "failed":
                assert not any(event == "execution" for event, _ in events)
            return done
        response = connected_query.post("/api/v1/query", json=body)
        assert response.status_code == 200
        return response.json()

    failed = await request()
    assert failed["status"] == "failed", failed
    if change == "ddl":
        assert "Schema source changed" in str(failed)
    if protocol == "mcp":
        assert failed["rows"] == [] and failed["error_code"] == "WORKFLOW_FAILED"
    else:
        assert failed["execution"] is None
    if change == "temp":
        ConnectionFactory.get("contract").execute("DROP TABLE temp.orders")
    recovered = await request()
    assert recovered["status"] == "completed", recovered
    execution = recovered if protocol == "mcp" else recovered["execution"]
    assert execution["rows"] == [[1], [2], [3], [4], [5]]


@pytest.mark.parametrize("protocol", ["json", "sse", "mcp"])
@pytest.mark.parametrize("change", ["use", "search_path", "temp", "preexisting_temp"])
async def test_duckdb_source_changes_fail_then_explicit_new_query_recovers(
    connected_query, monkeypatch, protocol, change,
):
    connection = await ConnectionFactory.create(DatabaseType.DUCKDB, {}, "contract")
    connection.execute("CREATE TABLE orders(id INT)")
    connection.execute("INSERT INTO orders VALUES (1)")
    connection.execute("ATTACH ':memory:' AS other")
    connection.execute("CREATE TABLE other.main.orders(id INT)")
    connection.execute("INSERT INTO other.main.orders VALUES (999)")
    connection.execute("CREATE SCHEMA extra")
    if change == "preexisting_temp":
        connection.execute("CREATE TEMP TABLE shadow(id INT)")
    changed = False

    class ChangingRouter(LiteLLMRouter):
        async def complete(self, messages, **kwargs):
            nonlocal changed
            if not changed:
                changed = True
                if change == "use":
                    connection.execute("USE other")
                elif change == "search_path":
                    connection.execute("SET search_path='extra'")
                elif change == "temp":
                    connection.execute("CREATE TEMP TABLE orders(id INT)")
                    connection.execute("INSERT INTO temp.main.orders VALUES (999)")
            return {"choices": [{"message": {"content": "SELECT id FROM orders ORDER BY id"}}]}

    monkeypatch.setattr(
        query, "get_default_router", lambda: ChangingRouter(RouterConfig(), mock_mode=True),
    )
    body = {"nl_text": "find all orders", "database": "contract", "execute": True,
            "num_candidates": 1}

    async def request():
        if protocol == "mcp":
            return await mcp_query(nl_text="find all orders", db_id="contract")
        if protocol == "sse":
            events = stream_events(connected_query.post("/api/v1/query/stream", json=body))
            done = next(value for event, value in events if event == "done")
            if done["status"] == "failed":
                assert not any(event == "execution" for event, _ in events)
            return done
        response = connected_query.post("/api/v1/query", json=body)
        assert response.status_code == 200
        return response.json()

    failed = await request()
    assert failed["status"] == "failed", failed
    message = "scope unsupported" if change == "preexisting_temp" else "Schema source changed"
    assert message in str(failed)
    if protocol == "mcp":
        assert failed["rows"] == [] and failed["error_code"] == "WORKFLOW_FAILED"
    else:
        assert failed["execution"] is None
    if change == "search_path":
        connection.execute("USE memory")
    elif change == "temp":
        connection.execute("DROP TABLE temp.main.orders")
    elif change == "preexisting_temp":
        connection.execute("DROP TABLE temp.main.shadow")
    recovered = await request()
    assert recovered["status"] == "completed", recovered
    execution = recovered if protocol == "mcp" else recovered["execution"]
    assert execution["rows"] == [[999 if change == "use" else 1]]


async def test_json_sse_result_and_session_match(connected_query):
    body = {
        "nl_text": "find all orders",
        "database": "contract",
        "execute": True,
        "num_candidates": 1,
    }
    first = connected_query.post("/api/v1/query", json=body).json()
    assert first["status"] == "completed", first
    assert first["execution"]["rows"] == [[1], [2], [3], [4], [5]]
    assert first["llm_calls"] == 1
    body["session_id"] = first["session_id"]
    stream = connected_query.post("/api/v1/query/stream", json=body)
    events = []
    event = ""
    for line in stream.text.splitlines():
        if line.startswith("event:"):
            event = line[6:].strip()
        elif line.startswith("data:"):
            events.append((event, json.loads(line[5:])))
    done = next(data for event, data in events if event == "done")
    assert set(first) == set(done)
    assert set(first["candidates"][0]) == set(done["candidates"][0])
    for field in [
        "primary_sql",
        "execution",
        "validation",
        "status",
        "session_id",
        "plan_id",
        "llm_calls",
    ]:
        assert first[field] == done[field], field
    started = [
        data["stage"] for event, data in events if event == "stage" and data["status"] == "started"
    ]
    assert started == ["parse", "retrieve", "generate", "validate", "execute"]
    assert len(query._SESSION_STORE[first["session_id"]]["turns"]) == 2


async def test_invalid_sql_never_executes(connected_query, monkeypatch):
    class UnsafeRouter(LiteLLMRouter):
        async def complete(self, *args, **kwargs):
            return {"choices": [{"message": {"content": "DELETE FROM orders"}}]}

    monkeypatch.setattr(
        query, "get_default_router", lambda: UnsafeRouter(RouterConfig(), mock_mode=True)
    )
    response = connected_query.post(
        "/api/v1/query",
        json={
            "nl_text": "delete all orders",
            "database": "contract",
            "execute": True,
            "validate": False,
        },
    ).json()
    assert response["status"] == "failed", response
    assert response["execution"] is None
    assert (
        ConnectionFactory.get("contract").execute("SELECT COUNT(*) FROM orders").fetchone()[0] == 5
    )


async def test_sse_marks_disabled_execution_as_skipped(connected_query):
    response = connected_query.post(
        "/api/v1/query/stream",
        json={"nl_text": "find all orders", "database": "contract", "execute": False},
    )
    event = ""
    stages = []
    for line in response.text.splitlines():
        if line.startswith("event:"):
            event = line[6:].strip()
        elif line.startswith("data:") and event == "stage":
            stages.append(json.loads(line[5:]))
    terminal = [s for s in stages if s["stage"] == "execute" and s["status"] == "completed"]
    assert terminal and terminal[0]["skipped"] is True
    assert not any(s["skipped"] for s in stages if s["stage"] == "generate")


async def test_schema_retriever_is_wired_and_filtered_columns_reach_prompt(
    connected_query, monkeypatch
):
    from app.knowledge.retrieval.schema_rag import SchemaSearchResult
    from app.rag import engine

    conn = ConnectionFactory.get("contract")
    conn.execute("CREATE TABLE unrelated (irrelevant_detail TEXT)")
    calls, prompts = [], []

    async def find(query_text, **kwargs):
        calls.append(kwargs)
        return [SchemaSearchResult("order-id", "contract", "orders", "id", rrf_score=1)]

    class CaptureRouter(LiteLLMRouter):
        async def complete(self, messages, **kwargs):
            prompts.append(str(messages))
            return {"choices": [{"message": {"content": "SELECT id FROM orders"}}]}

    monkeypatch.setattr(
        engine, "get_schema_rag", lambda: SimpleNamespace(find_relevant_tables=find)
    )
    monkeypatch.setattr(
        query, "get_default_router", lambda: CaptureRouter(RouterConfig(), mock_mode=True)
    )
    response = connected_query.post(
        "/api/v1/query",
        json={
            "nl_text": "find all orders",
            "database": "contract",
            "num_candidates": 1,
        },
    ).json()
    assert response["status"] == "completed", response
    assert calls[0]["db_id"] == "contract"
    assert "orders" in prompts[0]
    assert "irrelevant_detail" not in prompts[0]


@pytest.mark.parametrize("sql", ["test", "DELETE FROM orders", "SELECT", "SELECT 1; SELECT 2"])
async def test_validator_does_not_treat_nonquery_ast_as_valid(sql):
    from app.nodes.base import NodeInput
    from app.nodes.validate_sql import ValidateSQLNode

    output = await ValidateSQLNode().execute(NodeInput("ignored", context={"sql": sql}))
    assert output.result["passed"] is False


def stream_events(response):
    events = []
    event = ""
    for line in response.text.splitlines():
        if line.startswith("event:"):
            event = line[6:].strip()
        elif line.startswith("data:"):
            events.append((event, json.loads(line[5:])))
    return events


async def mcp_query(**kwargs):
    from fastmcp import Client

    from app.mcp.fastmcp_server import create_nl2sql_server

    async with Client(create_nl2sql_server()) as client:
        result = await client.call_tool("nl_query", kwargs)
        return result.structured_content


@pytest.mark.parametrize("protocol", ["json", "sse", "mcp"])
@pytest.mark.parametrize("reason", ["length", "content_filter", "tool_calls"])
async def test_incomplete_sql_fails_before_validation_execution_and_next_request_recovers(
    connected_query, monkeypatch, protocol, reason,
):
    calls, statements = [], []
    sql = "SELECT id FROM orders ORDER BY id"
    current_reason = reason

    class FinishRouter(LiteLLMRouter):
        async def complete(self, messages, **kwargs):
            calls.append(True)
            return {"choices": [{"finish_reason": current_reason,
                                 "message": {"content": sql}}]}

    monkeypatch.setattr(query, "get_default_router", lambda: FinishRouter(RouterConfig()))
    ConnectionFactory.get("contract").set_trace_callback(statements.append)
    body = {"nl_text": "find orders", "database": "contract", "execute": True,
            "num_candidates": 1}

    async def request():
        if protocol == "mcp":
            return await mcp_query(nl_text="find orders", db_id="contract")
        if protocol == "sse":
            events = stream_events(connected_query.post("/api/v1/query/stream", json=body))
            if current_reason != "stop":
                assert not any(event in ("validation", "execution") for event, _ in events)
                assert next(value for event, value in events if event == "error")["code"] == (
                    "WORKFLOW_FAILED"
                )
            return next(value for event, value in events if event == "done")
        response = connected_query.post("/api/v1/query", json=body)
        assert response.status_code == 200
        return response.json()

    failed = await request()
    assert failed["status"] == "failed", failed
    if protocol == "mcp":
        assert failed["sql"] == ""
    else:
        assert failed["primary_sql"] is None
    assert len(calls) == 1  # no automatic token increase or regeneration
    assert not any(sql.lower() in statement.lower() for statement in statements)
    if reason == "length":
        assert "output limit" in str(failed["errors"])
    if protocol == "mcp":
        assert failed["rows"] == [] and failed["error_code"] == "WORKFLOW_FAILED"
    else:
        assert failed["candidates"] == [] and failed["execution"] is None
        assert failed["validation"] is None
    current_reason = "stop"
    recovered = await request()
    assert recovered["status"] == "completed", recovered
    execution = recovered if protocol == "mcp" else recovered["execution"]
    assert execution["rows"] == [[1], [2], [3], [4], [5]]
    assert len(calls) == 2


@pytest.mark.parametrize("protocol", ["json", "sse"])
async def test_independent_completed_sql_executes_with_visible_discard_warning(
    connected_query, monkeypatch, protocol,
):
    calls = []

    class PartialRouter(LiteLLMRouter):
        async def complete(self, messages, **kwargs):
            calls.append(True)
            return {"choices": [{"finish_reason": "length" if len(calls) == 1 else "stop",
                                 "message": {"content": "SELECT id FROM orders ORDER BY id"}}]}

    monkeypatch.setattr(query, "get_default_router", lambda: PartialRouter(RouterConfig()))
    body = {"nl_text": "find orders", "database": "contract", "execute": True,
            "num_candidates": 2}
    if protocol == "sse":
        events = stream_events(connected_query.post("/api/v1/query/stream", json=body))
        result = next(value for event, value in events if event == "done")
    else:
        result = connected_query.post("/api/v1/query", json=body).json()
    assert result["status"] == "completed", result
    assert len(result["candidates"]) == 1
    discarded = [warning for warning in result["warnings"] if "Discarded incomplete" in warning]
    assert len(discarded) == 1 and "output limit" in discarded[0]
    assert any("RAG unavailable" in warning for warning in result["warnings"])
    assert result["execution"]["rows"] == [[1], [2], [3], [4], [5]]


@pytest.mark.parametrize("kind", ["timeout", "capacity", "internal", "invalid_response"])
async def test_three_protocols_share_infrastructure_failure_contract(
    connected_query, monkeypatch, kind
):
    from app.workflow import query_service
    from app.workflow.contracts import QueryCapacityError

    private_marker = "synthetic-private-exception-detail"

    async def fail(*args, **kwargs):
        if kind == "timeout":
            raise TimeoutError(private_marker)
        if kind == "capacity":
            raise QueryCapacityError(private_marker)
        if kind == "internal":
            raise RuntimeError(private_marker)
        return {"query_id": "q-invalid", "nl_text": private_marker, "status": "unknown"}

    monkeypatch.setattr(query_service, "run_query", fail)
    code, status = {
        "timeout": ("QUERY_TIMEOUT", 504),
        "capacity": ("QUERY_CAPACITY", 503),
        "internal": ("INTERNAL_ERROR", 500),
        "invalid_response": ("INTERNAL_ERROR", 500),
    }[kind]
    body = {"nl_text": "find orders", "database": "contract"}
    response = connected_query.post("/api/v1/query", json=body)
    assert response.status_code == status
    detail = response.json()["detail"]
    assert detail["code"] == code
    assert private_marker not in response.text
    if kind == "capacity":
        assert response.headers["retry-after"] == "1"
        assert detail["retry_after_seconds"] == 1

    stream = connected_query.post("/api/v1/query/stream", json=body)
    assert stream.status_code == 200
    assert stream_events(stream) == [("error", detail)]
    assert private_marker not in stream.text

    projected = await mcp_query(nl_text="find orders", db_id="contract")
    assert projected["status"] == "failed"
    assert projected["error_code"] == code
    assert projected["http_status"] == status
    assert projected["errors"] == [detail["message"]]
    assert projected["rows"] == []
    assert private_marker not in json.dumps(projected)
    if kind == "capacity":
        assert projected["retry_after_seconds"] == 1


async def test_mcp_success_metadata_matches_json_pipeline(connected_query):
    response = connected_query.post(
        "/api/v1/query",
        json={"nl_text": "find orders", "database": "contract", "execute": True,
              "num_candidates": 1},
    ).json()
    projected = await mcp_query(nl_text="find orders", db_id="contract")
    assert projected["status"] == response["status"] == "completed"
    assert projected["sql"] == response["primary_sql"]
    for field in ["columns", "rows", "row_count", "truncated"]:
        assert projected[field] == response["execution"][field]
    assert projected["llm_calls"] == response["llm_calls"] == 1
    assert projected["plan_id"] == response["plan_id"] == "request_query"
    assert projected["query_id"].startswith("q_")
    assert projected["session_id"].startswith("s_")
    assert len(projected["workflow_ids"]) == 1
    assert "error_code" not in projected


async def test_three_protocols_keep_business_failure_and_database_read_only(
    connected_query, monkeypatch
):
    class UnsafeRouter(LiteLLMRouter):
        async def complete(self, messages, **kwargs):
            return {"choices": [{"message": {"content": "DELETE FROM orders"}}]}

    monkeypatch.setattr(
        query, "get_default_router", lambda: UnsafeRouter(RouterConfig(), mock_mode=True)
    )
    body = {"nl_text": "delete orders", "database": "contract", "execute": True,
            "num_candidates": 1}
    response = connected_query.post("/api/v1/query", json=body)
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "failed" and data["errors"]
    assert data["primary_sql"] == "DELETE FROM orders"
    assert data["execution"] is None
    events = stream_events(connected_query.post("/api/v1/query/stream", json=body))
    assert [event for event, _ in events][-2:] == ["error", "done"]
    assert next(value for event, value in events if event == "error")["code"] == "WORKFLOW_FAILED"
    done = events[-1][1]
    assert set(done) == set(data)
    assert done["status"] == "failed" and done["execution"] is None
    projected = await mcp_query(nl_text="delete orders", db_id="contract")
    assert projected["status"] == "failed"
    assert projected["error_code"] == "WORKFLOW_FAILED"
    assert projected["sql"] == "DELETE FROM orders"
    assert projected["errors"]
    count = ConnectionFactory.get("contract").execute("SELECT COUNT(*) FROM orders").fetchone()
    assert count[0] == 5


async def test_mcp_invalid_request_has_safe_machine_readable_error(connected_query):
    data = await mcp_query(nl_text="", db_id="contract")
    assert data["status"] == "failed"
    assert data["error_code"] == "INVALID_REQUEST"
    assert data["http_status"] == 422
    assert data["sql"] == ""


@pytest.mark.parametrize("alias", ["validate", "run_validation"])
async def test_validation_alias_is_shared_by_json_and_sse(connected_query, alias):
    body = {"nl_text": "find orders", "database": "contract", alias: False, "execute": False}
    data = connected_query.post("/api/v1/query", json=body).json()
    events = stream_events(connected_query.post("/api/v1/query/stream", json=body))
    done = events[-1][1]
    assert data["validation"] is done["validation"] is None
    stages = [value for event, value in events if event == "stage"]
    assert any(stage["stage"] == "validate" and stage["skipped"] for stage in stages)
    assert set(done) == set(data)


@pytest.mark.parametrize("streaming", [False, True])
async def test_transport_cancellation_drains_producer_without_error_reclassification(
    monkeypatch, streaming
):
    import asyncio

    from app.workflow import query_service

    started = asyncio.Event()
    closed = asyncio.Event()

    async def pending(*args, **kwargs):
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            closed.set()

    monkeypatch.setattr(query_service, "run_query", pending)
    request = query.QueryRequest(nl_text="pending")
    if streaming:
        response = await query.submit_query_stream(request)
        task = asyncio.create_task(anext(response.body_iterator))
    else:
        task = asyncio.create_task(query.submit_query(request))
    await asyncio.wait_for(started.wait(), timeout=2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert closed.is_set()


async def test_json_and_sse_share_serialization_for_native_cell_values(
    connected_query, monkeypatch
):
    from datetime import datetime
    from decimal import Decimal

    from app.workflow import query_service

    async def native_values(*args, **kwargs):
        return {
            "query_id": "q-serialization",
            "nl_text": "serialization fixture",
            "execution": {
                "columns": ["amount", "at"],
                "rows": [[Decimal("12.50"), datetime(2026, 10, 21, 1, 2, 3)]],
                "row_count": 1,
                "truncated": False,
            },
            "internal_only": "must-not-be-public",
        }

    monkeypatch.setattr(query_service, "run_query", native_values)
    body = {"nl_text": "serialization fixture"}
    data = connected_query.post("/api/v1/query", json=body).json()
    events = stream_events(connected_query.post("/api/v1/query/stream", json=body))
    assert events == [("done", data)]
    assert data["execution"]["rows"] == [["12.50", "2026-10-21T01:02:03"]]
    assert "internal_only" not in data
    projected = await mcp_query(nl_text="serialization fixture")
    assert projected["rows"] == data["execution"]["rows"]


@pytest.mark.parametrize("backend_failure", [False, True])
async def test_schema_degradation_warning_reaches_all_protocols(
    connected_query, monkeypatch, backend_failure
):
    from app.rag import engine

    async def search(*args, **kwargs):
        if backend_failure:
            raise RuntimeError("synthetic private search exception")
        return []

    monkeypatch.setattr(
        engine, "get_schema_rag", lambda: SimpleNamespace(find_relevant_tables=search)
    )
    body = {"nl_text": "find all orders", "database": "contract", "execute": True,
            "num_candidates": 1}
    data = connected_query.post("/api/v1/query", json=body).json()
    expected = "SCHEMA_RAG_UNAVAILABLE" if backend_failure else "SCHEMA_LINKING_FALLBACK"
    assert data["status"] == "completed"
    assert data["execution"]["row_count"] == 5
    assert any(warning.startswith(expected) for warning in data["warnings"])
    events = stream_events(connected_query.post("/api/v1/query/stream", json=body))
    schema_event = next(value for event, value in events if event == "schema")
    done = next(value for event, value in events if event == "done")
    assert any(warning.startswith(expected) for warning in schema_event["warnings"])
    assert any(warning.startswith(expected) for warning in done["warnings"])
    projected = await mcp_query(nl_text="find all orders", db_id="contract")
    assert projected["status"] == "completed"
    assert any(warning.startswith(expected) for warning in projected["warnings"])
    assert "synthetic private search exception" not in json.dumps(
        {"json": data, "events": events, "mcp": projected}
    )


@pytest.mark.parametrize("sql,accepted", [
    ("WITH active AS (SELECT id FROM orders) SELECT id FROM active ORDER BY id", True),
    ("SELECT id FROM orders o JOIN users u ON u.id=o.user_id", False),
    ("SELECT main.orders.id FROM main.orders ORDER BY id", True),
    ("SELECT private.orders.id FROM main.orders ORDER BY id", False),
])
async def test_scoped_validation_drives_all_three_protocols(
    connected_query, monkeypatch, sql, accepted
):
    class ScopedRouter(LiteLLMRouter):
        async def complete(self, messages, **kwargs):
            return {"choices": [{"message": {"content": sql}}]}

    monkeypatch.setattr(
        query, "get_default_router", lambda: ScopedRouter(RouterConfig(), mock_mode=True)
    )
    body = {"nl_text": "find all orders", "database": "contract", "execute": True,
            "num_candidates": 1}
    data = connected_query.post("/api/v1/query", json=body).json()
    events = stream_events(connected_query.post("/api/v1/query/stream", json=body))
    done = next(value for event, value in events if event == "done")
    projected = await mcp_query(nl_text="find all orders", db_id="contract")
    expected = "completed" if accepted else "failed"
    assert data["status"] == done["status"] == projected["status"] == expected
    assert data["primary_sql"] == projected["sql"] == sql
    assert data["validation"]["schema_valid"] is accepted
    assert done["validation"]["schema_valid"] is accepted
    if accepted:
        assert data["execution"]["rows"] == done["execution"]["rows"] == projected["rows"]
        assert projected["rows"] == [[1], [2], [3], [4], [5]]
    else:
        assert data["execution"] is done["execution"] is None
        assert projected["rows"] == []
        assert projected["error_code"] == "WORKFLOW_FAILED"


@pytest.mark.parametrize("protocol", ["json", "sse", "mcp"])
async def test_request_usage_summary_contract(connected_query, protocol):
    body = {"nl_text": "find all orders", "database": "contract", "execute": True,
            "num_candidates": 1}
    if protocol == "mcp":
        result = await mcp_query(nl_text="find all orders", db_id="contract")
    elif protocol == "sse":
        result = next(value for event, value in stream_events(
            connected_query.post("/api/v1/query/stream", json=body)) if event == "done")
    else:
        result = connected_query.post("/api/v1/query", json=body).json()
    assert result["status"] == "completed"
    usage = result["llm_usage"]
    assert usage["attempt_count"] == result["llm_calls"] == 1
    assert usage["missing_usage_count"] == 1
    assert usage["usage_coverage"] == 0
    assert usage["attempts"][0]["prompt_tokens"] is None
    assert usage["attempts"][0]["source"] == "adapter_boundary"


async def test_usage_multiple_targets_and_candidates(connected_query, test_sqlite_db, monkeypatch):
    await ConnectionFactory.create(DatabaseType.SQLITE, {"path": test_sqlite_db}, db_id="second")
    class UsageRouter(LiteLLMRouter):
        async def complete(self, messages, **kwargs):
            return {"usage": {"prompt_tokens": 4, "completion_tokens": 2}, "choices": [
                {"finish_reason": "stop", "message": {"content": "SELECT id FROM orders"}}
            ]}
    monkeypatch.setattr(query, "get_default_router", lambda: UsageRouter(RouterConfig()))
    try:
        result = connected_query.post("/api/v1/query", json={
            "nl_text": "find all orders", "database": "", "num_candidates": 2,
        }).json()
        usage = result["llm_usage"]
        assert usage["attempt_count"] == result["llm_calls"]
        assert usage["attempt_count"] >= 2
        assert usage["known_prompt_tokens"] == usage["attempt_count"] * 4
        assert usage["known_completion_tokens"] == usage["attempt_count"] * 2
        assert usage["complete"]
        assert {c["db_id"] for c in result["candidates"]} == {"contract", "second"}
    finally:
        ConnectionFactory.close("second")


async def test_failed_generation_keeps_usage_summary(connected_query, monkeypatch):
    class TruncatedRouter(LiteLLMRouter):
        async def complete(self, messages, **kwargs):
            return {"usage": {"prompt_tokens": 3, "completion_tokens": 2}, "choices": [
                {"finish_reason": "length", "message": {"content": "SELECT id FROM orders"}}
            ]}
    monkeypatch.setattr(query, "get_default_router", lambda: TruncatedRouter(RouterConfig()))
    result = connected_query.post("/api/v1/query", json={
        "nl_text": "find all orders", "database": "contract", "num_candidates": 1,
    }).json()
    assert result["status"] == "failed"
    assert not result["candidates"]
    usage = result["llm_usage"]
    assert usage["attempt_count"] == result["llm_calls"] > 0
    assert usage["known_prompt_tokens"] == usage["attempt_count"] * 3
    assert all(a["status"] == "incomplete" for a in usage["attempts"])
