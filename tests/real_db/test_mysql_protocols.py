"""Public JSON/SSE/MCP semantics through fixed Harness and real MySQL execution."""

import asyncio
import json

import aiomysql
import pytest

from app.api import query
from app.config.settings import settings
from app.db.connections import ConnectionFactory
from app.db.runtime import gate
from app.llm.router import RouterConfig

pytestmark = [pytest.mark.real_database,
              pytest.mark.parametrize("service", ["mysql"], indirect=True)]


async def request(protocol, identity):
    body = query.QueryRequest(nl_text="find order ids", database=identity,
                              execute=True, num_candidates=1)
    if protocol == "json":
        return (await query.submit_query(body)).model_dump(mode="json")
    if protocol == "mcp":
        from fastmcp import Client

        from app.mcp.fastmcp_server import create_nl2sql_server

        async with Client(create_nl2sql_server()) as client:
            result = await client.call_tool("nl_query", {"nl_text": body.nl_text,
                                                        "db_id": identity})
            return result.structured_content
    response = await query.submit_query_stream(body)
    done = None
    async for chunk in response.body_iterator:
        if chunk.startswith("event: done\n"):
            done = json.loads(chunk.split("data: ", 1)[1])
        assert not chunk.startswith("event: error\n"), chunk
    assert done is not None
    return done


def router(sql):
    class FixedRouter:
        config = RouterConfig(default_model="fixture-only", fallback_chain=[])

        async def complete(self, messages, **kwargs):
            return {"choices": [{"message": {"content": sql}, "finish_reason": "stop"}]}

    return FixedRouter()


@pytest.mark.parametrize("protocol", ["json", "sse", "mcp"])
@pytest.mark.parametrize("truncated", [False, True])
async def test_mysql_protocol_result_and_usage_contract(service, connection, monkeypatch,
                                                       tmp_path, protocol, truncated):
    identity, _ = connection
    sql = "SELECT id FROM orders" + (" ORDER BY id" if truncated else " WHERE id=10")
    monkeypatch.setenv("DE_TRACE_DIR", str(tmp_path))
    monkeypatch.setattr(settings, "max_result_rows", 1)
    monkeypatch.setattr(query, "get_default_router", lambda: router(sql))
    result = await request(protocol, identity)
    assert result["status"] == "completed", result
    execution = result if protocol == "mcp" else result["execution"]
    assert execution["rows"] == [[10]] and execution["row_count"] == 1
    assert execution["truncated"] is truncated
    assert (result["sql"] if protocol == "mcp" else result["primary_sql"]) == sql
    assert result["llm_calls"] == 1
    assert result["llm_usage"]["attempt_count"] == 1
    assert result["llm_usage"]["missing_usage_count"] == 1
    assert result["llm_usage"]["usage_coverage"] == 0
    assert result["llm_usage"]["attempts"][0]["prompt_tokens"] is None
    candidate = result["candidates"][0]
    assert candidate["sql_text"] == sql  # Execution cap never changes candidate evidence.
    assert candidate["id"] and candidate["db_id"] == identity
    assert candidate["finish_reason"] == "stop"
    assert candidate["generation_source"] == "model_response"


@pytest.mark.parametrize("protocol", ["json", "sse", "mcp"])
async def test_mysql_protocol_cancel_drains_execution(service, connection, monkeypatch,
                                                     tmp_path, protocol):
    identity, conn = connection
    reached, release, drained = asyncio.Event(), asyncio.Event(), asyncio.Event()
    original = aiomysql.Cursor.fetchmany

    async def pause(cursor, *args):
        if cursor.connection is conn:
            reached.set()
            try:
                await release.wait()
            finally:
                drained.set()
        return await original(cursor, *args)

    monkeypatch.setenv("DE_TRACE_DIR", str(tmp_path))
    monkeypatch.setattr(query, "get_default_router", lambda: router("SELECT id FROM orders"))
    monkeypatch.setattr(aiomysql.Cursor, "fetchmany", pause)
    task = asyncio.create_task(request(protocol, identity))
    try:
        await asyncio.wait_for(reached.wait(), 3)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 3)
        await asyncio.wait_for(drained.wait(), 3)
        assert conn.closed and not gate(("database", id(conn))).locked()
        await asyncio.wait_for(ConnectionFactory.aclose(identity), 3)
        assert id(conn) not in ConnectionFactory._mysql_pools
    finally:
        release.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
