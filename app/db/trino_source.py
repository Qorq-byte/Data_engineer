"""Bounded Trino statement protocol with same-origin polling and cancellation."""

from __future__ import annotations

import asyncio
import json
import math
from contextlib import suppress
from typing import Any

import httpx
import sqlglot
from sqlglot import exp

from app.db.execution_types import ExecutionResult
from app.models.schema import ColumnSchema, SchemaSnapshot, TableSchema
from app.security.sql_policy import require_read_query

_MAX_RESPONSE_BYTES = 8 * 1024 * 1024
_MAX_SCHEMA_COLUMNS = 20_000


class TrinoConnection:
    def __init__(self, client: httpx.AsyncClient, catalog: str, schema: str) -> None:
        self._client = client
        self.catalog = catalog
        self.schema = schema

    @classmethod
    async def connect(cls, config: dict[str, Any]) -> TrinoConnection:
        database = config.get("database")
        if not isinstance(database, str) or database.count(".") != 1:
            raise ValueError("Trino database must be catalog.schema")
        catalog, schema = database.split(".")
        if not catalog or not schema:
            raise ValueError("Trino catalog and schema are required")
        secure = config.get("secure", False)
        if type(secure) is not bool:
            raise ValueError("Trino secure must be a boolean")
        host = config["host"]
        password = config.get("password", "")
        if not secure and (password or host not in {"127.0.0.1", "::1", "localhost"}):
            raise ValueError("Trino HTTP is only supported for unauthenticated loopback access")
        user = config["user"]
        url = httpx.URL(
            scheme="https" if secure else "http",
            host=host,
            port=int(config.get("port", 443 if secure else 8080)),
            path="/",
        )
        client = httpx.AsyncClient(
            base_url=url,
            auth=(user, password) if password else None,
            headers={"X-Trino-User": user, "X-Trino-Catalog": catalog, "X-Trino-Schema": schema},
            trust_env=False,
            limits=httpx.Limits(max_connections=1, max_keepalive_connections=1),
        )
        connection = cls(client, catalog, schema)
        try:
            _, rows = await connection._request(
                "SELECT current_catalog, current_schema", timeout=10, row_limit=1
            )
            if rows != [[catalog, schema]]:
                raise ConnectionError("Trino did not select the configured catalog and schema")
            _, rows = await connection._request(
                "SELECT schema_name FROM information_schema.schemata "
                f"WHERE schema_name = {exp.Literal.string(schema).sql(dialect='trino')}",
                timeout=10,
                row_limit=1,
            )
            if rows != [[schema]]:
                raise ConnectionError("Trino schema is unavailable")
        except BaseException:
            await connection.close()
            raise
        return connection

    async def close(self) -> None:
        await self._client.aclose()

    def _poll_url(self, value: object) -> httpx.URL | None:
        if value is None:
            return None
        if not isinstance(value, str):
            raise ValueError("Invalid Trino continuation URI")
        url = httpx.URL(value)
        origin = self._client.base_url
        if (
            (url.scheme, url.host, url.port) != (origin.scheme, origin.host, origin.port)
            or url.userinfo or url.fragment or not url.path.startswith("/v1/statement/")
        ):
            raise ValueError("Trino returned a continuation outside its statement endpoint")
        return url

    async def _read_page(
        self, method: str, url: str | httpx.URL, budget: int, *,
        content: str | None = None, headers: dict[str, str] | None = None,
    ) -> tuple[dict[str, Any], int]:
        payload = bytearray()
        async with self._client.stream(
            method, url, headers={"Accept-Encoding": "identity", **(headers or {})},
            timeout=None, content=content,
        ) as response:
            response.raise_for_status()
            async for chunk in response.aiter_bytes(chunk_size=65_536):
                if len(payload) + len(chunk) > budget:
                    raise ValueError("Trino result exceeds the response size limit")
                payload.extend(chunk)
        try:
            result = json.loads(payload)
        except (ValueError, RecursionError):
            raise ValueError("Invalid Trino query response") from None
        if not isinstance(result, dict):
            raise ValueError("Invalid Trino query response")
        return result, budget - len(payload)

    async def _request(
        self, sql: str, *, timeout: float, row_limit: int
    ) -> tuple[list[str], list[list[Any]]]:
        columns: list[str] = []
        rows: list[list[Any]] = []
        next_url = None
        server_deadline = f"query_max_run_time={max(1, math.ceil(timeout))}s"
        try:
            async with asyncio.timeout(timeout):
                page, budget = await self._read_page(
                    "POST", "/v1/statement", _MAX_RESPONSE_BYTES, content=sql,
                    headers={"X-Trino-Session": server_deadline},
                )
                while True:
                    next_url = self._poll_url(page.get("nextUri"))
                    if "error" in page:
                        raise ConnectionError("Trino statement failed")
                    if "columns" in page:
                        columns = [column["name"] for column in page["columns"]]
                        if not all(isinstance(column, str) for column in columns):
                            raise ValueError("Invalid Trino result columns")
                    data = page.get("data", [])
                    if (
                        not isinstance(data, list)
                        or any(
                            not isinstance(row, list) or len(row) != len(columns) for row in data
                        )
                        or len(rows) + len(data) > row_limit
                    ):
                        raise ValueError("Invalid or oversized Trino result page")
                    rows.extend(data)
                    if next_url is None:
                        return columns, rows
                    page, budget = await self._read_page("GET", next_url, budget)
        except (httpx.HTTPError, KeyError, TypeError):
            raise ConnectionError("Trino HTTP query failed") from None
        finally:
            if next_url is not None:
                with suppress(httpx.HTTPError, TimeoutError):
                    async with asyncio.timeout(2), self._client.stream("DELETE", next_url):
                        pass

    async def query(self, sql: str, max_rows: int, timeout: float) -> ExecutionResult:
        require_read_query(sql, dialect="trino")
        statements = [
            item for item in sqlglot.parse(sql, read="trino")
            if item is not None and not isinstance(item, exp.Semicolon)
        ]
        query = statements[0]
        if not isinstance(query, exp.Select):
            raise ValueError("Trino execution currently requires a SELECT query")
        for table in query.find_all(exp.Table):
            if (table.catalog and table.catalog != self.catalog) or (
                table.db and table.db != self.schema
            ):
                raise ValueError("Trino query references a different catalog or schema")
        cap = max_rows + 1
        limit = query.args.get("limit")
        if limit is not None:
            count = limit.expression
            if (
                not isinstance(limit, exp.Limit) or limit.args.get("limit_options")
                or not isinstance(count, exp.Literal)
                or not count.is_int or int(count.this) < 0
            ):
                raise ValueError("Trino requires a literal nonnegative LIMIT")
            cap = min(cap, int(count.this))
        query.limit(cap, copy=False)
        columns, rows = await self._request(
            query.sql(dialect="trino", comments=False), timeout=timeout, row_limit=cap
        )
        return ExecutionResult(
            columns=columns, rows=rows[:max_rows], row_count=min(len(rows), max_rows),
            truncated=len(rows) > max_rows,
        )

    async def extract_schema(self) -> SchemaSnapshot:
        schema = exp.Literal.string(self.schema).sql(dialect="trino")
        _, rows = await self._request(
            "SELECT table_name, column_name, data_type, is_nullable "
            f"FROM information_schema.columns WHERE table_schema = {schema} "
            f"ORDER BY table_name, ordinal_position LIMIT {_MAX_SCHEMA_COLUMNS + 1}",
            timeout=15,
            row_limit=_MAX_SCHEMA_COLUMNS + 1,
        )
        if len(rows) > _MAX_SCHEMA_COLUMNS:
            raise ValueError("Trino schema exceeds the column limit")
        tables: dict[str, TableSchema] = {}
        for table, name, data_type, nullable in rows:
            if table not in tables:
                tables[table] = TableSchema(
                    name=table, schema_name=self.schema, catalog_name=self.catalog
                )
            tables[table].columns.append(
                ColumnSchema(name=name, type=data_type, nullable=nullable == "YES")
            )
        return SchemaSnapshot(
            database_type="trino", database_name=f"{self.catalog}.{self.schema}", tables=tables
        )
