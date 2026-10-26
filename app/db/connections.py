"""Multi-database connection factory.

Supports 11 database types via a unified factory interface.
Phase 1 implements: SQLite, DuckDB, PostgreSQL, MySQL.
"""

import asyncio
import inspect
from contextlib import suppress
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
    _offload_connections: dict[int, Any] = {}
    _mysql_pools: dict[int, Any] = {}  # Track MySQL connection pools for proper cleanup

    @classmethod
    async def create(cls, db_type: DatabaseType, config: dict[str, Any], db_id: str = "") -> Any:
        from app.db.runtime import gate

        async with gate(("connect", db_id)):
            return await cls._create(db_type, config, db_id)

    @classmethod
    async def _create(cls, db_type: DatabaseType, config: dict[str, Any], db_id: str = "") -> Any:
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
        try:
            if db_id:
                if db_id in cls._connections:
                    await cls.aclose(db_id)
                if db_type == DatabaseType.SQLITE and not config.get("check_same_thread", False):
                    cls._offload_connections[id(conn)] = conn
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
        except BaseException:
            # A replacement may be waiting for the old handle's lease. Its newly
            # connected candidate must not leak when that wait is cancelled.
            if db_id and cls._connections.get(db_id) is conn:
                cls._connections.pop(db_id, None)
                cls._infos.pop(db_id, None)
                cls._offload_connections.pop(id(conn), None)
            await cls._close_handle(conn)
            raise

    @classmethod
    async def _connect(cls, db_type: DatabaseType, config: dict[str, Any]) -> Any:
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
    def _connect_sqlite(cls, config: dict[str, Any]) -> Any:
        import sqlite3

        path = config.get("path", ":memory:")
        check_same_thread = config.get("check_same_thread", False)
        conn = sqlite3.connect(path, check_same_thread=check_same_thread)
        conn.row_factory = sqlite3.Row
        return conn

    @classmethod
    def _connect_duckdb(cls, config: dict[str, Any]) -> Any:
        import duckdb

        path = config.get("path", ":memory:")
        return duckdb.connect(path, config={"enable_external_access": False})

    @classmethod
    async def _connect_postgresql(cls, config: dict[str, Any]) -> Any:
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
    async def _connect_mysql(cls, config: dict[str, Any]) -> Any:
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
            try:
                conn = await pool.acquire()
            except BaseException:
                pool.close()
                await pool.wait_closed()
                raise
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
    def is_current(cls, db_id: str, connection: object) -> bool:
        """Compare identity without reconnecting or handing out a replacement."""
        return cls._connections.get(db_id) is connection

    @classmethod
    def list_all(cls) -> list[ConnectionInfo]:
        """List all active connections."""
        return list(cls._infos.values())

    @classmethod
    def describe_capabilities(cls) -> dict[str, Any]:
        """Report connector implementation and registry counts without connecting.

        Module discovery is not importability, server reachability or health.
        Keep credentials and connection identifiers out of this aggregate view.
        """
        from importlib.util import find_spec

        implementations = {
            DatabaseType.SQLITE: ("sqlite3", "native_single_connection"),
            DatabaseType.DUCKDB: ("duckdb", "native_single_connection"),
            DatabaseType.POSTGRESQL: ("asyncpg", "async_single_connection"),
            DatabaseType.MYSQL: ("aiomysql", "pool_acquired_connection"),
        }
        counts = dict.fromkeys(DatabaseType, 0)
        for db_id in list(cls._connections):
            info = cls._infos.get(db_id)
            if info is not None and info.db_type in counts:
                counts[info.db_type] += 1
        connectors = []
        for db_type in DatabaseType:
            implementation = implementations.get(db_type)
            module, management = implementation or (None, "not_implemented")
            discoverable = None
            if module is not None:
                with suppress(ImportError, ValueError, AttributeError):
                    discoverable = find_spec(module) is not None
            connectors.append(
                {
                    "type": db_type.value,
                    "implemented": implementation is not None,
                    "driver_module": module,
                    "module_discoverable": discoverable,
                    "management": management,
                    "registered_connections": counts[db_type],
                }
            )
        return {
            "connectors": connectors,
            "connection_pool_size": None,
            "connection_timeout_s": None,
            "scope": "current process ConnectionFactory registry; not connection health",
            "configuration_source": "connector implementation and driver defaults",
        }

    @classmethod
    async def aclose(cls, db_id: str) -> None:
        """Wait for users of a connection, then release native/async resources."""
        from app.db.runtime import lease
        from app.db.schema_extractor import SchemaExtractor

        conn = cls._connections.get(db_id)
        if conn is None:
            return
        async with lease(conn):
            if cls._connections.get(db_id) is not conn:
                return
            cls._connections.pop(db_id, None)
            cls._infos.pop(db_id, None)
            cls._offload_connections.pop(id(conn), None)
            SchemaExtractor.invalidate(conn)
            await cls._close_handle(conn)

    @classmethod
    async def _close_handle(cls, conn: Any) -> None:
        async def dispose() -> None:
            pool = cls._mysql_pools.pop(id(conn), None)
            if pool is not None:
                pool.release(conn)
                pool.close()
                await pool.wait_closed()
            else:
                assert conn is not None
                result = conn.close()
                if inspect.isawaitable(result):
                    await result

        task = asyncio.create_task(dispose())
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            while not task.done():
                with suppress(Exception, asyncio.CancelledError):
                    await asyncio.shield(task)
            with suppress(Exception, asyncio.CancelledError):
                task.result()
            raise

    @classmethod
    async def aclose_all(cls) -> None:
        for db_id in list(cls._connections):
            await cls.aclose(db_id)

    @classmethod
    def close(cls, db_id: str) -> None:
        """Legacy sync adapter; async applications must await aclose()."""
        conn = cls._connections.get(db_id)
        if conn is None:
            return
        if id(conn) not in cls._mysql_pools and not inspect.iscoroutinefunction(conn.close):
            from app.db.schema_extractor import SchemaExtractor

            cls._connections.pop(db_id, None)
            cls._infos.pop(db_id, None)
            cls._offload_connections.pop(id(conn), None)
            SchemaExtractor.invalidate(conn)
            conn.close()
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            asyncio.run(cls.aclose(db_id))
        else:
            loop.create_task(cls.aclose(db_id))

    @classmethod
    def close_all(cls) -> None:
        """Legacy adapter; use await aclose_all() during application shutdown."""
        for db_id in list(cls._connections):
            cls.close(db_id)
