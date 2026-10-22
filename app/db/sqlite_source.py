"""SQLite source revision and native read scope, without rewriting submitted SQL."""

import sqlite3
from collections.abc import Iterator
from contextlib import closing, contextmanager


class SchemaSourceChangedError(ValueError):
    """The request's extracted main schema is no longer current."""


def schema_version(connection: sqlite3.Connection) -> int:
    with closing(connection.execute("PRAGMA main.schema_version")) as cursor:
        row = cursor.fetchone()
    if row is None:
        raise ValueError("Cannot read SQLite main schema version")
    return int(row[0])


def require_schema_version(connection: sqlite3.Connection, expected: int) -> None:
    if schema_version(connection) != expected:
        raise SchemaSourceChangedError("Schema source changed; submit a new query")


@contextmanager
def read_scope(
    connection: sqlite3.Connection, expected_version: int | None = None,
) -> Iterator[None]:
    if expected_version is not None:
        require_schema_version(connection, expected_version)
    main_names: set[str] = set()
    temp_names: set[str] = set()
    if expected_version is not None:
        # SQLite can omit the database for optimized zero-column reads (COUNT,
        # covering indexes, etc.). Resolve only unambiguous main objects then.
        with closing(connection.execute(
            "SELECT name FROM main.sqlite_master WHERE type IN ('table', 'view')"
        )) as cursor:
            main_names = {str(row[0]).casefold() for row in cursor.fetchall()}
        main_names.update({"sqlite_master", "sqlite_schema"})
        with closing(connection.execute(
            "SELECT name FROM temp.sqlite_master WHERE type IN ('table', 'view')"
        )) as cursor:
            temp_names = {str(row[0]).casefold() for row in cursor.fetchall()}
        require_schema_version(connection, expected_version)
    allowed = {
        sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ,
        sqlite3.SQLITE_FUNCTION, sqlite3.SQLITE_RECURSIVE,
    }

    def authorize(
        action: int, arg1: str | None, arg2: str | None,
        database: str | None, trigger: str | None,
    ) -> int:
        if action not in allowed:
            return sqlite3.SQLITE_DENY
        if action == sqlite3.SQLITE_READ and expected_version is not None and database != "main":
            if database is not None or arg1 is None:
                return sqlite3.SQLITE_DENY
            name = arg1.casefold()
            if name not in main_names or name in temp_names:
                return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK

    connection.set_authorizer(authorize)
    try:
        yield
    finally:
        connection.set_authorizer(None)
