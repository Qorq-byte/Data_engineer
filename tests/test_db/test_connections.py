"""Tests for ConnectionFactory."""

import pytest

from app.db.connections import ConnectionFactory, DatabaseType


def test_connect_sqlite_memory():
    """Test creating an in-memory SQLite connection."""
    conn = ConnectionFactory._connect_sqlite({"path": ":memory:"})
    assert conn is not None
    conn.execute("CREATE TABLE test (id INT)")
    conn.execute("INSERT INTO test VALUES (1)")
    result = conn.execute("SELECT * FROM test").fetchone()
    assert result[0] == 1
    conn.close()


def test_connect_sqlite_file(tmp_path):
    """Test creating a file-based SQLite connection."""
    db_path = tmp_path / "test.db"
    conn = ConnectionFactory._connect_sqlite({"path": str(db_path)})
    conn.execute("CREATE TABLE t (x TEXT)")
    conn.commit()
    conn.close()
    assert db_path.exists()


@pytest.mark.asyncio
async def test_connection_factory_create_sqlite():
    """Test ConnectionFactory.create with SQLite."""
    conn = await ConnectionFactory.create(
        DatabaseType.SQLITE,
        {"path": ":memory:"},
        db_id="test_sqlite",
    )
    assert conn is not None

    info = ConnectionFactory.get_info("test_sqlite")
    assert info.db_type == DatabaseType.SQLITE
    assert info.status == "connected"

    ConnectionFactory.close("test_sqlite")


@pytest.mark.asyncio
async def test_unsupported_database_raises():
    """Test that unimplemented databases raise NotImplementedError."""
    with pytest.raises(NotImplementedError):
        await ConnectionFactory._connect(DatabaseType.SNOWFLAKE, {})



def test_connection_factory_list_and_close():
    """Test listing and closing connections."""
    conn = ConnectionFactory._connect_sqlite({"path": ":memory:"})
    ConnectionFactory._connections["test_list"] = conn
    ConnectionFactory._infos["test_list"] = type(
        "info", (), {"db_id": "test_list", "db_type": "sqlite"}
    )()

    assert len(ConnectionFactory.list_all()) >= 1

    ConnectionFactory.close("test_list")
    assert "test_list" not in ConnectionFactory._connections
