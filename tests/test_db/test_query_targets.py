"""Pinned target identity with real SQLite/DuckDB and deterministic lifecycle races."""

import asyncio
import sqlite3
from contextlib import asynccontextmanager

import pytest

from app.api.query import _resolve_all_schemas, _resolve_schema
from app.db import runtime
from app.db.connections import ConnectionFactory, DatabaseType
from app.db.query_targets import resolve_query_targets
from app.db.schema_extractor import SchemaExtractor
from app.nodes.base import NodeInput
from app.nodes.execute_sql import ExecuteSQLNode


@pytest.fixture
async def isolated_targets(monkeypatch):
    for attribute in ("_connections", "_infos", "_offload_connections", "_mysql_pools"):
        monkeypatch.setattr(ConnectionFactory, attribute, {})
    monkeypatch.setattr(SchemaExtractor, "_cache", {})
    yield
    await ConnectionFactory.aclose_all()


async def add_database(db_id, db_type=DatabaseType.SQLITE, label="source-label", value=1):
    connection = await ConnectionFactory.create(db_type, {"database": label}, db_id)
    connection.execute("CREATE TABLE marker (id INTEGER)")
    connection.execute(f"INSERT INTO marker VALUES ({value})")
    return connection


@pytest.mark.parametrize("replacement", [None, DatabaseType.SQLITE, DatabaseType.DUCKDB])
async def test_target_keeps_handle_metadata_and_schema_from_before_await(
    isolated_targets, monkeypatch, replacement,
):
    old = await add_database("target")
    original_extract = SchemaExtractor.cached_extract

    async def retire_after_extract(cls, connection, *args, **kwargs):
        schema = await original_extract(connection, *args, **kwargs)
        if replacement is None:
            await ConnectionFactory.aclose("target")
        else:
            await add_database("target", replacement, label="new-label", value=999)
        return schema

    with monkeypatch.context() as patch:
        patch.setattr(SchemaExtractor, "cached_extract", classmethod(retire_after_extract))
        warnings = []
        target = (await resolve_query_targets("target", warnings))["target"]
    assert not warnings
    assert target.connection is old
    assert target.database_name == target.schema.database_name == "source-label"
    assert target.dialect == target.schema.database_type == "sqlite"
    assert id(old) not in SchemaExtractor._cache
    rejected = await ExecuteSQLNode().execute(NodeInput(
        "SELECT id FROM marker",
        context={"connection": target.connection, "db_id": "target"},
    ))
    assert rejected.metadata["status"] == "connection_changed"
    assert rejected.result is None
    if replacement is not None:
        current = (await resolve_query_targets("target", []))["target"]
        assert current.connection is ConnectionFactory.get("target")
        assert current.database_name == "new-label"
        assert current.dialect == replacement.value
        result = await ExecuteSQLNode().execute(NodeInput(
            "SELECT id FROM marker", context={"connection": current.connection, "db_id": "target"},
        ))
        assert result.result["rows"] == [[999]]


async def test_all_targets_and_legacy_projections_keep_distinct_connection_ids(isolated_targets):
    first = await add_database("first")
    second = await add_database("second", value=2)
    targets = await resolve_query_targets("", [])
    assert set(targets) == {"first", "second"}
    assert targets["first"].connection is first
    assert targets["second"].connection is second
    # Equal display names must not merge separate targets.
    assert len(await _resolve_all_schemas("", [])) == 2
    assert (await _resolve_schema("", [])).database_name == "source-label"
    explicit = await resolve_query_targets(" second ", [])
    assert set(explicit) == {"second"}
    assert explicit["second"].connection is second


@pytest.mark.parametrize("database", ["", "missing"])
async def test_missing_target_returns_warning_without_a_replacement(isolated_targets, database):
    warnings = []
    assert await resolve_query_targets(database, warnings) == {}
    assert len(warnings) == 1


async def test_replacing_one_database_does_not_retire_another_target(isolated_targets):
    await add_database("first")
    await add_database("second", value=2)
    targets = await resolve_query_targets("", [])
    await add_database("first", value=999)
    for name, target in targets.items():
        result = await ExecuteSQLNode().execute(NodeInput(
            "SELECT id FROM marker", context={"connection": target.connection, "db_id": name},
        ))
        if name == "first":
            assert result.metadata["status"] == "connection_changed"
        else:
            assert not result.errors
            assert result.result["rows"] == [[2]]


@pytest.mark.parametrize("replace", [False, True])
async def test_identity_is_checked_after_waiting_for_execution_lease(
    isolated_targets, monkeypatch, replace,
):
    old = await add_database("target")
    reached, resume = asyncio.Event(), asyncio.Event()
    original_lease = runtime.lease
    paused = False

    @asynccontextmanager
    async def pause_once(connection):
        nonlocal paused
        if connection is old and not paused:
            paused = True
            reached.set()
            await resume.wait()
        async with original_lease(connection):
            yield

    monkeypatch.setattr(runtime, "lease", pause_once)
    task = asyncio.create_task(ExecuteSQLNode().execute(NodeInput(
        "SELECT id FROM marker", context={"connection": old, "db_id": "target"},
    )))
    try:
        await asyncio.wait_for(reached.wait(), 2)
        if replace:
            await add_database("target", value=999)
        else:
            await ConnectionFactory.aclose("target")
        with pytest.raises(sqlite3.ProgrammingError):
            old.execute("SELECT 1")
    finally:
        resume.set()
    result = await asyncio.wait_for(task, 2)
    assert result.metadata["status"] == "connection_changed"
    assert result.result is None


async def test_unregistered_caller_owned_connection_still_executes():
    with sqlite3.connect(":memory:") as connection:
        result = await ExecuteSQLNode().execute(NodeInput(
            "SELECT 7", context={"connection": connection},
        ))
        assert not result.errors
        assert result.result["rows"] == [[7]]
