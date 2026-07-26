"""Schema management API endpoints.

See SPEC §6.2 (Schema management API) for the full specification.

Phase 1 endpoints:
  - GET  /api/v1/databases         — list connected databases
  - POST /api/v1/databases/test    — test a database connection
  - POST /api/v1/databases/connect — add a new database connection
  - GET  /api/v1/schema/{db_id}    — get schema snapshot

Phase 3 endpoints:
  - POST /api/v1/schema/{db_id}/refresh — refresh schema cache (re-extract)
  - GET  /api/v1/schema/{db_id}/search  — search tables and columns
"""

from __future__ import annotations

import re
from datetime import datetime

from fastapi import APIRouter, HTTPException, Query

from app.db.connections import ConnectionFactory, DatabaseType
from app.db.schema_extractor import SchemaExtractor

router = APIRouter()


@router.get("/databases")
async def list_databases() -> dict:
    """List all connected databases."""
    connections = ConnectionFactory.list_all()
    return {
        "databases": [
            {
                "db_id": c.db_id,
                "db_type": c.db_type.value,
                "alias": c.alias,
                "host": c.host,
                "database_name": c.database_name,
                "status": c.status,
            }
            for c in connections
        ]
    }


@router.post("/databases/test")
async def test_connection(body: dict) -> dict:
    """Test a database connection without storing it.

    Request body:
        {
            "db_type": "sqlite",
            "config": {"path": ":memory:"}
        }
    """
    db_type_str = body.get("db_type", "")
    config = body.get("config", {})

    try:
        db_type = DatabaseType(db_type_str)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported database type: '{db_type_str}'. "
            f"Available: {[d.value for d in DatabaseType]}",
        ) from None

    catalogs: list[str] = []
    try:
        conn = await ConnectionFactory.create(db_type, config, db_id="test_probe")
        # Quick test: try a simple query
        if db_type == DatabaseType.SQLITE or db_type == DatabaseType.DUCKDB:
            conn.execute("SELECT 1")
        elif db_type == DatabaseType.POSTGRESQL:
            await conn.fetch("SELECT 1")
        elif db_type == DatabaseType.MYSQL:
            cursor = await conn.cursor()
            try:
                await cursor.execute("SHOW DATABASES")
                rows = await cursor.fetchall()
                catalogs = [r[0] for r in rows]
            finally:
                await cursor.close()
        ConnectionFactory.close("test_probe")
        result: dict = {
            "status": "ok",
            "message": f"Successfully connected to {db_type_str}",
        }
        if catalogs:
            result["catalogs"] = catalogs
        return result
    except NotImplementedError as e:
        raise HTTPException(status_code=501, detail=str(e)) from e
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Connection failed: {e}") from e


@router.post("/databases/connect")
async def connect_database(body: dict) -> dict:
    """Add a new database connection (persisted for the session).

    Request body:
        {
            "db_type": "sqlite",
            "config": {"path": "/path/to/db.sqlite"},
            "alias": "my_sqlite_db"
        }
    """
    db_type_str = body.get("db_type", "")
    config = body.get("config", {})
    alias = body.get("alias", db_type_str)

    try:
        db_type = DatabaseType(db_type_str)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported database type: '{db_type_str}'",
        ) from None

    db_id = f"{db_type_str}_{alias}_{datetime.now().strftime('%Y%m%d%H%M%S')}"
    # Sanitize db_id for URL safety — keep only ASCII word characters
    db_id = re.sub(r'[^a-zA-Z0-9_-]', '_', db_id)

    try:
        config_with_alias = {**config, "alias": alias}
        await ConnectionFactory.create(db_type, config_with_alias, db_id=db_id)
        info = ConnectionFactory.get_info(db_id)

        # Trigger background schema RAG indexing
        _schedule_background_schema_indexing(db_id)

        return {
            "status": "connected",
            "db_id": db_id,
            "db_type": info.db_type.value,
            "alias": alias,
            "database_name": info.database_name,
        }
    except NotImplementedError as e:
        raise HTTPException(status_code=501, detail=str(e)) from e
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Connection failed: {e}") from e


@router.get("/schema/{db_id}")
async def get_schema(db_id: str) -> dict:
    """Get the full schema snapshot for a connected database."""
    try:
        conn = ConnectionFactory.get(db_id)
        info = ConnectionFactory.get_info(db_id)
    except KeyError:
        raise HTTPException(
            status_code=404,
            detail=f"Database '{db_id}' not found. "
            "Connect first via POST /api/v1/databases/connect",
        ) from None

    try:
        snapshot = await SchemaExtractor.extract(
            conn, info.db_type, info.database_name
        )
    except NotImplementedError as e:
        raise HTTPException(status_code=501, detail=str(e)) from e
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Schema extraction failed: {e}") from e

    return {
        "db_id": db_id,
        "database_type": snapshot.database_type,
        "database_name": snapshot.database_name,
        "table_count": len(snapshot.tables),
        "tables": {
            name: {
                "name": t.name,
                "comment": t.comment,
                "column_count": len(t.columns),
                "row_count_estimate": t.row_count_estimate,
                "columns": [
                    {
                        "name": c.name,
                        "type": c.type,
                        "nullable": c.nullable,
                        "is_primary_key": c.is_primary_key,
                        "is_foreign_key": c.is_foreign_key,
                        "references": list(c.references) if c.references else None,
                        "comment": c.comment,
                        "is_pii": c.is_pii,
                        "pii_type": c.pii_type,
                    }
                    for c in t.columns
                ],
                "foreign_keys": [
                    {
                        "name": fk.name,
                        "column": fk.column,
                        "ref_table": fk.ref_table,
                        "ref_column": fk.ref_column,
                    }
                    for fk in t.foreign_keys
                ],
            }
            for name, t in snapshot.tables.items()
        },
    }


# ── Phase 3 endpoints ─────────────────────────────────────────────────


@router.post("/schema/{db_id}/refresh")
async def refresh_schema(db_id: str) -> dict:
    """Refresh the schema cache for a connected database.

    Re-extracts the full schema snapshot from the live database connection.
    This is useful after DDL changes (new tables, altered columns, etc.).
    """
    try:
        conn = ConnectionFactory.get(db_id)
        info = ConnectionFactory.get_info(db_id)
    except KeyError:
        raise HTTPException(
            status_code=404,
            detail=f"Database '{db_id}' not found. "
            "Connect first via POST /api/v1/databases/connect",
        ) from None

    try:
        snapshot = await SchemaExtractor.extract(
            conn, info.db_type, info.database_name
        )
    except NotImplementedError as e:
        raise HTTPException(status_code=501, detail=str(e)) from e
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Schema extraction failed: {e}") from e

    # Rebuild RAG index for this database
    try:
        from app.rag.converters import snapshot_to_schema_docs
        from app.rag import get_schema_rag

        docs = snapshot_to_schema_docs(snapshot, db_id)
        await get_schema_rag().index_schemas(docs)
    except Exception:
        pass

    return {
        "status": "refreshed",
        "db_id": db_id,
        "database_type": snapshot.database_type,
        "database_name": snapshot.database_name,
        "table_count": len(snapshot.tables),
        "tables": sorted(snapshot.tables.keys()),
        "total_columns": sum(len(t.columns) for t in snapshot.tables.values()),
        "refreshed_at": datetime.now().isoformat(),
    }


@router.get("/schema/{db_id}/search")
async def search_schema(
    db_id: str,
    q: str = Query(..., min_length=1, description="Search keyword (table or column name)"),
    search_tables: bool = Query(default=True, description="Include table names in search"),
    search_columns: bool = Query(default=True, description="Include column names in search"),
    search_comments: bool = Query(default=True, description="Include comments in search"),
    limit: int = Query(default=20, ge=1, le=100, description="Max results"),
) -> dict:
    """Search tables and columns in a database schema by keyword.

    Performs case-insensitive substring matching against table names,
    column names, and optionally comments.
    """
    try:
        conn = ConnectionFactory.get(db_id)
        info = ConnectionFactory.get_info(db_id)
    except KeyError:
        raise HTTPException(
            status_code=404,
            detail=f"Database '{db_id}' not found. "
            "Connect first via POST /api/v1/databases/connect",
        ) from None

    try:
        snapshot = await SchemaExtractor.extract(
            conn, info.db_type, info.database_name
        )
    except NotImplementedError as e:
        raise HTTPException(status_code=501, detail=str(e)) from e
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Schema extraction failed: {e}") from e

    query_lower = q.strip().lower()
    results: list[dict] = []

    for table_name, table in snapshot.tables.items():
        table_match = False

        # Check table name match
        if search_tables and query_lower in table_name.lower():
            table_match = True

        # Check table comment match
        if search_comments and table.comment and query_lower in table.comment.lower():
            table_match = True

        # Check column matches
        matched_columns: list[dict] = []
        for col in table.columns:
            col_match = False
            if search_columns and query_lower in col.name.lower():
                col_match = True
            if search_comments and col.comment and query_lower in col.comment.lower():
                col_match = True

            if col_match:
                matched_columns.append({
                    "name": col.name,
                    "type": col.type,
                    "nullable": col.nullable,
                    "is_primary_key": col.is_primary_key,
                    "is_foreign_key": col.is_foreign_key,
                    "comment": col.comment,
                })

        if table_match or matched_columns:
            results.append({
                "table_name": table_name,
                "comment": table.comment,
                "column_count": len(table.columns),
                "table_match": table_match,
                "matched_columns": matched_columns,
            })

        if len(results) >= limit:
            break

    return {
        "db_id": db_id,
        "query": q,
        "total_matches": len(results),
        "truncated": len(results) >= limit,
        "results": results,
    }


# ── Disconnect ───────────────────────────────────────────────────────────


@router.delete("/databases/{db_id}")
async def disconnect_database(db_id: str) -> dict:
    """Remove and close a database connection."""
    try:
        info = ConnectionFactory.get_info(db_id)
    except KeyError:
        raise HTTPException(
            status_code=404,
            detail=f"Database '{db_id}' not found.",
        ) from None

    ConnectionFactory.close(db_id)

    # Clean up RAG index for this database
    try:
        from app.rag import get_schema_rag
        get_schema_rag().remove_database(db_id)
    except Exception:
        pass

    return {
        "status": "disconnected",
        "db_id": db_id,
        "db_type": info.db_type.value,
        "alias": info.alias,
    }


# ── Database catalogs ──────────────────────────────────────────────────


@router.get("/databases/{db_id}/catalogs")
async def list_catalogs(db_id: str) -> dict:
    """List available databases/catalogs on a connected server.

    For MySQL: runs ``SHOW DATABASES``.
    For PostgreSQL: queries ``pg_database``.
    For SQLite/DuckDB: returns the connected database as a single entry.
    """
    try:
        conn = ConnectionFactory.get(db_id)
        info = ConnectionFactory.get_info(db_id)
    except KeyError:
        raise HTTPException(
            status_code=404,
            detail=f"Database '{db_id}' not found.",
        ) from None

    catalogs: list[str] = []

    try:
        if info.db_type == DatabaseType.MYSQL:
            cursor = await conn.cursor()
            try:
                await cursor.execute("SHOW DATABASES")
                rows = await cursor.fetchall()
                catalogs = [r[0] for r in rows]
            finally:
                await cursor.close()

        elif info.db_type == DatabaseType.POSTGRESQL:
            rows = await conn.fetch(
                "SELECT datname FROM pg_database "
                "WHERE datistemplate = false ORDER BY datname"
            )
            catalogs = [r["datname"] for r in rows]

        elif info.db_type in (DatabaseType.SQLITE, DatabaseType.DUCKDB):
            catalogs = [info.database_name]

        else:
            catalogs = [info.database_name]

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to list catalogs: {e}",
        ) from e

    return {
        "db_id": db_id,
        "db_type": info.db_type.value,
        "current_database": info.database_name,
        "catalogs": catalogs,
        "count": len(catalogs),
    }


# ── PII summary (SPEC §9.2) ────────────────────────────────────────────────


@router.get("/schema/{db_id}/pii-summary")
async def get_pii_summary(db_id: str) -> dict:
    """Get a summary of PII (personally identifiable information) columns.

    Returns the count of detected PII columns grouped by ``pii_type``,
    along with per-table details. Useful for security audits and
    verifying that the schema extraction pipeline is correctly flagging
    sensitive columns.
    """
    try:
        conn = ConnectionFactory.get(db_id)
        info = ConnectionFactory.get_info(db_id)
    except KeyError:
        raise HTTPException(
            status_code=404,
            detail=f"Database '{db_id}' not found.",
        ) from None

    try:
        snapshot = await SchemaExtractor.extract(
            conn, info.db_type, info.database_name
        )
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Schema extraction failed: {e}",
        ) from e

    from app.db.pii_detector import PiiDetector

    summary = PiiDetector().get_pii_summary(snapshot)
    return {
        "db_id": db_id,
        "database_name": snapshot.database_name,
        **summary,
    }


# ── Helpers ────────────────────────────────────────────────────────────────


def _schedule_background_schema_indexing(db_id: str) -> None:
    """Fire-and-forget background task: extract schema and index into RAG.

    Uses asyncio.create_task so the connect endpoint returns immediately
    without waiting for schema extraction + embedding generation.
    """
    try:
        import asyncio

        async def _index_schema_background(db_id: str) -> None:
            import contextlib

            with contextlib.suppress(Exception):
                from app.db.connections import ConnectionFactory
                from app.db.schema_extractor import SchemaExtractor
                from app.rag.converters import snapshot_to_schema_docs
                from app.rag import get_schema_rag

                info = ConnectionFactory.get_info(db_id)
                conn = ConnectionFactory.get(db_id)
                snapshot = await SchemaExtractor.extract(
                    conn, info.db_type, info.database_name
                )
                docs = snapshot_to_schema_docs(snapshot, db_id)
                await get_schema_rag().index_schemas(docs)

        asyncio.create_task(_index_schema_background(db_id))
    except Exception:
        pass
