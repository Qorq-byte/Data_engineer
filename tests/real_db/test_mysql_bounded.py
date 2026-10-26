"""Observe actual aiomysql buffering against owned synthetic MySQL fixtures."""

import asyncio

import aiomysql
import pytest
import pytest_asyncio

from app.db.connections import ConnectionFactory, DatabaseType
from app.db.runtime import gate
from app.nodes.execute_sql import ExecuteSQLNode
from tests.real_db.support import admin_connection, admin_sql
from tests.real_db.test_acceptance import query

pytestmark = [pytest.mark.real_database,
              pytest.mark.parametrize("service", ["mysql"], indirect=True)]


@pytest_asyncio.fixture
async def large_table(service):
    async with admin_connection(service) as admin:
        await admin_sql(service, admin,
                        "CREATE TABLE stage41_rows (id INTEGER PRIMARY KEY, label VARCHAR(16))")
        try:
            cursor = await admin.cursor()
            try:
                await cursor.executemany("INSERT INTO stage41_rows VALUES (%s,%s)",
                                         [(i, f"row-{i}") for i in range(10000)])
            finally:
                await cursor.close()
            yield
        finally:
            await admin_sql(service, admin, "DROP TABLE stage41_rows")


@pytest.mark.parametrize("size", [1000, 10000])
@pytest.mark.parametrize("maximum", [1, 100])
async def test_mysql_driver_buffer_is_bounded(service, connection, large_table, monkeypatch,
                                             request, size, maximum):
    identity, conn = connection
    observed = []
    original = aiomysql.Cursor._do_get_result

    async def inspect(cursor):
        await original(cursor)
        if cursor.description and cursor.description[0][0] == "stage41_id":
            observed.append(len(cursor._rows))

    monkeypatch.setattr(aiomysql.Cursor, "_do_get_result", inspect)
    node = ExecuteSQLNode()
    output = await node.execute(query(identity, conn,
        f"SELECT id AS stage41_id,label FROM stage41_rows WHERE id < {size} ORDER BY id",
        max_rows=maximum))
    assert output.metadata["status"] == "success"
    assert output.result["columns"] == ["stage41_id", "label"]
    assert output.result["row_count"] == maximum and output.result["truncated"]
    assert output.result["rows"] == [[i, f"row-{i}"] for i in range(maximum)]
    request.node.user_properties.extend([
        ("buffered_rows", str(observed)), ("fixture_rows", size), ("max_rows", maximum),
    ])
    assert observed == [maximum + 1]
    again = await node.execute(query(identity, conn, "SELECT 7 AS after_truncation"))
    assert again.result["rows"] == [[7]] and not conn.closed


@pytest.mark.parametrize(("sql", "rows", "truncated"), [
    ("SELECT id FROM orders WHERE 1=0", [], False),
    ("SELECT id FROM orders ORDER BY id LIMIT 2", [[10], [20]], False),
    ("SELECT id FROM orders ORDER BY id LIMIT 3", [[10], [20]], True),
    ("SELECT id FROM orders ORDER BY id LIMIT 1", [[10]], False),
    ("SELECT id FROM orders ORDER BY id LIMIT 1, 4", [[20], [30]], True),
    ("SELECT id FROM orders ORDER BY id LIMIT 4 OFFSET 1", [[20], [30]], True),
    ("SELECT id FROM orders ORDER BY id LIMIT 0", [], False),
    ("WITH chosen AS (SELECT id FROM orders) SELECT id FROM chosen ORDER BY id DESC",
     [[50], [40]], True),
    ("SELECT id FROM orders WHERE id=10 UNION ALL SELECT id FROM orders WHERE id>10 "
     "ORDER BY id DESC", [[50], [40]], True),
    ("SELECT id FROM orders ORDER BY id; -- trailing comment", [[10], [20]], True),
    ("SELECT id FROM orders ORDER BY id /* trailing comment */", [[10], [20]], True),
    ("(SELECT id FROM orders ORDER BY id LIMIT 4)", [[10], [20]], True),
    ("SELECT id FROM (SELECT id FROM orders ORDER BY id LIMIT 1) AS chosen",
     [[10]], False),
    ("SELECT id FROM orders WHERE id=10 UNION ALL "
     "(SELECT id FROM orders WHERE id>10 ORDER BY id LIMIT 1)", [[10], [20]], False),
])
async def test_mysql_limit_semantics(service, connection, sql, rows, truncated):
    identity, conn = connection
    output = await ExecuteSQLNode().execute(query(identity, conn, sql, max_rows=2))
    assert output.metadata["status"] == "success", output.errors
    assert output.result == {"columns": ["id"], "rows": rows, "row_count": len(rows),
                             "truncated": truncated}


async def test_mysql_implicit_labels_and_global_cap(service, connection, monkeypatch):
    from app.config.settings import settings

    identity, conn = connection
    monkeypatch.setattr(settings, "max_result_rows", 1)
    output = await ExecuteSQLNode().execute(query(identity, conn,
        "SELECT id+1, 'literal  label' FROM orders ORDER BY id", max_rows=100))
    assert output.result == {"columns": ["id+1", "literal  label"],
                             "rows": [[11, "literal  label"]], "row_count": 1,
                             "truncated": True}


@pytest.mark.parametrize("phase", ["fetch", "close", "restore"])
@pytest.mark.parametrize("mode", ["cancel", "timeout"])
async def test_mysql_interrupted_cleanup(service, connection, monkeypatch, phase, mode):
    """Pause a real cursor at an exact lifecycle boundary, without replacing DB I/O."""
    identity, conn = connection
    reached = asyncio.Event()
    never = asyncio.Event()
    cursor_type = aiomysql.Cursor
    method = {"fetch": "fetchmany", "close": "close", "restore": "execute"}[phase]
    original = getattr(cursor_type, method)

    async def paused(cursor, *args, **kwargs):
        if cursor.connection is conn and (phase != "restore" or
                args[0].startswith("SET SESSION sql_notes=1")):
            reached.set()
            await never.wait()
        return await original(cursor, *args, **kwargs)

    monkeypatch.setattr(cursor_type, method, paused)
    task = asyncio.create_task(ExecuteSQLNode().execute(query(
        identity, conn, "SELECT id FROM orders ORDER BY id", max_rows=1,
        explain=phase == "restore", timeout_ms=200 if mode == "timeout" else 5000)))
    try:
        await asyncio.wait_for(reached.wait(), 3)
        if mode == "cancel":
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(task, 3)
        else:
            result = await asyncio.wait_for(task, 3)
            assert result.metadata["status"] == "execution_error"
    finally:
        never.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    assert conn.closed
    assert not gate(("database", id(conn))).locked()
    pool = ConnectionFactory._mysql_pools[id(conn)]
    await asyncio.wait_for(ConnectionFactory.aclose(identity), 3)
    assert pool.closed and pool.size == 0
    assert id(conn) not in ConnectionFactory._mysql_pools
    replacement = await ConnectionFactory.create(DatabaseType.MYSQL, service.config(), identity)
    again = await ExecuteSQLNode().execute(query(identity, replacement, "SELECT 1 AS recovered"))
    assert again.result["rows"] == [[1]]


@pytest.mark.parametrize("phase", ["close", "restore"])
async def test_mysql_cleanup_failure_discards_session(service, connection, monkeypatch, phase):
    identity, conn = connection
    original = getattr(aiomysql.Cursor, phase if phase == "close" else "execute")
    injected = False

    async def failed(cursor, *args, **kwargs):
        nonlocal injected
        if cursor.connection is conn and not injected and (phase == "close" or
                args[0].startswith("SET SESSION sql_notes=1")):
            injected = True
            raise RuntimeError("injected cleanup failure")
        return await original(cursor, *args, **kwargs)

    monkeypatch.setattr(aiomysql.Cursor, phase if phase == "close" else "execute", failed)
    result = await ExecuteSQLNode().execute(query(identity, conn,
        "SELECT id FROM orders", explain=phase == "restore"))
    assert injected and result.metadata["status"] == "execution_error"
    assert result.result is None and conn.closed
    await asyncio.wait_for(ConnectionFactory.aclose(identity), 3)
    assert id(conn) not in ConnectionFactory._mysql_pools


async def test_mysql_select_has_no_note_setting_side_effect(service, connection, monkeypatch):
    identity, conn = connection
    statements = []
    original = aiomysql.Cursor.execute

    async def observed(cursor, sql, *args):
        if cursor.connection is conn:
            statements.append(sql)
        return await original(cursor, sql, *args)

    monkeypatch.setattr(aiomysql.Cursor, "execute", observed)
    result = await ExecuteSQLNode().execute(query(identity, conn, "SELECT id FROM orders",
                                                 max_rows=1))
    assert result.metadata["status"] == "success"
    assert len(statements) == 1
    assert "sql_notes" not in statements[0]


@pytest.mark.parametrize("initial", [0, 1])
@pytest.mark.parametrize("prefix", [None, "EXPLAIN ", "explain\n"])
async def test_mysql_explain_restores_exact_note_setting(service, connection, recwarn,
                                                        initial, prefix):
    identity, conn = connection
    cursor = await conn.cursor()
    try:
        await cursor.execute(f"SET SESSION sql_notes={initial}")
        sql = "SELECT id AS private_stage41_plan FROM orders"
        result = await ExecuteSQLNode().execute(query(identity, conn,
            f"{prefix}{sql}" if prefix else sql, explain=prefix is None))
        assert result.metadata["status"] == "success", result.errors
        assert all("private_stage41_plan" not in str(w.message) for w in recwarn)
        await cursor.execute("SELECT @@SESSION.sql_notes")
        assert (await cursor.fetchone())[0] == initial
        assert not conn.closed
    finally:
        if not conn.closed:
            await cursor.execute("SET SESSION sql_notes=1")
        await cursor.close()


async def test_mysql_unbuffered_close_drains_remaining_rows(service, connection, large_table,
                                                           monkeypatch, request):
    """Control probe: replacing Cursor with SSCursor alone still reads on close."""
    identity, conn = connection
    original = conn._read_packet
    packets = 0

    async def counted(*args, **kwargs):
        nonlocal packets
        packet = await original(*args, **kwargs)
        packets += 1
        return packet

    monkeypatch.setattr(conn, "_read_packet", counted)
    cursor = await conn.cursor(aiomysql.SSCursor)
    try:
        # Intentionally probe the alternative directly, using the SELECT-only
        # fixture account. Production execution always uses bounded_mysql_query.
        await cursor.execute("SELECT id FROM stage41_rows WHERE id<1000 ORDER BY id")
        assert await cursor.fetchmany(2) == [(0,), (1,)]
        before = packets
        await asyncio.wait_for(cursor.close(), 3)
        drained = packets - before
        request.node.user_properties.append(("packets_drained_on_unbuffered_close", drained))
        assert drained == 999  # Remaining 998 rows plus EOF, after returning only 2 rows.
        again = await ExecuteSQLNode().execute(query(identity, conn, "SELECT 1 AS after_probe"))
        assert again.result["rows"] == [[1]] and not conn.closed
    finally:
        await cursor.close()
