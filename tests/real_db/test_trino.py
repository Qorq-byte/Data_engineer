"""Opt-in acceptance against a disposable Trino catalog and its read-only role."""

import asyncio
import os

import pytest
import pytest_asyncio

from app.api.query import ExecuteRequest, QueryRequest, _execute_sql_direct
from app.db.connections import ConnectionFactory, DatabaseType
from app.db.schema_extractor import SchemaExtractor
from app.db.trino_source import TrinoConnection
from app.llm.router import LiteLLMRouter, RouterConfig
from app.workflow.query_service import run_query

pytestmark = pytest.mark.real_database
DB_ID = "stage67-trino"


@pytest_asyncio.fixture
async def trino():
    port = os.environ.get("DE_REAL_TRINO_PORT")
    if os.environ.get("DE_REAL_DB_TESTS") != "1" or port is None:
        pytest.skip("Explicit Trino fixture required")
    port = int(port)
    if not 1024 <= port <= 65535 or port == 8080:
        raise ValueError("Trino fixture requires a dedicated mapped port")
    config = {"host": "127.0.0.1", "port": port, "database": "fixture.acceptance"}
    reader = await ConnectionFactory.create(
        DatabaseType.TRINO, {**config, "user": "stage67_reader"}, DB_ID
    )
    try:
        yield reader, config
    finally:
        await ConnectionFactory.aclose(DB_ID)


async def test_trino_schema_execution_and_native_read_only(trino):
    reader, _ = trino
    snapshot = await SchemaExtractor.extract(reader, DatabaseType.TRINO)
    table = snapshot.tables["orders"]
    assert snapshot.database_name == "fixture.acceptance"
    assert (table.catalog_name, table.schema_name) == ("fixture", "acceptance")
    assert [(c.name, c.type) for c in table.columns] == [("id", "integer"), ("note", "varchar")]
    result = await _execute_sql_direct(ExecuteRequest(
        database=DB_ID, dialect="trino", sql="SELECT id FROM orders ORDER BY id", max_rows=3,
    ))
    assert result["status"] == "success", result
    assert result["columns"] == ["id"]
    assert result["rows"] == [[10], [20], [30]]
    assert result["truncated"] is True
    result = await reader.query("SELECT id FROM orders ORDER BY id LIMIT 2", 3, 5)
    assert result["rows"] == [[10], [20]]
    assert result["truncated"] is False
    for sql in (
        "SELECT id FROM fixture.other.orders",
        "SELECT table_name FROM system.metadata.table_comments",
        "SELECT id FROM orders; SELECT 1",
        "DELETE FROM orders WHERE id=10",
    ):
        with pytest.raises(ValueError):
            await reader.query(sql, 3, 2)
    with pytest.raises(ValueError, match="continuation outside"):
        reader._poll_url("https://example.invalid/v1/statement/queued/test")
    with pytest.raises(ConnectionError, match="statement failed"):
        await reader._request("INSERT INTO orders VALUES (60, NULL)", timeout=5, row_limit=1)
    assert (await reader.query("SELECT count(*) FROM orders", 1, 3))["rows"] == [[5]]


async def test_trino_harness_queries_the_selected_catalog(trino):
    class FixtureRouter(LiteLLMRouter):
        async def complete(self, messages, **kwargs):
            return {"choices": [{
                "finish_reason": "stop",
                "message": {"content": "SELECT id FROM orders ORDER BY id"},
            }]}

    result = await run_query(
        QueryRequest(nl_text="List order IDs", database=DB_ID, execute=True, num_candidates=1),
        llm_router=FixtureRouter(RouterConfig(), mock_mode=True),
    )
    assert result["status"] == "completed", result
    assert result["execution"]["rows"] == [[10], [20], [30], [40], [50]]


async def test_trino_timeout_cancel_and_follow_up(trino):
    reader, config = trino
    admin = await TrinoConnection.connect({**config, "user": "stage67_admin"})
    query = (
        "SELECT sum(a.id * b.id) FROM long_rows a CROSS JOIN long_rows b "
        "WHERE a.id < b.id"
    )

    async def running() -> bool:
        _, rows = await admin._request(
            "SELECT count(*) FROM system.runtime.queries WHERE user = 'stage67_reader' "
            "AND state NOT IN ('FAILED', 'FINISHED') AND query LIKE '%CROSS JOIN%'",
            timeout=3, row_limit=1,
        )
        return rows[0][0] > 0

    async def wait_for_idle():
        async with asyncio.timeout(8):
            while await running():
                await asyncio.sleep(0.05)

    try:
        with pytest.raises(TimeoutError):
            await reader.query(query, 1, 0.01)
        await wait_for_idle()
        task = asyncio.create_task(reader.query(query, 1, 20))
        try:
            async with asyncio.timeout(8):
                while not await running():
                    await asyncio.sleep(0.05)
        finally:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        await wait_for_idle()
        assert (await reader.query("SELECT id FROM orders ORDER BY id", 1, 4))["rows"] == [[10]]
    finally:
        await admin.close()
