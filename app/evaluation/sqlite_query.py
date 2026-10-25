"""Bounded read-only SQLite execution shared by legacy and contract evaluations."""

from __future__ import annotations

import math
import sqlite3
import time
from dataclasses import dataclass
from typing import Any

from app.security.sql_policy import require_read_query


class EvaluationQueryError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class QueryResult:
    columns: tuple[str, ...]
    rows: list[tuple[Any, ...]]


def execute_query(
    conn: sqlite3.Connection, sql: str, timeout_seconds: float = 2, max_rows: int = 1000,
) -> QueryResult:
    if (isinstance(timeout_seconds, bool) or not math.isfinite(timeout_seconds)
            or timeout_seconds <= 0 or type(max_rows) is not int or max_rows <= 0):
        raise ValueError("Invalid SQLite evaluation limits")
    try:
        require_read_query(sql, "sqlite")
    except ValueError as exc:
        raise EvaluationQueryError("SQL_POLICY_REJECTED", str(exc)) from exc
    deadline = time.monotonic() + timeout_seconds
    conn.set_progress_handler(lambda: int(time.monotonic() >= deadline), 100)
    cursor = None
    try:
        cursor = conn.execute(sql)
        rows = cursor.fetchmany(max_rows + 1)
        if time.monotonic() >= deadline:
            raise EvaluationQueryError("SQL_TIMEOUT", "SQL evaluation deadline exceeded")
        if len(rows) > max_rows:
            raise EvaluationQueryError("SQL_RESULT_LIMIT", "Result exceeds evaluation row limit")
        return QueryResult(tuple(c[0] for c in cursor.description or ()), rows)
    except sqlite3.Error as exc:
        code = "SQL_TIMEOUT" if time.monotonic() >= deadline else "SQL_EXECUTION_ERROR"
        raise EvaluationQueryError(code, str(exc)) from exc
    finally:
        try:
            if cursor is not None:
                cursor.close()
        finally:
            conn.set_progress_handler(None, 0)


def execute_rows(
    conn: sqlite3.Connection, sql: str, timeout_seconds: float, max_rows: int,
) -> list[tuple[Any, ...]]:
    return execute_query(conn, sql, timeout_seconds, max_rows).rows
