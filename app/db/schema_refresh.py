"""One source-checked refresh lifecycle for manual, background and startup indexing."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from typing import Protocol

from app.db.connections import ConnectionFactory
from app.db.runtime import gate
from app.db.schema_extractor import SchemaExtractor
from app.models.rag_schema import SchemaDocument, SchemaIndexClearResult, SchemaIndexWriteError
from app.models.schema import SchemaSnapshot


class SchemaIndexWriter(Protocol):
    async def index_schemas(
        self, docs: list[SchemaDocument], language: str = "auto", *,
        replace_db_id: str | None = None,
        before_replace: Callable[[], Awaitable[None]] | None = None,
    ) -> int: ...


class SchemaRefreshConflictError(ValueError):
    """Prepared documents no longer represent the registered source."""


class SchemaIndexClearer(Protocol):
    async def clear_database(self, db_id: str) -> SchemaIndexClearResult: ...


async def clear_database_schema_index(
    db_id: str, index: SchemaIndexClearer, *,
    on_cleared: Callable[[str], None] | None = None,
) -> SchemaIndexClearResult:
    """Serialize index-only maintenance with full source refresh; no DB connection needed."""
    async with gate(("schema-refresh", db_id)):
        result = await index.clear_database(db_id)
        if on_cleared is not None:
            on_cleared(db_id)
        return result


class SchemaRefreshNotFoundError(KeyError):
    """The requested registered connection was absent when capture began."""


@dataclass(frozen=True)
class SchemaRefreshResult:
    snapshot: SchemaSnapshot
    rag_indexed: bool
    warnings: tuple[str, ...] = ()


def _same_structure(before: SchemaSnapshot, after: SchemaSnapshot) -> bool:
    return (
        before.database_type == after.database_type
        and before.source_schema_version == after.source_schema_version
        and before.duckdb_source_state == after.duckdb_source_state
        and {name: replace(table, row_count_estimate=0) for name, table in before.tables.items()}
        == {name: replace(table, row_count_estimate=0) for name, table in after.tables.items()}
    )


async def refresh_database_schema(
    db_id: str, index: SchemaIndexWriter | None = None,
    *, document_builder: Callable[[SchemaSnapshot, str], list[SchemaDocument]],
) -> SchemaRefreshResult:
    async with gate(("schema-refresh", db_id)):
        try:
            connection = ConnectionFactory.get(db_id)
            info = ConnectionFactory.get_info(db_id)
        except KeyError as exc:
            raise SchemaRefreshNotFoundError(db_id) from exc
        db_type, database_name = info.db_type, info.database_name
        snapshot = await SchemaExtractor.extract(connection, db_type, database_name)
        SchemaExtractor.invalidate(connection)

        async def verify_source() -> None:
            message = "Schema source changed during refresh; retry explicitly"
            if not ConnectionFactory.is_current(db_id, connection):
                raise SchemaRefreshConflictError(message)
            try:
                current = await SchemaExtractor.extract(connection, db_type, database_name)
            except Exception as exc:
                raise SchemaRefreshConflictError(message) from exc
            if not ConnectionFactory.is_current(db_id, connection) or not _same_structure(
                snapshot, current,
            ):
                raise SchemaRefreshConflictError(message)

        if index is None:
            return SchemaRefreshResult(
                snapshot, False, ("Schema extracted but RAG index was not refreshed",),
            )
        try:
            await index.index_schemas(
                document_builder(snapshot, db_id), replace_db_id=db_id,
                before_replace=verify_source,
            )
        except SchemaRefreshConflictError:
            SchemaExtractor.invalidate(connection)
            raise
        except SchemaIndexWriteError as exc:
            return SchemaRefreshResult(snapshot, False, (str(exc),))
        except Exception:
            return SchemaRefreshResult(
                snapshot, False, ("Schema extracted but RAG index was not refreshed",),
            )
        return SchemaRefreshResult(snapshot, True)
