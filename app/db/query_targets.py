"""Capture schema and native handle together; never re-resolve a running query."""

from dataclasses import dataclass
from typing import Any

from app.db.connections import ConnectionFactory
from app.db.schema_extractor import SchemaExtractor
from app.models.schema import SchemaSnapshot


@dataclass(frozen=True)
class ResolvedQueryTarget:
    schema: SchemaSnapshot
    database_name: str
    dialect: str
    # Native driver objects are deliberately kept at this dynamic I/O boundary.
    connection: Any


async def resolve_query_targets(
    database: str, warnings: list[str], *, first_only: bool = False,
) -> dict[str, ResolvedQueryTarget]:
    """Capture registered handle/info before awaiting extraction from that handle.

    A concurrent reconnect may retire the captured handle. The execution node
    rejects it under its connection lease; it must never substitute a new one.
    """
    db_id = database.strip()
    ids = [db_id] if db_id else [info.db_id for info in ConnectionFactory.list_all()]
    if first_only:
        ids = ids[:1]
    if not ids:
        warnings.append("No database connected. Connect first via POST /api/v1/databases/connect.")
    targets: dict[str, ResolvedQueryTarget] = {}
    for target_id in ids:
        try:
            connection = ConnectionFactory.get(target_id)
            info = ConnectionFactory.get_info(target_id)
        except KeyError:
            warnings.append(f"Database '{target_id}' not found or disconnected.")
            continue
        # Copy metadata before the first await, just like the handle itself.
        database_name, db_type = info.database_name, info.db_type
        try:
            schema = await SchemaExtractor.cached_extract(connection, db_type, database_name)
        except NotImplementedError:
            warnings.append(
                f"Schema extraction for '{db_type.value}' is not yet implemented "
                f"(db: {target_id}). Skipping."
            )
            continue
        except Exception:
            warnings.append(f"Schema extraction failed for '{target_id}'.")
            continue
        targets[target_id] = ResolvedQueryTarget(schema, database_name, db_type.value, connection)
    return targets
