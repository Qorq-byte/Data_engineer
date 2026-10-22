"""DuckDB namespace state; never mutate search_path or rewrite submitted SQL."""

from app.db.execution_types import DuckReadConnection
from app.db.sqlite_source import SchemaSourceChangedError
from app.models.schema import DuckDBSourceState


class DuckDBSourceScopeError(ValueError):
    """A bound request cannot prove its scope in this native environment."""


def source_state(connection: DuckReadConnection) -> DuckDBSourceState:
    rows = connection.execute("""
        SELECT d.database_name, d.database_oid, s.schema_name, s.oid,
               current_setting('search_path')
        FROM duckdb_databases() d
        JOIN duckdb_schemas() s ON s.database_oid = d.database_oid
        WHERE d.database_name = current_database() AND s.schema_name = current_schema()
    """).fetchall()
    if len(rows) != 1:
        raise ValueError("Cannot determine DuckDB source namespace")
    row = rows[0]
    temporary = connection.execute("""
        SELECT schema_name, table_name FROM duckdb_tables() WHERE temporary AND NOT internal
        UNION ALL
        SELECT schema_name, view_name FROM duckdb_views() WHERE temporary AND NOT internal
        ORDER BY 1, 2
    """).fetchall()
    return DuckDBSourceState(
        str(row[0]), int(row[1]), str(row[2]), int(row[3]), str(row[4]),
        tuple((str(item[0]), str(item[1])) for item in temporary),
    )


def require_source_state(connection: DuckReadConnection, expected: DuckDBSourceState) -> None:
    current = source_state(connection)
    if current != expected:
        raise SchemaSourceChangedError("Schema source changed; submit a new query")
    if current.schema_name.casefold() != "main" or current.temporary_objects:
        raise DuckDBSourceScopeError(
            "DuckDB source scope unsupported; use main without user temporary objects"
        )
