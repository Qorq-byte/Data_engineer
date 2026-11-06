"""Opt-in acceptance against an owned ClickHouse listener on loopback."""

import asyncio
import os

import httpx
import pytest
import pytest_asyncio

from app.api.query import ExecuteRequest, QueryRequest, _execute_sql_direct
from app.db.connections import ConnectionFactory, DatabaseType
from app.db.schema_extractor import SchemaExtractor
from app.llm.router import LiteLLMRouter, RouterConfig
from app.workflow.query_service import run_query

pytestmark = pytest.mark.real_database
DATABASE = "de_stage67_fixture"
READER = ("stage67_reader", "stage67-disposable-reader")
ADMIN = ("default", "stage67-disposable-admin")
DB_ID = "stage67-clickhouse"


@pytest_asyncio.fixture
async def clickhouse():
    port = os.environ.get("DE_REAL_CLICKHOUSE_PORT")
    if os.environ.get("DE_REAL_DB_TESTS") != "1" or port is None:
        pytest.skip("Explicit ClickHouse fixture required")
    port = int(port)
    if not 1024 <= port <= 65535 or port in {8123, 8443, 9000}:
        raise ValueError("ClickHouse fixture requires a dedicated mapped port")
    config = {
        "host": "127.0.0.1", "port": port, "database": DATABASE,
        "user": READER[0], "password": READER[1],
    }
    async with httpx.AsyncClient(
        base_url=f"http://127.0.0.1:{port}", auth=ADMIN, trust_env=False, timeout=8
    ) as admin:
        conn = await ConnectionFactory.create(DatabaseType.CLICKHOUSE, config, DB_ID)
        try:
            yield conn, admin, config
        finally:
            await ConnectionFactory.aclose(DB_ID)


async def test_schema_execute_and_native_read_only(clickhouse):
    connection, admin, config = clickhouse
    snapshot = await SchemaExtractor.extract(connection, DatabaseType.CLICKHOUSE)
    assert snapshot.database_name == DATABASE
    table = snapshot.tables["orders"]
    assert table.schema_name == DATABASE
    assert [(c.name, c.type, c.nullable) for c in table.columns] == [
        ("id", "UInt32", False), ("note", "Nullable(String)", True)
    ]
    result = await _execute_sql_direct(ExecuteRequest(
        database=DB_ID, dialect="clickhouse", sql="SELECT id FROM orders ORDER BY id", max_rows=3,
    ))
    assert result["status"] == "success"
    assert result["rows"] == [[10], [20], [30]]
    assert result["columns"] == ["id"]
    assert result["truncated"] is True
    small = await connection.query("SELECT id FROM orders ORDER BY id LIMIT 2", 3, 2)
    assert small == {
        "columns": ["id"], "rows": [[10], [20]], "row_count": 2, "truncated": False,
    }
    for sql in (
        "DELETE FROM orders WHERE id=10",
        "SELECT id FROM orders; SELECT 1",
        "SELECT name FROM system.tables",
        "SELECT id FROM orders SETTINGS readonly=0",
        "SELECT id FROM orders FORMAT CSV",
        "SELECT * FROM url('https://example.invalid')",
    ):
        with pytest.raises(ValueError):
            await connection.query(sql, 3, 2)
    native = await admin.post(
        "/", auth=READER, content=f"INSERT INTO {DATABASE}.orders VALUES (60,NULL)"
    )
    assert native.is_error
    with pytest.raises(ConnectionError, match="native read-only"):
        await ConnectionFactory.create(DatabaseType.CLICKHOUSE, {
            **config, "user": ADMIN[0], "password": ADMIN[1],
        })
    assert (await connection.query("SELECT count(*) FROM orders", 3, 2))["rows"] == [[5]]


async def test_query_workflow_uses_clickhouse(clickhouse):
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


async def test_timeout_cancellation_and_connection_reuse(clickhouse):
    connection, admin, _ = clickhouse
    query = (
        "SELECT sum(a.id * b.id) FROM long_rows AS a CROSS JOIN long_rows AS b "
        "WHERE a.id < b.id"
    )
    async def active() -> bool:
        response = await admin.post("/", content=(
            "SELECT count() FROM system.processes WHERE user='stage67_reader' "
            "AND query LIKE '%CROSS JOIN%' FORMAT TSV"
        ))
        response.raise_for_status()
        return int(response.text) > 0

    async def wait_for_idle() -> None:
        async with asyncio.timeout(5):
            while await active():
                await asyncio.sleep(0.02)

    with pytest.raises(TimeoutError):
        await connection.query(query, 1, 0.01)
    await wait_for_idle()

    task = asyncio.create_task(connection.query(query, 1, 15))
    try:
        async with asyncio.timeout(5):
            while not await active():
                await asyncio.sleep(0.02)
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    await wait_for_idle()
    assert (await connection.query("SELECT id FROM orders ORDER BY id", 1, 2))["rows"] == [[10]]
