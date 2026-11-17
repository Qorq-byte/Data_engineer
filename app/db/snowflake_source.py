"""Bounded read-only Snowflake access through the optional Python connector."""

from __future__ import annotations

import asyncio
import math
import re
import time
from collections.abc import Callable, Coroutine
from contextlib import suppress
from typing import Any

import sqlglot
from sqlglot import exp

from app.db.execution_types import ExecutionResult
from app.models.schema import ColumnSchema, SchemaSnapshot, TableSchema
from app.security.sql_policy import require_read_query

_MAX_SCHEMA_COLUMNS = 20_000
_QUERY_POLL_INTERVAL_SECONDS = 0.2
_CANCEL_CONFIRM_TIMEOUT_SECONDS = 5.0
_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]*$")


async def _drain_task(task: asyncio.Task[Any]) -> Any | None:
    """Wait until a shielded worker finishes before releasing its connection lease."""
    while not task.done():
        try:
            await asyncio.shield(task)
        except (asyncio.CancelledError, Exception):
            continue
    if task.cancelled():
        return None
    try:
        return task.result()
    except Exception:
        return None


class SnowflakeConnection:
    """Single-session Snowflake adapter with SQL and server-side deadline guards."""

    def __init__(self, connection: Any, database: str, schema: str) -> None:
        self._connection = connection
        self.database = database
        self.schema = schema
        self._closed = False
        self._broken = False
        self._last_query_id: str | None = None

    @property
    def last_query_id(self) -> str | None:
        """Last submitted query ID, for native terminal-state verification (no SQL text)."""
        return self._last_query_id

    @classmethod
    async def connect(cls, config: dict[str, Any]) -> SnowflakeConnection:
        allowed = {
            "account", "user", "password", "warehouse", "database", "schema", "role",
            "host", "port", "alias",
        }
        unknown = sorted(set(config) - allowed)
        if unknown:
            raise ValueError(f"Unsupported Snowflake connection settings: {', '.join(unknown)}")

        account = config.get("account")
        user = config.get("user")
        password = config.get("password")
        if (
            not isinstance(account, str)
            or not account.strip()
            or any(c.isspace() for c in account)
        ):
            raise ValueError("Snowflake account is required")
        if not isinstance(user, str) or not user.strip():
            raise ValueError("Snowflake user is required")
        if not isinstance(password, str) or not password:
            raise ValueError("Snowflake password authentication is required")

        database = cls._identifier(config.get("database"), "database")
        schema = cls._identifier(config.get("schema"), "schema")
        warehouse = cls._identifier(config.get("warehouse"), "warehouse")
        role_value = config.get("role")
        if role_value is None or (isinstance(role_value, str) and not role_value.strip()):
            role = None
        elif isinstance(role_value, str):
            role = cls._identifier(role_value, "role")
        else:
            raise ValueError("Snowflake role must be a string when provided")
        host = config.get("host")
        if host is not None and (not isinstance(host, str) or not host.strip()):
            raise ValueError("Snowflake host must be a non-empty string when provided")
        port = config.get("port", 443)
        if type(port) is not int or port != 443:
            raise ValueError("Snowflake connections require TLS on port 443")

        try:
            from importlib import import_module

            snowflake_connector = import_module("snowflake.connector")
        except ImportError:
            raise ImportError(
                "Snowflake requires the optional driver; install with "
                "pip install -e '.[snowflake]'"
            ) from None

        from app.config.settings import settings

        connect_args: dict[str, Any] = {
            "account": account.strip(),
            "user": user.strip(),
            "password": password,
            "warehouse": warehouse,
            "database": database,
            "schema": schema,
            "port": port,
            "login_timeout": 10,
            "request_timeout": 10,
            "client_prefetch_threads": 1,
            "autocommit": True,
            "session_parameters": {
                "ABORT_DETACHED_QUERY": True,
                "STATEMENT_TIMEOUT_IN_SECONDS": max(
                    1, math.ceil(settings.statement_timeout_ms / 1000)
                ),
            },
        }
        if role is not None:
            connect_args["role"] = role
        if host is not None:
            connect_args["host"] = host.strip()

        connect_task = asyncio.create_task(
            asyncio.to_thread(snowflake_connector.connect, **connect_args)
        )
        try:
            connection = await asyncio.shield(connect_task)
        except asyncio.CancelledError:
            connection = await _drain_task(connect_task)
            if connection is not None:
                close_task = asyncio.create_task(asyncio.to_thread(connection.close))
                await _drain_task(close_task)
            raise
        except Exception as exc:
            raise ConnectionError(
                f"Snowflake connection failed ({type(exc).__name__})"
            ) from None

        return cls(connection, database, schema)

    @staticmethod
    def _identifier(value: object, field: str) -> str:
        if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
            raise ValueError(
                f"Snowflake {field} must be an unquoted SQL identifier "
                "(letters, digits, underscore, or dollar sign)"
            )
        return value.upper()

    def _ensure_open(self) -> None:
        if self._closed or self._broken:
            raise ConnectionError("Snowflake connection is closed; reconnect before querying")

    async def _run_driver(
        self,
        function: Callable[..., Any],
        *args: Any,
        on_cancel: Callable[[Any], Coroutine[Any, Any, None]] | None = None,
    ) -> Any:
        task = asyncio.create_task(asyncio.to_thread(function, *args))
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            result = await _drain_task(task)
            if result is not None and on_cancel is not None:
                cleanup = asyncio.create_task(on_cancel(result))
                await _drain_task(cleanup)
            raise

    async def _cleanup_driver(self, function: Callable[..., Any], *args: Any) -> None:
        task = asyncio.create_task(asyncio.to_thread(function, *args))
        await _drain_task(task)

    def _submit_query(self, sql: str) -> tuple[Any, str]:
        cursor = self._connection.cursor()
        try:
            cursor.execute_async(sql)
            query_id = cursor.sfqid
            if not isinstance(query_id, str) or not query_id:
                raise ConnectionError("Snowflake did not return a query identifier")
            self._last_query_id = query_id
            return cursor, query_id
        except BaseException:
            with suppress(Exception):
                cursor.close()
            raise

    def _is_query_running(self, query_id: str) -> bool:
        status = self._connection.get_query_status_throw_if_error(query_id)
        return bool(self._connection.is_still_running(status))

    def _cancel_query_sync(self, query_id: str) -> None:
        status = self._connection.get_query_status(query_id)
        if not self._connection.is_still_running(status):
            return

        cursor = self._connection.cursor()
        try:
            cursor.execute(
                "SELECT SYSTEM$CANCEL_QUERY(%(query_id)s)", {"query_id": query_id}
            )
            cursor.fetchone()
        finally:
            cursor.close()

        deadline = time.monotonic() + _CANCEL_CONFIRM_TIMEOUT_SECONDS
        while time.monotonic() < deadline:
            status = self._connection.get_query_status(query_id)
            if not self._connection.is_still_running(status):
                return
            time.sleep(_QUERY_POLL_INTERVAL_SECONDS)
        raise TimeoutError("Snowflake did not confirm query cancellation")

    async def _cancel_or_retire(self, query_id: str) -> None:
        try:
            await self._run_driver(self._cancel_query_sync, query_id)
        except asyncio.CancelledError:
            raise
        except Exception:
            self._broken = True
            with suppress(Exception):
                await self.close()
            raise ConnectionError(
                "Snowflake query cancellation could not be confirmed; reconnect required"
            ) from None

    async def _cancel_abandoned_submission(self, result: Any) -> None:
        cursor, query_id = result
        try:
            await self._cancel_or_retire(query_id)
        except Exception:
            pass
        finally:
            await self._cleanup_driver(cursor.close)

    @staticmethod
    def _fetch_sync(cursor: Any, count: int) -> tuple[list[str], list[list[Any]]]:
        description = cursor.description or ()
        columns: list[str] = []
        for item in description:
            name = getattr(item, "name", None)
            if name is None:
                name = item[0]
            if not isinstance(name, str):
                raise ValueError("Snowflake returned invalid column metadata")
            columns.append(name)
        rows = [list(row) for row in cursor.fetchmany(count)]
        if any(len(row) != len(columns) for row in rows):
            raise ValueError("Snowflake returned rows with inconsistent column counts")
        return columns, rows

    async def _execute_query(
        self, sql: str, max_rows: int, timeout: float
    ) -> ExecutionResult:
        self._ensure_open()
        deadline = time.monotonic() + timeout
        try:
            cursor, query_id = await self._run_driver(
                self._submit_query, sql, on_cancel=self._cancel_abandoned_submission
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            raise ConnectionError(
                f"Snowflake query submission failed ({type(exc).__name__})"
            ) from None

        try:
            if time.monotonic() >= deadline:
                raise TimeoutError
            while True:
                is_running = await self._run_driver(self._is_query_running, query_id)
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError
                if not is_running:
                    break
                await asyncio.sleep(min(_QUERY_POLL_INTERVAL_SECONDS, remaining))
            await self._run_driver(cursor.get_results_from_sfqid, query_id)
            if time.monotonic() >= deadline:
                raise TimeoutError
            columns, rows = await self._run_driver(
                self._fetch_sync, cursor, max_rows + 1
            )
            if time.monotonic() >= deadline:
                raise TimeoutError
            return {
                "columns": columns,
                "rows": rows[:max_rows],
                "row_count": min(len(rows), max_rows),
                "truncated": len(rows) > max_rows,
            }
        except asyncio.CancelledError:
            with suppress(Exception):
                await self._cancel_or_retire(query_id)
            raise
        except TimeoutError:
            try:
                await self._cancel_or_retire(query_id)
            except ConnectionError:
                raise TimeoutError(
                    "Snowflake query timed out; server cancellation was not confirmed"
                ) from None
            raise TimeoutError("Snowflake query deadline exceeded") from None
        except Exception as exc:
            raise ConnectionError(
                f"Snowflake query failed ({type(exc).__name__})"
            ) from None
        finally:
            await self._cleanup_driver(cursor.close)

    def _bounded_query(self, sql: str, row_limit: int) -> str:
        require_read_query(sql, dialect="snowflake")
        if "SYSTEM$CANCEL_QUERY" in sql.upper():
            raise ValueError("Snowflake system cancellation functions are connector-managed")

        statements = [
            item for item in sqlglot.parse(sql, read="snowflake")
            if item is not None and not isinstance(item, exp.Semicolon)
        ]
        if len(statements) != 1 or not isinstance(
            statements[0], (exp.Select, exp.SetOperation)
        ):
            raise ValueError(
                "Snowflake execution currently requires one SELECT or set-operation query"
            )
        query = statements[0]
        if query.args.get("into"):
            raise ValueError("Snowflake SELECT INTO is not allowed")
        if query.find(exp.TableFromRows) is not None:
            raise ValueError("Snowflake table-valued functions are not allowed")

        for table in query.find_all(exp.Table):
            for key, expected, label in (
                ("catalog", self.database, "database"), ("db", self.schema, "schema")
            ):
                identifier = table.args.get(key)
                if identifier is None:
                    continue
                # Quoted names preserve case; "public" is not the configured PUBLIC.
                if not isinstance(identifier, exp.Identifier):
                    raise ValueError(f"Snowflake query has an invalid {label} reference")
                actual = identifier.name
                if not identifier.args.get("quoted"):
                    actual = actual.upper()
                if actual != expected:
                    raise ValueError(f"Snowflake query references a different {label}")

        limit = query.args.get("limit")
        if limit is not None:
            count = limit.expression
            if (
                not isinstance(limit, exp.Limit) or limit.expressions
                or limit.args.get("limit_options") or not isinstance(count, exp.Literal)
                or not count.is_int or int(count.this) < 0
            ):
                raise ValueError("Snowflake requires a literal nonnegative LIMIT")
            row_limit = min(row_limit, int(count.this))
        query.limit(row_limit, copy=False)
        return query.sql(dialect="snowflake", comments=False)

    async def query(self, sql: str, max_rows: int, timeout: float) -> ExecutionResult:
        if timeout <= 0:
            raise ValueError("Snowflake query timeout must be positive")
        row_limit = max(1, max_rows)
        bounded = self._bounded_query(sql, row_limit + 1)
        return await self._execute_query(bounded, row_limit, timeout)

    async def extract_schema(self) -> SchemaSnapshot:
        self._ensure_open()
        schema_literal = exp.Literal.string(self.schema).sql(dialect="snowflake")
        information_schema = f"{self.database}.INFORMATION_SCHEMA"
        sql = (
            "SELECT c.TABLE_NAME, c.COLUMN_NAME, c.DATA_TYPE, c.IS_NULLABLE, "
            "c.COLUMN_DEFAULT, c.COMMENT, t.COMMENT "
            f"FROM {information_schema}.COLUMNS c "
            f"LEFT JOIN {information_schema}.TABLES t "
            "ON t.TABLE_CATALOG = c.TABLE_CATALOG "
            "AND t.TABLE_SCHEMA = c.TABLE_SCHEMA AND t.TABLE_NAME = c.TABLE_NAME "
            f"WHERE c.TABLE_SCHEMA = {schema_literal} "
            "ORDER BY c.TABLE_NAME, c.ORDINAL_POSITION "
            f"LIMIT {_MAX_SCHEMA_COLUMNS + 1}"
        )
        require_read_query(sql, dialect="snowflake")
        result = await self._execute_query(
            sql, _MAX_SCHEMA_COLUMNS, timeout=15.0
        )
        rows = result["rows"]
        if result["truncated"] or result["row_count"] > _MAX_SCHEMA_COLUMNS:
            raise ValueError("Snowflake schema exceeds the column limit")

        tables: dict[str, TableSchema] = {}
        for (
            table_name,
            column_name,
            data_type,
            nullable,
            default,
            column_comment,
            table_comment,
        ) in rows:
            if not all(isinstance(value, str) for value in (table_name, column_name, data_type)):
                raise ValueError("Snowflake returned invalid schema metadata")
            table = tables.get(table_name)
            if table is None:
                table = TableSchema(
                    name=table_name,
                    comment=table_comment or None,
                    schema_name=self.schema,
                    catalog_name=self.database,
                )
                tables[table_name] = table
            table.columns.append(
                ColumnSchema(
                    name=column_name,
                    type=data_type,
                    nullable=nullable == "YES",
                    default=default,
                    comment=column_comment or None,
                )
            )

        return SchemaSnapshot(
            database_type="snowflake",
            database_name=f"{self.database}.{self.schema}",
            tables=tables,
        )

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        task = asyncio.create_task(asyncio.to_thread(self._connection.close))
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            await _drain_task(task)
            raise
