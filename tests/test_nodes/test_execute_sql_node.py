"""Tests for ExecuteSQLNode — the only fully implemented node in Phase 1."""


import pytest

from app.nodes.base import NodeInput
from app.nodes.execute_sql import ExecuteSQLNode


@pytest.fixture
def exec_node():
    return ExecuteSQLNode()


@pytest.fixture
def sqlite_ctx(sqlite_connection):
    return {"connection": sqlite_connection}


def test_is_write_statement_select():
    """SELECT should NOT be flagged as write."""
    assert not ExecuteSQLNode._is_write_statement("SELECT * FROM users")
    assert not ExecuteSQLNode._is_write_statement("  SELECT 1  ")
    assert not ExecuteSQLNode._is_write_statement("WITH cte AS (SELECT 1) SELECT * FROM cte")


def test_is_write_statement_block():
    """Write keywords should be flagged."""
    assert ExecuteSQLNode._is_write_statement("INSERT INTO users VALUES (1)")
    assert ExecuteSQLNode._is_write_statement("UPDATE users SET name='x'")
    assert ExecuteSQLNode._is_write_statement("DELETE FROM users")
    assert ExecuteSQLNode._is_write_statement("DROP TABLE users")
    assert ExecuteSQLNode._is_write_statement("ALTER TABLE users ADD COLUMN x INT")
    assert ExecuteSQLNode._is_write_statement("TRUNCATE users")
    assert ExecuteSQLNode._is_write_statement("CREATE TABLE t (id INT)")


def test_is_write_statement_comments():
    """Comments should be stripped before checking."""
    assert not ExecuteSQLNode._is_write_statement(
        "-- This is a comment\nSELECT * FROM users"
    )


def test_is_write_statement_empty():
    assert not ExecuteSQLNode._is_write_statement("")
    assert not ExecuteSQLNode._is_write_statement("   ")


@pytest.mark.asyncio
async def test_execute_sql_read_query(exec_node, sqlite_ctx):
    """Execute a simple SELECT query."""
    input = NodeInput(
        query_text="SELECT 1 AS num",
        context=sqlite_ctx,
        config={"max_rows": 100},
    )
    output = await exec_node.execute(input)
    assert output.errors == []
    assert output.result is not None
    assert output.result["columns"] == ["num"]
    assert output.result["rows"][0][0] == 1


@pytest.mark.asyncio
async def test_execute_sql_blocked_write(exec_node, sqlite_ctx):
    """Write operations should be blocked by default."""
    input = NodeInput(
        query_text="DROP TABLE IF EXISTS nonexistent",
        context=sqlite_ctx,
        config={"max_rows": 100},
    )
    output = await exec_node.execute(input)
    assert len(output.errors) > 0
    assert "blocked" in output.metadata.get("reason", "")


@pytest.mark.asyncio
async def test_execute_sql_allowed_write(exec_node, sqlite_ctx):
    """Write operations should pass when allow_write=True."""
    input = NodeInput(
        query_text="CREATE TABLE test_allow (id INT)",
        context=sqlite_ctx,
        config={"allow_write": True, "max_rows": 100},
    )
    output = await exec_node.execute(input)
    assert output.errors == []
    assert output.result["row_count"] == 0  # DDL returns no rows


@pytest.mark.asyncio
async def test_execute_sql_no_connection(exec_node):
    """Missing connection should return error."""
    input = NodeInput(
        query_text="SELECT 1",
        context={},
        config={},
    )
    output = await exec_node.execute(input)
    assert len(output.errors) > 0
    assert output.result is None


@pytest.mark.asyncio
async def test_execute_sql_empty_statement(exec_node, sqlite_ctx):
    """Empty SQL should return error."""
    input = NodeInput(
        query_text="",
        context=sqlite_ctx,
        config={},
    )
    output = await exec_node.execute(input)
    assert len(output.errors) > 0


@pytest.mark.asyncio
async def test_execute_sql_with_sample_data(exec_node, sqlite_connection):
    """Execute a query against the sample e-commerce schema."""
    ctx = {"connection": sqlite_connection}
    input = NodeInput(
        query_text="SELECT tier, COUNT(*) as cnt FROM users GROUP BY tier ORDER BY tier",
        context=ctx,
        config={"max_rows": 100},
    )
    output = await exec_node.execute(input)
    assert output.errors == []
    assert output.result is not None
    assert len(output.result["rows"]) == 3  # bronze, gold, silver
