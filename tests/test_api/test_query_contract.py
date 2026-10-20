"""JSON/SSE use identical real SQLite execution and session semantics."""

import json
from types import SimpleNamespace

import pytest

from app.api import create_app, query
from app.db.connections import ConnectionFactory, DatabaseType
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
    class UnsafeRouter:
        mock_mode = True

        async def complete(self, *args, **kwargs):
            return {"choices": [{"message": {"content": "DELETE FROM orders"}}]}

    monkeypatch.setattr(query, "get_default_router", lambda: UnsafeRouter())
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
