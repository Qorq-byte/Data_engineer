"""Explicitly enabled acceptance on a pre-provisioned, disposable Snowflake namespace.

No administrative fixture creation, no implicit .env credential discovery, no LLM calls.
See docs/optimization/snowflake-acceptance.md for grants, data and cost boundaries.
"""

import asyncio
import os

import pytest
import pytest_asyncio

from app.api.query import ExecuteRequest, QueryRequest, _execute_sql_direct
from app.db.connections import ConnectionFactory, DatabaseType
from app.db.schema_extractor import SchemaExtractor
from app.llm.router import LiteLLMRouter, RouterConfig
from app.workflow.query_service import run_query

pytestmark = pytest.mark.real_database
DB_ID = "acceptance-snowflake"


@pytest_asyncio.fixture
async def snowflake(record_property):
    if os.environ.get("DE_REAL_SNOWFLAKE_TESTS") != "1":
        pytest.skip("Dedicated Snowflake fixture and explicit cloud-cost authorization required")
    fields = ("account", "user", "password", "warehouse", "database", "schema", "role")
    config = {key: os.environ.get(f"DE_SNOWFLAKE_{key.upper()}", "") for key in fields}
    missing = [key for key, value in config.items() if not value]
    if missing:
        pytest.fail(f"Explicit Snowflake fixture settings missing: {', '.join(missing)}")
    if config["schema"].upper() != "DE_ACCEPTANCE_FIXTURE":
        pytest.fail("Snowflake tests require the dedicated DE_ACCEPTANCE_FIXTURE schema")
    # Missing optional driver after opting in is a failed acceptance, never a skip.
    import snowflake.connector

    record_property("snowflake_driver", snowflake.connector.__version__)
    reader = await ConnectionFactory.create(DatabaseType.SNOWFLAKE, config, DB_ID)
    try:
        result = await reader.query("SELECT CURRENT_VERSION()", 1, 10)
        record_property("snowflake_server", result["rows"][0][0])
        yield reader
    finally:
        await ConnectionFactory.aclose(DB_ID)


async def test_metadata_rows_and_native_read_only(snowflake):
    from snowflake.connector.errors import ProgrammingError

    snapshot = await SchemaExtractor.extract(snowflake, DatabaseType.SNOWFLAKE)
    table = snapshot.tables["ORDERS"]
    assert (table.catalog_name, table.schema_name) == (snowflake.database, snowflake.schema)
    assert [c.name for c in table.columns] == ["ID", "NOTE"]
    assert [c.nullable for c in table.columns] == [False, True]
    assert table.comment == "Synthetic acceptance orders"
    assert table.columns[1].comment == "Nullable acceptance note"
    assert table.columns[0].type == "NUMBER"
    assert table.columns[1].type == "TEXT"
    result = await _execute_sql_direct(ExecuteRequest(
        database=DB_ID, dialect="snowflake", sql="SELECT id FROM orders ORDER BY id", max_rows=3,
    ))
    assert result["status"] == "success"
    assert result["rows"] == [[10], [20], [30]]
    assert result["truncated"] is True
    limited = await snowflake.query("SELECT id FROM orders ORDER BY id LIMIT 2", 3, 10)
    assert limited["rows"] == [[10], [20]]
    assert limited["truncated"] is False

    def native_insert():
        with snowflake._connection.cursor() as cursor:
            cursor.execute("INSERT INTO ORDERS (ID, NOTE) VALUES (60, NULL)")

    # Bypass the adapter: certify native role privileges separately from the AST policy.
    with pytest.raises(ProgrammingError) as caught:
        await asyncio.to_thread(native_insert)
    assert caught.value.sqlstate == "42501" or "privilege" in str(caught.value).lower()
    assert (await snowflake.query("SELECT COUNT(*) FROM orders", 1, 10))["rows"] == [[5]]


async def test_harness_uses_native_connector(snowflake):
    class FixtureRouter(LiteLLMRouter):
        async def complete(self, messages, **kwargs):
            return {"choices": [{"finish_reason": "stop", "message": {
                "content": "SELECT id FROM orders ORDER BY id",
            }}]}

    result = await run_query(
        QueryRequest(nl_text="List order IDs", database=DB_ID, execute=True, num_candidates=1),
        llm_router=FixtureRouter(RouterConfig(), mock_mode=True),
    )
    assert result["status"] == "completed"
    assert result["execution"]["rows"] == [[10], [20], [30], [40], [50]]


async def test_timeout_cancel_terminal_state_and_reuse(snowflake):
    # Precreated 10k integer rows. Deliberately expensive, bounded by session timeout.
    query = (
        "SELECT SUM(a.id * b.id * c.id) FROM long_rows a CROSS JOIN long_rows b "
        "CROSS JOIN long_rows c WHERE a.id < b.id AND b.id < c.id"
    )
    with pytest.raises(TimeoutError):
        await snowflake.query(query, 1, 0.01)
    assert snowflake.last_query_id
    status = await asyncio.to_thread(
        snowflake._connection.get_query_status, snowflake.last_query_id
    )
    assert not snowflake._connection.is_still_running(status)

    previous = snowflake.last_query_id
    task = asyncio.create_task(snowflake.query(query, 1, 20))
    try:
        async with asyncio.timeout(10):
            while snowflake.last_query_id == previous:
                await asyncio.sleep(0.01)
        assert not task.done(), "Cancellation fixture completed before it could be cancelled"
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    status = await asyncio.to_thread(
        snowflake._connection.get_query_status, snowflake.last_query_id
    )
    assert not snowflake._connection.is_still_running(status)
    assert (await snowflake.query("SELECT id FROM orders ORDER BY id", 1, 10))["rows"] == [[10]]


async def test_application_scope_and_statement_policy(snowflake):
    for sql in (
        "SELECT id FROM other_schema.orders", "SELECT id FROM other_db.public.orders",
        'SELECT id FROM "de_acceptance_fixture".orders',
        "SELECT id FROM orders; SELECT 1", "DELETE FROM orders WHERE id=10",
        "SELECT * FROM TABLE(GENERATOR(ROWCOUNT => 10))",
    ):
        with pytest.raises(ValueError):
            await snowflake.query(sql, 3, 10)
