"""Read-only StarRocks access through the native MySQL wire protocol."""

from __future__ import annotations

import asyncio
import math
import ssl
from contextlib import suppress
from typing import Any, TypedDict

import aiomysql
import sqlglot
from sqlglot import exp

from app.db.execution_types import ExecutionResult
from app.db.mysql_query import bounded_mysql_query
from app.models.schema import ColumnSchema, SchemaSnapshot, TableSchema
from app.security.sql_policy import require_read_query

_CATALOG = "default_catalog"
_MAX_SCHEMA_COLUMNS = 20_000


class _Options(TypedDict):
    host: str
    port: int
    user: str
    password: str
    db: str
    autocommit: bool
    connect_timeout: float
    ssl: ssl.SSLContext | None


class StarRocksConnection:
    def __init__(self, pool: aiomysql.Pool, options: _Options) -> None:
        self._pool = pool
        self._options = options
        self.database = options["db"]

    @classmethod
    async def connect(cls, config: dict[str, Any]) -> StarRocksConnection:
        database = config.get("database")
        if not isinstance(database, str) or not database:
            raise ValueError("StarRocks database is required")
        secure = config.get("secure", False)
        if type(secure) is not bool:
            raise ValueError("StarRocks secure must be a boolean")
        host = config["host"]
        if not secure and host not in {"127.0.0.1", "::1", "localhost"}:
            raise ValueError("StarRocks remote connections require TLS")
        options = _Options(
            host=host,
            port=int(config.get("port", 9030)),
            user=config["user"],
            password=config.get("password", ""),
            db=database,
            autocommit=True,
            connect_timeout=4,
            ssl=ssl.create_default_context() if secure else None,
        )
        try:
            async with asyncio.timeout(5):
                pool = await aiomysql.create_pool(minsize=1, maxsize=1, **options)
        except aiomysql.Error:
            raise ConnectionError("StarRocks connection failed") from None
        connection = cls(pool, options)
        try:
            _, rows = await connection._request("SELECT DATABASE()", 1, 4)
            if rows != [(database,)]:
                raise ConnectionError("StarRocks did not select the configured database")
        except BaseException:
            await connection.close()
            raise
        return connection

    async def close(self) -> None:
        self._pool.close()
        await self._pool.wait_closed()

    async def _cancel_query(self, thread_id: int) -> None:
        with suppress(aiomysql.Error, TimeoutError, OSError):
            async with (
                asyncio.timeout(2),
                aiomysql.connect(**self._options) as conn,
                conn.cursor() as cursor,
            ):
                await cursor.execute("KILL QUERY %s", (thread_id,))

    async def _request(
        self, sql: str, row_limit: int, timeout: float, args: tuple[object, ...] = ()
    ) -> tuple[list[str], list[tuple[Any, ...]]]:
        conn = None
        try:
            async with asyncio.timeout(timeout):
                conn = await self._pool.acquire()
                async with conn.cursor(aiomysql.SSCursor) as cursor:
                    await cursor.execute(
                        "SET SESSION query_timeout = %s", (max(1, math.ceil(timeout)),)
                    )
                    await cursor.execute(sql, args or None)
                    columns = [str(item[0]) for item in cursor.description or ()]
                    return columns, list(await cursor.fetchmany(row_limit))
        except (TimeoutError, asyncio.CancelledError):
            if conn is not None:
                thread_id = conn.thread_id()
                try:
                    await self._cancel_query(thread_id)
                finally:
                    conn.close()
            raise
        except aiomysql.Error:
            raise ConnectionError("StarRocks SQL query failed") from None
        finally:
            if conn is not None:
                self._pool.release(conn)

    async def query(self, sql: str, max_rows: int, timeout: float) -> ExecutionResult:
        if "/*+" in sql:
            raise ValueError("StarRocks query hints are managed by the connector")
        require_read_query(sql, dialect="starrocks")
        statements = [
            item for item in sqlglot.parse(sql, read="starrocks")
            if item is not None and not isinstance(item, exp.Semicolon)
        ]
        for table in statements[0].find_all(exp.Table):
            if (table.catalog and table.catalog != _CATALOG) or (
                table.db and table.db != self.database
            ):
                raise ValueError("StarRocks query references a different catalog or database")
        bounded = bounded_mysql_query(sql, max_rows + 1)
        columns, rows = await self._request(bounded, max_rows + 1, timeout)
        return ExecutionResult(
            columns=columns, rows=[list(row) for row in rows[:max_rows]],
            row_count=min(len(rows), max_rows), truncated=len(rows) > max_rows,
        )

    async def extract_schema(self) -> SchemaSnapshot:
        _, rows = await self._request(
            "SELECT TABLE_NAME, COLUMN_NAME, COLUMN_TYPE, IS_NULLABLE, COLUMN_COMMENT "
            "FROM information_schema.columns WHERE TABLE_SCHEMA = %s "
            "ORDER BY TABLE_NAME, ORDINAL_POSITION "
            f"LIMIT {_MAX_SCHEMA_COLUMNS + 1}",
            _MAX_SCHEMA_COLUMNS + 1, 10, (self.database,),
        )
        if len(rows) > _MAX_SCHEMA_COLUMNS:
            raise ValueError("StarRocks schema exceeds the column limit")
        tables: dict[str, TableSchema] = {}
        for table, name, data_type, nullable, comment in rows:
            if table not in tables:
                tables[table] = TableSchema(
                    name=table, schema_name=self.database, catalog_name=_CATALOG
                )
            tables[table].columns.append(
                ColumnSchema(
                    name=name, type=data_type, nullable=nullable == "YES", comment=comment or None
                )
            )
        return SchemaSnapshot(
            database_type="starrocks", database_name=self.database, tables=tables
        )
