"""Real DuckDB resolution-state changes, cache provenance and conservative scopes."""

from contextlib import closing

import duckdb
import pytest

from app.db.connections import DatabaseType
from app.db.schema_extractor import SchemaExtractor
from app.knowledge.schema_retriever import _build_filtered_schema
from app.models.schema import SchemaSnapshot
from app.nodes.base import NodeInput
from app.nodes.execute_sql import ExecuteSQLNode
from app.nodes.schema_linking import _filter_schema


@pytest.fixture
def connection(monkeypatch):
    monkeypatch.setattr(SchemaExtractor, "_cache", {})
    with closing(duckdb.connect()) as conn:
        conn.execute("CREATE TABLE marker(id INT)")
        conn.execute("INSERT INTO marker VALUES (1)")
        conn.execute("ATTACH ':memory:' AS other")
        conn.execute("CREATE TABLE other.main.marker(other_id INT)")
        yield conn


async def test_cache_tracks_use_without_waiting_for_ttl(connection):
    old = await SchemaExtractor.cached_extract(connection, DatabaseType.DUCKDB)
    connection.execute("USE other")
    current = await SchemaExtractor.cached_extract(connection, DatabaseType.DUCKDB)
    assert current.tables["marker"].column_names == ["other_id"]
    assert old.tables["marker"].column_names == ["id"]
    assert current.duckdb_source_state.catalog_name == "other"


async def test_cache_tracks_catalog_reattached_under_same_name(connection):
    connection.execute("USE other")
    before = await SchemaExtractor.cached_extract(connection, DatabaseType.DUCKDB)
    connection.execute("USE memory")
    connection.execute("DETACH other")
    connection.execute("ATTACH ':memory:' AS other")
    connection.execute("CREATE TABLE other.main.marker(replacement_id INT)")
    connection.execute("USE other")
    after = await SchemaExtractor.cached_extract(connection, DatabaseType.DUCKDB)
    assert after.tables["marker"].column_names == ["replacement_id"]
    assert after.duckdb_source_state.catalog_oid != before.duckdb_source_state.catalog_oid


@pytest.mark.parametrize("change", ["use", "search_path", "temp_table", "temp_view"])
@pytest.mark.parametrize("explain", [False, True])
async def test_running_request_rejects_changed_environment(connection, change, explain):
    connection.execute("CREATE SCHEMA extra")
    schema = await SchemaExtractor.extract(connection, DatabaseType.DUCKDB)
    if change == "use":
        connection.execute("USE other")
    elif change == "search_path":
        connection.execute("SET search_path='extra'")
    elif change == "temp_table":
        connection.execute("CREATE TEMP TABLE marker(id INT)")
    else:
        connection.execute("CREATE TEMP VIEW shadow AS SELECT 999 AS id")
    result = await ExecuteSQLNode().execute(NodeInput(
        "SELECT 1", context={"schema": schema, "connection": connection},
        config={"explain": explain},
    ))
    assert result.metadata["status"] == "schema_changed", result
    assert result.result is None


@pytest.mark.parametrize("scope", ["temp_table", "temp_view", "extra"])
async def test_preexisting_unsupported_scope_is_not_implicitly_accepted(connection, scope):
    if scope == "extra":
        connection.execute("CREATE SCHEMA extra")
        connection.execute("USE extra")
    elif scope == "temp_table":
        connection.execute("CREATE TEMP TABLE marker(id INT)")
    else:
        connection.execute("CREATE TEMP VIEW shadow AS SELECT 999 AS id")
    schema = await SchemaExtractor.extract(connection, DatabaseType.DUCKDB)
    result = await ExecuteSQLNode().execute(NodeInput(
        "SELECT id FROM memory.main.marker", context={"schema": schema, "connection": connection},
    ))
    assert result.metadata["status"] == "source_scope_unsupported", result
    assert result.result is None


@pytest.mark.parametrize("filter_fn", [_filter_schema, _build_filtered_schema])
async def test_source_state_survives_schema_projection(connection, filter_fn):
    schema = await SchemaExtractor.extract(connection, DatabaseType.DUCKDB)
    assert schema.duckdb_source_state is not None
    assert filter_fn(schema, ["marker"]).duckdb_source_state == schema.duckdb_source_state


@pytest.mark.parametrize("sql", [
    "SELECT id FROM marker", "SELECT COUNT(*) FROM main.marker",
    "WITH x AS (SELECT id FROM marker) SELECT id FROM x",
])
async def test_stable_main_queries_still_execute(connection, sql):
    schema = await SchemaExtractor.extract(connection, DatabaseType.DUCKDB)
    output = await ExecuteSQLNode().execute(NodeInput(
        sql, context={"schema": schema, "connection": connection}, config={"explain": True},
    ))
    assert not output.errors
    assert output.result["rows"] == [[1]]


@pytest.mark.parametrize("legacy", [False, True])
async def test_unbound_direct_and_legacy_queries_keep_existing_scope(connection, legacy):
    connection.execute("CREATE TEMP TABLE marker(id INT)")
    connection.execute("INSERT INTO marker VALUES (999)")
    context = {"connection": connection}
    if legacy:
        context["schema"] = SchemaSnapshot("duckdb", "legacy")
    output = await ExecuteSQLNode().execute(NodeInput("SELECT id FROM marker", context=context))
    assert not output.errors
    assert output.result["rows"] == [[999]]


async def test_bound_explain_uses_original_nondefault_catalog(connection):
    connection.execute("USE other")
    connection.execute("INSERT INTO other.main.marker VALUES (7)")
    schema = await SchemaExtractor.extract(connection, DatabaseType.DUCKDB)
    output = await ExecuteSQLNode().execute(NodeInput(
        "SELECT other_id FROM marker", context={"schema": schema, "connection": connection},
        config={"explain": True},
    ))
    assert not output.errors
    assert output.result["rows"] == [[7]]
    assert output.metadata["plan"] is not None


async def test_cache_detects_search_path_and_returns_detached_snapshot(connection):
    before = await SchemaExtractor.cached_extract(connection, DatabaseType.DUCKDB)
    before.tables.clear()
    connection.execute("SET search_path='main'")
    after = await SchemaExtractor.cached_extract(connection, DatabaseType.DUCKDB)
    assert "marker" in after.tables
    assert before.duckdb_source_state.search_path != after.duckdb_source_state.search_path


async def test_schema_oid_changes_when_namespace_is_recreated(connection):
    connection.execute("CREATE SCHEMA extra")
    connection.execute("USE extra")
    before = await SchemaExtractor.cached_extract(connection, DatabaseType.DUCKDB)
    connection.execute("USE main")
    connection.execute("DROP SCHEMA extra")
    connection.execute("CREATE SCHEMA extra")
    connection.execute("USE extra")
    after = await SchemaExtractor.cached_extract(connection, DatabaseType.DUCKDB)
    assert after.duckdb_source_state.schema_oid != before.duckdb_source_state.schema_oid
