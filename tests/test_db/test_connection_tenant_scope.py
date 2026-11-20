"""Exercise tenant connection lifecycles through real SQLite query targets."""

import asyncio
import sqlite3

import pytest

from app.auth.tenant import reset_current_tenant_id, set_current_tenant_id
from app.db.connections import ConnectionFactory, DatabaseType
from app.db.query_targets import resolve_query_targets
from app.nodes.base import NodeInput
from app.nodes.execute_sql import ExecuteSQLNode


async def test_same_database_id_resolves_and_replaces_independently() -> None:
    db_id = "shared-id"
    both_registered = asyncio.Barrier(2)

    async def create_and_query(tenant_id: int):
        token = set_current_tenant_id(tenant_id)
        try:
            name = f"orders_{tenant_id}"
            connection = await ConnectionFactory.create(DatabaseType.SQLITE, {"alias": name}, db_id)
            connection.execute(f"CREATE TABLE {name} (total INTEGER)")
            connection.execute(f"INSERT INTO {name} VALUES (?)", (tenant_id * 10,))
            await asyncio.wait_for(both_registered.wait(), timeout=5)

            warnings: list[str] = []
            targets = await resolve_query_targets("", warnings)
            assert not warnings
            assert list(targets) == [db_id]
            assert set(targets[db_id].schema.tables) == {name}
            assert ConnectionFactory.get_info(db_id).alias == name
            output = await ExecuteSQLNode().execute(NodeInput(
                f"SELECT total FROM {name}",
                context={"db_id": db_id, "connection": targets[db_id].connection},
            ))
            assert output.result["rows"] == [[tenant_id * 10]]
            return connection
        finally:
            reset_current_tenant_id(token)

    try:
        async with asyncio.TaskGroup() as tasks:
            tenant_one = tasks.create_task(create_and_query(1))
            tenant_two = tasks.create_task(create_and_query(2))
        first, second = tenant_one.result(), tenant_two.result()
        token = set_current_tenant_id(2)
        try:
            assert not ConnectionFactory.is_current(db_id, first)
            rejected = await ExecuteSQLNode().execute(NodeInput(
                "SELECT total FROM orders_1", context={"db_id": db_id, "connection": first},
            ))
            assert rejected.metadata["status"] == "connection_changed"
            replacement = await ConnectionFactory.create(DatabaseType.SQLITE, {}, db_id)
            assert ConnectionFactory.get(db_id) is replacement
            with pytest.raises(sqlite3.ProgrammingError, match="closed"):
                second.execute("SELECT 1")
            await ConnectionFactory.aclose(db_id)
            with pytest.raises(KeyError, match="No connection found"):
                ConnectionFactory.get(db_id)
            assert not ConnectionFactory.list_all()
            with pytest.raises(sqlite3.ProgrammingError, match="closed"):
                replacement.execute("SELECT 1")
        finally:
            reset_current_tenant_id(token)

        assert ConnectionFactory.get(db_id) is first
        assert first.execute("SELECT total FROM orders_1").fetchone()[0] == 10
    finally:
        await ConnectionFactory.aclose_all()
