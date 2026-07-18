"""Multi-database connection factory.

Supports 11 database types via a unified factory interface.
Phase 1 implements: SQLite, DuckDB, PostgreSQL, MySQL.
"""

import asyncio
import contextlib
from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class DatabaseType(StrEnum):
    """Supported database types (11 total)."""

    SQLITE = "sqlite"
    DUCKDB = "duckdb"
    POSTGRESQL = "postgresql"
    MYSQL = "mysql"
    SNOWFLAKE = "snowflake"
    STARROCKS = "starrocks"
    BIGQUERY = "bigquery"
    REDSHIFT = "redshift"
    CLICKHOUSE = "clickhouse"
    DATABRICKS = "databricks"
    TRINO = "trino"


@dataclass
class ConnectionInfo:
    """Metadata about an active database connection."""

    db_id: str
    db_type: DatabaseType
    alias: str = ""
    host: str = ""
    port: int = 0
    database_name: str = ""
    status: str = "connected"  # "connected", "error", "disconnected"


class ConnectionFactory:
    """Factory for creating and managing database connections.

    Returns native connections where possible, with sqlalchemy Engine as fallback.
    """

    # Registry of active connections
    _connections: dict[str, Any] = {}
    _infos: dict[str, ConnectionInfo] = {}
    _mysql_pools: dict[int, Any] = {}  # Track MySQL connection pools for proper cleanup

    @classmethod
    async def create(
        cls, db_type: DatabaseType, config: dict, db_id: str = ""
    ) -> Any:
        """Create a new database connection.

        Args:
            db_type: The database type enum.
            config: Connection config dict (keys vary by db_type).
            db_id: Unique identifier for this connection.

        Returns:
            A native connection object (duckdb.DuckDBPyConnection,
            asyncpg.Connection, sqlite3.Connection, etc.).

        Raises:
            ValueError: If db_type is unsupported.
            ConnectionError: If connection fails.
        """
        conn = await cls._connect(db_type, config)
        if db_id:
            cls._connections[db_id] = conn
            cls._infos[db_id] = ConnectionInfo(
                db_id=db_id,
                db_type=db_type,
                alias=config.get("alias", db_id),
                host=config.get("host", ""),
                port=config.get("port", 0),
                database_name=config.get("database", config.get("path", "")),
            )
        return conn

    @classmethod
    async def _connect(cls, db_type: DatabaseType, config: dict) -> Any:
        """Internal dispatch to the appropriate connection method."""
        match db_type:
            case DatabaseType.SQLITE:
                return cls._connect_sqlite(config)
            case DatabaseType.DUCKDB:
                return cls._connect_duckdb(config)
            case DatabaseType.POSTGRESQL:
                return await cls._connect_postgresql(config)
            case DatabaseType.MYSQL:
                return await cls._connect_mysql(config)
            case (
                DatabaseType.SNOWFLAKE
                | DatabaseType.STARROCKS
                | DatabaseType.BIGQUERY
                | DatabaseType.REDSHIFT
                | DatabaseType.CLICKHOUSE
                | DatabaseType.DATABRICKS
                | DatabaseType.TRINO
            ):
                raise NotImplementedError(
                    f"Database type '{db_type}' is not yet implemented (Phase 2+)"
                )
            case _:
                raise ValueError(f"Unsupported database type: {db_type}")

    @classmethod
    def _connect_sqlite(cls, config: dict) -> Any:
        import sqlite3

        path = config.get("path", ":memory:")
        check_same_thread = config.get("check_same_thread", False)
        conn = sqlite3.connect(path, check_same_thread=check_same_thread)
        conn.row_factory = sqlite3.Row
        return conn

    @classmethod
    def _connect_duckdb(cls, config: dict) -> Any:
        import duckdb

        path = config.get("path", ":memory:")
        return duckdb.connect(path)

    @classmethod
    async def _connect_postgresql(cls, config: dict) -> Any:
        try:
            import asyncpg

            return await asyncpg.connect(
                host=config["host"],
                port=config.get("port", 5432),
                user=config["user"],
                password=config.get("password", ""),
                database=config["database"],
            )
        except ImportError as e:
            raise ImportError(
                "asyncpg is required for PostgreSQL. Install with: pip install asyncpg"
            ) from e

    @classmethod
    async def _connect_mysql(cls, config: dict) -> Any:
        try:
            import aiomysql

            pool = await aiomysql.create_pool(
                host=config["host"],
                port=config.get("port", 3306),
                user=config["user"],
                password=config.get("password", ""),
                db=config["database"],
                autocommit=True,
            )
            conn = await pool.acquire()
            # Store the pool so it can be properly closed later
            cls._mysql_pools[id(conn)] = pool
            return conn
        except ImportError as e:
            raise ImportError(
                "aiomysql is required for MySQL. Install with: pip install aiomysql"
            ) from e

    @classmethod
    def get(cls, db_id: str) -> Any:
        """Get an active connection by ID."""
        conn = cls._connections.get(db_id)
        if conn is None:
            raise KeyError(f"No connection found for db_id='{db_id}'")
        return conn

    @classmethod
    def get_info(cls, db_id: str) -> ConnectionInfo:
        """Get connection metadata."""
        info = cls._infos.get(db_id)
        if info is None:
            raise KeyError(f"No connection info found for db_id='{db_id}'")
        return info

    @classmethod
    def list_all(cls) -> list[ConnectionInfo]:
        """List all active connections."""
        return list(cls._infos.values())

    @classmethod
    def close(cls, db_id: str) -> None:
        """Close and remove a connection."""
        conn = cls._connections.pop(db_id, None)
        info = cls._infos.pop(db_id, None)
        if conn is not None:
            # Release MySQL pool connection and close the pool
            pool = cls._mysql_pools.pop(id(conn), None)
            if pool is not None:
                with contextlib.suppress(Exception):
                    pool.close()
                    # Schedule pool termination in event loop
                    try:
                        loop = asyncio.get_event_loop()
                        if loop.is_running():
                            loop.call_soon(lambda: asyncio.ensure_future(pool.wait_closed()))
                    except RuntimeError:
                        pass
            with contextlib.suppress(Exception):
                conn.close()

    @classmethod
    def close_all(cls) -> None:
        """Close all active connections."""
        for conn in list(cls._connections.values()):
            # Release MySQL pools
            pool = cls._mysql_pools.pop(id(conn), None)
            if pool is not None:
                with contextlib.suppress(Exception):
                    pool.close()
                    try:
                        loop = asyncio.get_event_loop()
                        if loop.is_running():
                            loop.call_soon(lambda p=pool: asyncio.ensure_future(p.wait_closed()))
                    except RuntimeError:
                        pass
            with contextlib.suppress(Exception):
                conn.close()
        cls._connections.clear()
        cls._infos.clear()
        cls._mysql_pools.clear()
