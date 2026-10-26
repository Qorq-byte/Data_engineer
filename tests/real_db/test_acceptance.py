"""All database I/O is real. The only synthetic component is deterministic SQL generation."""

import asyncio
from decimal import Decimal

import pytest

from app.db.connections import ConnectionFactory, DatabaseType
from app.db.query_targets import resolve_query_targets
from app.db.runtime import gate
from app.db.schema_extractor import SchemaExtractor
from app.nodes.base import NodeInput
from app.nodes.execute_sql import ExecuteSQLNode
from tests.real_db.support import admin_connection, admin_sql

pytestmark = pytest.mark.real_database


def query(identity, conn, sql, **config):
    return NodeInput(query_text=sql, context={"sql": sql, "connection": conn, "db_id": identity},
                     config=config)


async def wait_for_server_lock(service, admin, conn):
    for _ in range(60):
        if service.kind == "postgresql":
            await admin.execute("SELECT pg_stat_clear_snapshot()")
            waiting = await admin.fetchval(
                "SELECT wait_event_type='Lock' FROM pg_stat_activity WHERE pid=$1",
                conn.get_server_pid())
        else:
            rows = await admin_sql(service, admin, "SHOW FULL PROCESSLIST")
            waiting = any(r[0] == conn.thread_id() and "lock" in r[6].lower() for r in rows)
        if waiting:
            return
        await asyncio.sleep(0.05)
    pytest.fail("Reader never reached the actual server lock wait")


async def test_real_schema_keys_and_namespace(service, connection):
    identity, conn = connection
    schema = await SchemaExtractor.extract(conn, DatabaseType(service.kind), "fixture-alias")
    assert set(schema.table_names) == {"stage40_marker", "users", "orders",
                                      "composite_parent", "composite_child"}
    users, orders = schema.get_table("users"), schema.get_table("orders")
    assert users.primary_keys == ["id"]
    assert orders.primary_keys == ["id"]
    assert orders.get_column("user_id").references == ("users", "id")
    assert any(f.column == "user_id" and f.ref_table == "users" for f in orders.foreign_keys)
    assert any(i.name == "orders_user_idx" for i in orders.indexes)
    expected = "public" if service.kind == "postgresql" else "de_stage40_fixture"
    assert all(t.schema_name == expected for t in schema.tables.values())
    assert ConnectionFactory.get_info(identity).db_type == service.kind


async def test_composite_foreign_keys_keep_column_pairing(service, connection):
    _, conn = connection
    schema = await SchemaExtractor.extract(conn, DatabaseType(service.kind))
    parent, child = schema.get_table("composite_parent"), schema.get_table("composite_child")
    assert parent.primary_keys == ["region", "id"]
    assert child.get_column("region").references == ("composite_parent", "region")
    assert child.get_column("parent_id").references == ("composite_parent", "id")
    assert len(child.foreign_keys) == 2


async def test_empty_result_keeps_columns(connection):
    identity, conn = connection
    output = await ExecuteSQLNode().execute(query(
        identity, conn, "SELECT id,name FROM users WHERE 1=0"))
    assert output.metadata["status"] == "success"
    assert output.result == {"columns": ["id", "name"], "rows": [], "row_count": 0,
                             "truncated": False}


async def test_results_null_decimal_and_limit(connection):
    identity, conn = connection
    node = ExecuteSQLNode()
    result = await node.execute(query(identity, conn, "SELECT id,amount FROM orders ORDER BY id",
                                      max_rows=2))
    assert result.result["rows"] == [[10, Decimal("10.25")], [20, Decimal("20.50")]]
    assert result.metadata["truncated"] is True and result.metadata["row_count"] == 2
    result = await node.execute(query(identity, conn, "SELECT note FROM users WHERE id=1"))
    assert result.result["rows"] == [[None]]


@pytest.mark.parametrize("sql", ["DELETE FROM orders", "SELECT id FROM orders; DELETE FROM orders"])
async def test_application_policy_remains_read_only(connection, sql):
    identity, conn = connection
    output = await ExecuteSQLNode().execute(query(identity, conn, sql, allow_write=True))
    assert output.metadata["status"] == "blocked"
    count = await ExecuteSQLNode().execute(query(
        identity, conn, "SELECT COUNT(*) AS n FROM orders"))
    assert count.result["rows"] == [[5]]


async def test_reader_account_cannot_write_without_application_guard(service, connection):
    _, conn = connection
    if service.kind == "postgresql":
        import asyncpg
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await conn.execute("DELETE FROM orders")
    else:
        import pymysql
        cursor = await conn.cursor()
        try:
            with pytest.raises(pymysql.err.OperationalError) as error:
                await cursor.execute("DELETE FROM orders")
            assert error.value.args[0] == 1142
        finally:
            await cursor.close()


async def test_explain_uses_async_driver_and_returns_plan(connection, recwarn):
    identity, conn = connection
    plans = []

    class ObservedNode(ExecuteSQLNode):
        def _parse_explain_output(self, result, existing=None):
            plans.append(result)
            return super()._parse_explain_output(result, existing)

    output = await ObservedNode().execute(query(
        identity, conn, "SELECT id AS private_plan_marker FROM orders", explain=True))
    assert output.metadata["status"] == "success"
    assert plans[0]["row_count"] > 0 and plans[0]["columns"]
    assert all("private_plan_marker" not in str(w.message) for w in recwarn)


@pytest.mark.parametrize("mode", ["timeout", "cancel"])
@pytest.mark.parametrize("explain", [False, True])
async def test_lock_wait_interrupt_releases_resources(service, connection, mode, explain):
    identity, conn = connection
    node = ExecuteSQLNode()
    async with admin_connection(service) as admin:
        if service.kind == "postgresql":
            await admin.execute("BEGIN")
            await admin.execute("LOCK TABLE orders IN ACCESS EXCLUSIVE MODE")
        else:
            await admin_sql(service, admin, "LOCK TABLES orders WRITE")
        task = asyncio.create_task(node.execute(query(
            identity, conn, "SELECT id FROM orders", explain=explain,
            timeout_ms=200 if mode == "timeout" else 5000)))
        try:
            if mode == "cancel":
                await wait_for_server_lock(service, admin, conn)
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await asyncio.wait_for(task, 3)
            else:
                output = await asyncio.wait_for(task, 3)
                assert output.metadata["status"] == "execution_error"
        finally:
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await admin_sql(service, admin,
                "ROLLBACK" if service.kind == "postgresql" else "UNLOCK TABLES")
    assert not gate(("database", id(conn))).locked()
    if service.kind == "mysql":
        assert conn.closed
        await ConnectionFactory.aclose(identity)
        assert id(conn) not in ConnectionFactory._mysql_pools
        conn = await ConnectionFactory.create(DatabaseType.MYSQL, service.config(), identity)
    result = await node.execute(query(identity, conn, "SELECT COUNT(*) AS n FROM orders"))
    assert result.result["rows"] == [[5]]


async def test_explain_failure_preserves_session(service, connection):
    identity, conn = connection
    before = None
    if service.kind == "mysql":
        cursor = await conn.cursor()
        try:
            await cursor.execute("SELECT @@SESSION.sql_notes")
            before = (await cursor.fetchone())[0]
        finally:
            await cursor.close()
    result = await ExecuteSQLNode().execute(query(identity, conn,
        "SELECT missing_column FROM orders", explain=True))
    assert result.metadata["status"] == "execution_error"
    if service.kind == "mysql":
        cursor = await conn.cursor()
        try:
            await cursor.execute("SELECT @@SESSION.sql_notes")
            assert (await cursor.fetchone())[0] == before
        finally:
            await cursor.close()
    result = await ExecuteSQLNode().execute(query(
        identity, conn, "SELECT COUNT(*) AS n FROM orders"))
    assert result.result["rows"] == [[5]]


async def test_reconnect_never_redirects_captured_target(service, connection):
    identity, old = connection
    warnings = []
    captured = await resolve_query_targets(identity, warnings)
    assert captured[identity].connection is old and not warnings
    new = await ConnectionFactory.create(DatabaseType(service.kind), service.config(), identity)
    result = await ExecuteSQLNode().execute(query(identity, old, "SELECT id FROM orders"))
    assert result.metadata["status"] == "connection_changed"
    result = await ExecuteSQLNode().execute(query(
        identity, new, "SELECT COUNT(*) AS n FROM orders"))
    assert result.result["rows"] == [[5]]
    assert id(old) not in SchemaExtractor._cache


async def test_concurrent_queries_on_single_handle_are_serialized(connection):
    identity, conn = connection
    results = await asyncio.gather(*(ExecuteSQLNode().execute(
        query(identity, conn, f"SELECT id FROM orders WHERE id={i}")) for i in [10, 20, 30, 40]))
    assert [r.result["rows"] for r in results] == [[[10]], [[20]], [[30]], [[40]]]


async def test_close_clears_registry_cache_and_pool(service, connection):
    identity, conn = connection
    await SchemaExtractor.cached_extract(conn, DatabaseType(service.kind))
    assert id(conn) in SchemaExtractor._cache
    await ConnectionFactory.aclose(identity)
    with pytest.raises(KeyError):
        ConnectionFactory.get(identity)
    assert id(conn) not in SchemaExtractor._cache
    assert id(conn) not in ConnectionFactory._mysql_pools


async def test_close_waits_for_real_inflight_query(service, connection):
    identity, conn = connection
    async with admin_connection(service) as admin:
        if service.kind == "postgresql":
            await admin.execute("BEGIN")
            await admin.execute("LOCK TABLE orders IN ACCESS EXCLUSIVE MODE")
        else:
            await admin_sql(service, admin, "LOCK TABLES orders WRITE")
        task = asyncio.create_task(ExecuteSQLNode().execute(query(
            identity, conn, "SELECT id FROM orders ORDER BY id", timeout_ms=5000)))
        closing = None
        unlocked = False
        try:
            await wait_for_server_lock(service, admin, conn)
            closing = asyncio.create_task(ConnectionFactory.aclose(identity))
            await asyncio.sleep(0.05)
            assert not closing.done()
            await admin_sql(service, admin,
                "ROLLBACK" if service.kind == "postgresql" else "UNLOCK TABLES")
            unlocked = True
            result = await asyncio.wait_for(task, 3)
            await asyncio.wait_for(closing, 3)
            assert result.result["rows"] == [[10], [20], [30], [40], [50]]
            with pytest.raises(KeyError):
                ConnectionFactory.get(identity)
        finally:
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            if not unlocked:
                await admin_sql(service, admin,
                    "ROLLBACK" if service.kind == "postgresql" else "UNLOCK TABLES")
            if closing is not None:
                await asyncio.wait_for(closing, 3)


async def test_cancelled_reconnect_disposes_unpublished_connection(
    service, connection, monkeypatch,
):
    from app.db.runtime import lease

    identity, old = connection
    connected = asyncio.Event()
    candidates = []
    original = ConnectionFactory._connect

    async def observed(kind, config):
        candidate = await original(kind, config)
        candidates.append(candidate)
        connected.set()
        return candidate

    monkeypatch.setattr(ConnectionFactory, "_connect", staticmethod(observed))
    async with lease(old):
        task = asyncio.create_task(ConnectionFactory.create(
            DatabaseType(service.kind), service.config(), identity))
        try:
            await asyncio.wait_for(connected.wait(), 3)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            candidate = candidates[0]
            assert candidate.is_closed() if service.kind == "postgresql" else candidate.closed
            assert id(candidate) not in ConnectionFactory._mysql_pools
            assert ConnectionFactory.get(identity) is old
        finally:
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            for candidate in candidates:
                pool = ConnectionFactory._mysql_pools.pop(id(candidate), None)
                if pool is not None:
                    pool.release(candidate)
                    pool.close()
                    await pool.wait_closed()
                elif service.kind == "postgresql" and not candidate.is_closed():
                    await candidate.close()


async def test_formal_harness_with_synthetic_generation_and_real_execution(service, connection,
                                                                          tmp_path, monkeypatch):
    from app.api.query import QueryRequest, QueryResponse
    from app.llm.router import RouterConfig
    from app.workflow.query_service import run_query

    class FixedRouter:
        config = RouterConfig(default_model="fixture-only", fallback_chain=[])

        async def complete(self, messages, **kwargs):
            return {"choices": [{"message": {"content": "SELECT id,name FROM users ORDER BY id"},
                                 "finish_reason": "stop"}]}

    identity, _ = connection
    monkeypatch.setenv("DE_TRACE_DIR", str(tmp_path))
    result = await run_query(QueryRequest(nl_text="列出用户id和name并按id升序", domain="",
        database=identity, dialect=service.kind, num_candidates=1, execute=True),
        llm_router=FixedRouter())
    assert result["status"] == "completed"
    assert result["execution"]["rows"] == [[1, "Ada"], [2, "Bo"], [3, "Noorders"]]
    assert result["llm_calls"] == 1
    QueryResponse.model_validate(result).model_dump(mode="json")
