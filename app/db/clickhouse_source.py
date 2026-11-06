"""Bounded, read-only access to a single ClickHouse database over HTTP."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
import sqlglot
from sqlglot import exp

from app.db.execution_types import ExecutionResult
from app.models.schema import ColumnSchema, SchemaSnapshot, TableSchema
from app.security.sql_policy import require_read_query

_MAX_RESPONSE_BYTES = 8 * 1024 * 1024
_MAX_SCHEMA_COLUMNS = 20_000


class ClickHouseConnection:
    def __init__(self, client: httpx.AsyncClient, database: str) -> None:
        self._client = client
        self.database = database

    @classmethod
    async def connect(cls, config: dict[str, Any]) -> ClickHouseConnection:
        database = config.get("database")
        if not isinstance(database, str) or not database:
            raise ValueError("ClickHouse database is required")
        secure = config.get("secure", False)
        if type(secure) is not bool:
            raise ValueError("ClickHouse secure must be a boolean")
        url = httpx.URL(
            scheme="https" if secure else "http",
            host=config["host"],
            port=int(config.get("port", 8443 if secure else 8123)),
            path="/",
        )
        client = httpx.AsyncClient(
            base_url=url,
            auth=(config["user"], config.get("password", "")),
            trust_env=False,
            limits=httpx.Limits(max_connections=1, max_keepalive_connections=1),
        )
        connection = cls(client, database)
        try:
            _, rows = await connection._request(
                "SELECT getSetting('readonly'), "
                "getSetting('cancel_http_readonly_queries_on_client_close'), "
                "getSetting('max_execution_time')",
                timeout=5,
            )
            if not (
                len(rows) == 1 and len(rows[0]) == 3
                and rows[0][0] == 1 and rows[0][1] is True
                and isinstance(rows[0][2], int | float) and rows[0][2] > 0
            ):
                raise ConnectionError(
                    "ClickHouse requires a native read-only profile with cancellation "
                    "and a server execution deadline"
                )
        except BaseException:
            await connection.close()
            raise
        return connection

    async def close(self) -> None:
        await self._client.aclose()

    async def _request(
        self, sql: str, *, timeout: float, parameters: dict[str, str] | None = None
    ) -> tuple[list[str], list[list[Any]]]:
        params = {"database": self.database, **(parameters or {})}
        payload = bytearray()
        try:
            async with asyncio.timeout(timeout), self._client.stream(
                "POST",
                "/",
                params=params,
                content=f"{sql}\nFORMAT JSONCompact",
                headers={"Accept-Encoding": "identity"},
                timeout=None,
            ) as response:
                response.raise_for_status()
                async for chunk in response.aiter_bytes(chunk_size=65_536):
                    if len(payload) + len(chunk) > _MAX_RESPONSE_BYTES:
                        raise ValueError("ClickHouse result exceeds the response size limit")
                    payload.extend(chunk)
        except httpx.HTTPError:
            raise ConnectionError("ClickHouse HTTP query failed") from None
        try:
            result = json.loads(payload)
            columns = [column["name"] for column in result["meta"]]
            rows = result["data"]
            if (
                not all(isinstance(column, str) for column in columns)
                or not isinstance(rows, list)
                or any(not isinstance(row, list) or len(row) != len(columns) for row in rows)
                or "exception" in result
            ):
                raise ValueError
        except (ValueError, TypeError, KeyError, RecursionError):
            raise ValueError("Invalid ClickHouse query response") from None
        return columns, rows

    async def query(self, sql: str, max_rows: int, timeout: float) -> ExecutionResult:
        bounded = self._bounded_query(sql, max_rows + 1)
        columns, rows = await self._request(bounded, timeout=timeout)
        return ExecutionResult(
            columns=columns,
            rows=rows[:max_rows],
            row_count=min(len(rows), max_rows),
            truncated=len(rows) > max_rows,
        )

    def _bounded_query(self, sql: str, row_limit: int) -> str:
        require_read_query(sql, dialect="clickhouse")
        statements = [
            item for item in sqlglot.parse(sql, read="clickhouse")
            if item is not None and not isinstance(item, exp.Semicolon)
        ]
        query = statements[0]
        if not isinstance(query, exp.Select):
            raise ValueError("ClickHouse execution currently requires a SELECT query")
        for item in query.walk():
            if isinstance(item, exp.Query) and (
                item.args.get("settings") or item.args.get("format")
            ):
                raise ValueError("ClickHouse SETTINGS and FORMAT are managed by the connector")
            if isinstance(item, exp.Table) and (
                item.catalog or (item.db and item.db != self.database)
            ):
                raise ValueError("ClickHouse query references a different database")
        limit = query.args.get("limit")
        if limit is not None:
            count = limit.expression
            if (
                not isinstance(limit, exp.Limit) or limit.expressions
                or limit.args.get("limit_options") or not isinstance(count, exp.Literal)
                or not count.is_int or int(count.this) < 0
            ):
                raise ValueError("ClickHouse requires a literal nonnegative LIMIT")
            row_limit = min(row_limit, int(count.this))
        query.limit(row_limit, copy=False)
        return query.sql(dialect="clickhouse", comments=False)

    async def extract_schema(self) -> SchemaSnapshot:
        _, rows = await self._request(
            "SELECT table, name, type, is_in_primary_key, comment "
            "FROM system.columns WHERE database = {db:String} ORDER BY table, position "
            f"LIMIT {_MAX_SCHEMA_COLUMNS + 1}",
            timeout=10,
            parameters={"param_db": self.database},
        )
        if len(rows) > _MAX_SCHEMA_COLUMNS:
            raise ValueError("ClickHouse schema exceeds the column limit")
        tables: dict[str, TableSchema] = {}
        for table, name, data_type, primary, comment in rows:
            if table not in tables:
                tables[table] = TableSchema(name=table, schema_name=self.database)
            tables[table].columns.append(
                ColumnSchema(
                    name=name,
                    type=data_type,
                    nullable=data_type.startswith(("Nullable(", "LowCardinality(Nullable(")),
                    is_primary_key=bool(primary),
                    comment=comment or None,
                )
            )
        return SchemaSnapshot(
            database_type="clickhouse", database_name=self.database, tables=tables
        )
