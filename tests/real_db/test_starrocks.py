"""Opt-in acceptance against a disposable StarRocks MySQL-protocol listener."""

import asyncio
import os

import aiomysql
import pytest
import pytest_asyncio

from app.api.query import ExecuteRequest, QueryRequest, _execute_sql_direct
from app.db.connections import ConnectionFactory, DatabaseType
from app.db.schema_extractor import SchemaExtractor
from app.llm.router import LiteLLMRouter, RouterConfig
from app.workflow.query_service import run_query

pytestmark = pytest.mark.real_database
DATABASE = "de_stage67_fixture"
DB_ID = "stage67-starrocks"
READER = "stage67_reader"


@pytest_asyncio.fixture
async def starrocks():
    port = os.environ.get("DE_REAL_STARROCKS_PORT")
    if os.environ.get("DE_REAL_DB_TESTS") != "1" or port is None:
        pytest.skip("Explicit StarRocks fixture required")
    port = int(port)
    if not 1024 <= port <= 65535 or port in {3306, 9030}:
        raise ValueError("StarRocks fixture requires a dedicated mapped port")
    config = {
        "host": "127.0.0.1", "port": port, "database": DATABASE,
        "user": READER, "password": "stage67-disposable-reader",
    }
    reader = await ConnectionFactory.create(DatabaseType.STARROCKS, config, DB_ID)
    try:
        yield reader, config
    finally:
        await ConnectionFactory.aclose(DB_ID)


async def test_starrocks_schema_execution_and_native_read_only(starrocks):
    reader, config = starrocks
    snapshot = await SchemaExtractor.extract(reader, DatabaseType.STARROCKS)
    table = snapshot.tables["orders"]
    assert snapshot.database_name == DATABASE
    assert (table.catalog_name, table.schema_name) == ("default_catalog", DATABASE)
    assert [(c.name, c.type, c.nullable) for c in table.columns] == [
        ("id", "int(11)", False), ("note", "varchar(32)", True)
    ]
    result = await _execute_sql_direct(ExecuteRequest(
        database=DB_ID, dialect="starrocks", sql="SELECT id FROM orders ORDER BY id", max_rows=3,
    ))
    assert result["status"] == "success", result
    assert result["columns"] == ["id"]
    assert result["rows"] == [[10], [20], [30]]
    assert result["truncated"] is True
    limited = await reader.query("SELECT id FROM orders ORDER BY id LIMIT 2", 3, 5)
    assert limited["rows"] == [[10], [20]]
    assert limited["truncated"] is False
    for sql in (
        "SELECT id FROM other.orders",
        "SELECT id FROM orders; SELECT 1",
        "DELETE FROM orders WHERE id=10",
        "SELECT /*+ SET_VAR(query_timeout = 300) */ id FROM orders",
    ):
        with pytest.raises(ValueError):
            await reader.query(sql, 3, 2)
    async with aiomysql.connect(
        host=config["host"], port=config["port"], user=READER,
        password=config["password"], db=DATABASE, autocommit=True,
    ) as native, native.cursor() as cursor:
        with pytest.raises(aiomysql.Error):
            await cursor.execute("INSERT INTO orders VALUES (60, NULL)")
    assert (await reader.query("SELECT count(*) FROM orders", 1, 3))["rows"] == [[5]]


async def test_starrocks_harness_uses_native_execution(starrocks):
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


async def test_starrocks_timeout_and_native_cancel(starrocks):
    reader, config = starrocks
    query = (
        "SELECT sum(a.id * b.id) FROM long_rows a CROSS JOIN long_rows b "
        "WHERE a.id < b.id"
    )
    async with aiomysql.connect(
        host=config["host"], port=config["port"], user="root", autocommit=True,
    ) as admin:
        async def active():
            async with admin.cursor(aiomysql.DictCursor) as cursor:
                await cursor.execute("SHOW FULL PROCESSLIST")
                rows = await cursor.fetchall()
            return any(
                row["User"] == READER and "CROSS JOIN" in str(row.get("Info")) for row in rows
            )

        async def wait_for_idle():
            async with asyncio.timeout(8):
                while await active():
                    await asyncio.sleep(0.05)

        with pytest.raises(TimeoutError):
            await reader.query(query, 1, 0.01)
        await wait_for_idle()
        task = asyncio.create_task(reader.query(query, 1, 20))
        try:
            async with asyncio.timeout(8):
                while not await active():
                    await asyncio.sleep(0.05)
        finally:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        await wait_for_idle()
    assert (await reader.query("SELECT id FROM orders ORDER BY id", 1, 4))["rows"] == [[10]]
