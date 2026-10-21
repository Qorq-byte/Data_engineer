"""Real SQLite concurrency/cancellation, cache isolation and awaited lifecycle."""

import asyncio

import pytest

from app.db.connections import ConnectionFactory, DatabaseType
from app.db.schema_extractor import SchemaExtractor
from app.nodes.base import NodeInput
from app.nodes.execute_sql import ExecuteSQLNode


@pytest.fixture
async def managed_connection(monkeypatch):
    monkeypatch.setattr(ConnectionFactory, "_connections", {})
    monkeypatch.setattr(ConnectionFactory, "_infos", {})
    monkeypatch.setattr(ConnectionFactory, "_offload_connections", {})
    conn = await ConnectionFactory.create(DatabaseType.SQLITE, {}, "concurrent")
    conn.execute("CREATE TABLE numbers (id INTEGER)")
    conn.executemany("INSERT INTO numbers VALUES (?)", [(number,) for number in range(100)])
    yield conn
    await ConnectionFactory.aclose_all()


async def test_same_connection_results_are_isolated(managed_connection):
    async def query(number):
        return await ExecuteSQLNode().execute(
            NodeInput(
                query_text=f"SELECT {number} AS request_id, COUNT(*) FROM numbers",
                context={"connection": managed_connection},
            )
        )

    outputs = await asyncio.gather(*(query(number) for number in range(12)))
    assert [output.result["rows"] for output in outputs] == [
        [[number, 100]] for number in range(12)
    ]
    assert all(not output.errors for output in outputs)


async def test_native_timeout_does_not_block_loop_and_connection_recovers(managed_connection):
    task = asyncio.create_task(
        ExecuteSQLNode().execute(
            NodeInput(
                query_text=(
                    "WITH RECURSIVE n(x) AS (SELECT 1 UNION ALL "
                    "SELECT x+1 FROM n WHERE x<10000000) SELECT SUM(x) FROM n"
                ),
                context={"connection": managed_connection},
                config={"timeout_ms": 70},
            )
        )
    )
    ticks = 0
    while not task.done():
        await asyncio.sleep(0.001)
        ticks += 1
    output = await task
    assert output.errors
    assert ticks > 2
    assert managed_connection.execute("SELECT COUNT(*) FROM numbers").fetchone()[0] == 100


async def test_native_cancel_waits_before_reuse(managed_connection):
    task = asyncio.create_task(
        ExecuteSQLNode().execute(
            NodeInput(
                query_text=(
                    "WITH RECURSIVE n(x) AS (SELECT 1 UNION ALL "
                    "SELECT x+1 FROM n WHERE x<10000000) SELECT SUM(x) FROM n"
                ),
                context={"connection": managed_connection},
            )
        )
    )
    await asyncio.sleep(0.02)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    result = await ExecuteSQLNode().execute(
        NodeInput("SELECT 42", context={"connection": managed_connection})
    )
    assert result.result["rows"] == [[42]]
    assert not result.errors


async def test_schema_cache_copies_snapshots_and_refreshes_on_invalidation(managed_connection):
    first = await SchemaExtractor.cached_extract(managed_connection, DatabaseType.SQLITE)
    first.tables.clear()
    second = await SchemaExtractor.cached_extract(managed_connection, DatabaseType.SQLITE)
    assert "numbers" in second.tables
    managed_connection.execute("CREATE TABLE added (id INTEGER)")
    refreshed = await SchemaExtractor.cached_extract(managed_connection, DatabaseType.SQLITE)
    assert "added" in refreshed.tables
    assert refreshed.source_schema_version != second.source_schema_version
    SchemaExtractor.invalidate(managed_connection)
    assert (
        "added"
        in (await SchemaExtractor.cached_extract(managed_connection, DatabaseType.SQLITE)).tables
    )


async def test_replacing_connection_closes_original_and_clears_cache(managed_connection):
    await SchemaExtractor.cached_extract(managed_connection, DatabaseType.SQLITE)
    replacement = await ConnectionFactory.create(DatabaseType.SQLITE, {}, "concurrent")
    assert replacement is ConnectionFactory.get("concurrent")
    assert id(managed_connection) not in SchemaExtractor._cache
    with pytest.raises(Exception, match="closed"):
        managed_connection.execute("SELECT 1")


async def test_async_close_is_awaited(monkeypatch):
    closed = []

    class AsyncConnection:
        async def close(self):
            await asyncio.sleep(0)
            closed.append(True)

    monkeypatch.setattr(ConnectionFactory, "_connections", {"async": AsyncConnection()})
    monkeypatch.setattr(ConnectionFactory, "_infos", {})
    await ConnectionFactory.aclose_all()
    assert closed == [True]
    assert ConnectionFactory._connections == {}
