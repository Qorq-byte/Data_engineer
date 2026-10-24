"""Query API endpoints — NL input → SQL generation → validation → execution.

See SPEC §6.1 (Core API) for the full API specification.

Phase 2: Full NL→SQL pipeline with all 5 nodes.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re as _re_module
import uuid
from collections.abc import AsyncGenerator
from datetime import datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import AliasChoices, BaseModel, Field

from app.db.connections import ConnectionFactory
from app.db.query_targets import resolve_query_targets
from app.llm.factory import get_default_router
from app.models.query import SQLCandidate
from app.models.schema import SchemaSnapshot
from app.nodes.base import NodeInput
from app.nodes.execute_sql import ExecuteSQLNode
from app.storage.mysql_store import get_store
from app.workflow.contracts import QueryEventName, QueryStatus, classify_query_failure

logger = logging.getLogger(__name__)
router = APIRouter()


# ── Schema resolution helper ────────────────────────────────────────────


def _validate_model(model: str | None) -> None:
    """Reject unknown / disabled models with 422 before running the pipeline."""
    if not model:
        return
    try:
        from app.api.settings import _settings_store

        providers = _settings_store.get("llm", {}).get("providers", {})
        known = {
            m
            for cfg in providers.values()
            if cfg.get("enabled", True)
            for m in (cfg.get("models") or [])
        }
    except Exception:  # noqa: BLE001 — settings unavailable, skip validation
        return
    if known and model not in known:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown or disabled model '{model}'. Available: {sorted(known)}",
        )


async def _resolve_schema(database: str, warnings: list[str]) -> SchemaSnapshot | None:
    """Legacy schema-only projection; empty database selects the first target."""
    targets = await resolve_query_targets(database, warnings, first_only=True)
    return next((target.schema for target in targets.values()), None)


async def _resolve_all_schemas(
    database: str, warnings: list[str]
) -> dict[str, tuple[SchemaSnapshot, str, str]]:
    """Legacy projection keyed by db_id; query execution uses full pinned targets."""
    targets = await resolve_query_targets(database, warnings)
    return {
        db_id: (target.schema, target.database_name, target.dialect)
        for db_id, target in targets.items()
    }


# ── Metadata query detection ──────────────────────────────────────────

_METADATA_PATTERNS: list[tuple[str, _re_module.Pattern[str]]] = [
    # ── Table count queries ──
    (
        "table_count",
        _re_module.compile(
            r"(多少[张个]?(?:表|数据表)|表[的的]*数量|表[的的]*总数|count.*table|how\s+many\s+table)",
            _re_module.IGNORECASE,
        ),
    ),
    # ── Table list queries ──
    (
        "table_list",
        _re_module.compile(
            r"(所有表名?|表[的的]*列表?|列出表|显示表|show\s+table|list\s+table|所有表|全部表)",
            _re_module.IGNORECASE,
        ),
    ),
    # ── Column/list queries about table structure ──
    (
        "table_structure",
        _re_module.compile(
            r"(表结构|表定义|describe\s+table|表[的的]*字段|表[的的]*列)", _re_module.IGNORECASE
        ),
    ),
]


def _detect_metadata_query(
    nl_text: str, schema_map: dict[str, tuple[SchemaSnapshot, str, str]]
) -> list[SQLCandidate] | None:
    """Detect database metadata queries and generate SQL directly.

    Handles queries like:
      - "数据库中有多少张表"  → SELECT COUNT(*) FROM information_schema.tables ...
      - "查询所有表名"        → SELECT table_name FROM information_schema.tables ...
      - "查看表结构"          → (handled by LLM via schema context)

    Returns a list of SQLCandidate if the query is a metadata query,
    or ``None`` if it's a regular data query.
    """
    if not nl_text or not schema_map:
        return None

    query_type: str | None = None
    for qtype, pattern in _METADATA_PATTERNS:
        if pattern.search(nl_text):
            query_type = qtype
            break

    if query_type is None:
        return None

    candidates: list[SQLCandidate] = []

    for _db_id, (_snapshot, db_name, db_type) in schema_map.items():
        if db_type == "mysql":
            if query_type == "table_count":
                sql = (
                    f"-- 查询数据库 '{db_name}' 中的表数量\n"
                    f"SELECT COUNT(*) AS table_count\n"
                    f"FROM information_schema.tables\n"
                    f"WHERE table_schema = '{db_name}'\n"
                    f"  AND table_type = 'BASE TABLE';"
                )
            elif query_type == "table_list":
                sql = (
                    f"-- 查询数据库 '{db_name}' 中的所有表名\n"
                    f"SELECT table_name\n"
                    f"FROM information_schema.tables\n"
                    f"WHERE table_schema = '{db_name}'\n"
                    f"  AND table_type = 'BASE TABLE'\n"
                    f"ORDER BY table_name;"
                )
            elif query_type == "table_structure":
                # For structure queries, let the LLM handle it with the schema context
                return None
            else:
                continue
        elif db_type == "sqlite":
            if query_type == "table_count":
                sql = (
                    f"-- 查询数据库 '{db_name}' 中的表数量\n"
                    f"SELECT COUNT(*) AS table_count\n"
                    f"FROM sqlite_master\n"
                    f"WHERE type = 'table' AND name NOT LIKE 'sqlite_%';"
                )
            elif query_type == "table_list":
                sql = (
                    f"-- 查询数据库 '{db_name}' 中的所有表名\n"
                    f"SELECT name AS table_name\n"
                    f"FROM sqlite_master\n"
                    f"WHERE type = 'table' AND name NOT LIKE 'sqlite_%'\n"
                    f"ORDER BY name;"
                )
            else:
                continue
        elif db_type == "postgresql":
            if query_type == "table_count":
                sql = (
                    f"-- 查询数据库 '{db_name}' 中的表数量\n"
                    f"SELECT COUNT(*) AS table_count\n"
                    f"FROM information_schema.tables\n"
                    f"WHERE table_schema = 'public'\n"
                    f"  AND table_type = 'BASE TABLE';"
                )
            elif query_type == "table_list":
                sql = (
                    f"-- 查询数据库 '{db_name}' 中的所有表名\n"
                    f"SELECT table_name\n"
                    f"FROM information_schema.tables\n"
                    f"WHERE table_schema = 'public'\n"
                    f"  AND table_type = 'BASE TABLE'\n"
                    f"ORDER BY table_name;"
                )
            else:
                continue
        elif db_type == "duckdb":
            if query_type == "table_count":
                sql = (
                    f"-- 查询数据库 '{db_name}' 中的表数量\n"
                    f"SELECT COUNT(*) AS table_count\n"
                    f"FROM information_schema.tables\n"
                    f"WHERE table_schema = 'main';"
                )
            elif query_type == "table_list":
                sql = (
                    f"-- 查询数据库 '{db_name}' 中的所有表名\n"
                    f"SELECT table_name\n"
                    f"FROM information_schema.tables\n"
                    f"WHERE table_schema = 'main'\n"
                    f"ORDER BY table_name;"
                )
            else:
                continue
        else:
            continue

        candidates.append(
            SQLCandidate(
                id=f"cand_{uuid.uuid4().hex[:8]}",
                sql_text=sql,
                confidence=0.95,
                generation_mode="metadata_query",
                dialect=db_type,
                database_name=db_name,
            )
        )

    return candidates if candidates else None


# ── Request / Response models ──────────────────────────────────────────


class QueryRequest(BaseModel):
    """Request body for POST /api/v1/query."""

    nl_text: str = Field(
        ..., description="Natural language query text", min_length=1, max_length=10000
    )
    session_id: str = Field(default="", max_length=128, description="Session identifier")
    domain: str = Field(default="default", description="Domain name (e.g., ecommerce)")
    database: str = Field(default="", description="Database identifier")
    dialect: str = Field(default="ansi", description="SQL dialect")
    num_candidates: int = Field(default=3, ge=1, le=5, description="Number of SQL candidates")
    execute: bool = Field(default=False, description="Execute the generated SQL")
    run_validation: bool = Field(
        default=True,
        description="Run SQL validation",
        validation_alias=AliasChoices("validate", "run_validation"),
    )
    link_schema: bool = Field(default=True, description="Run schema linking")
    model: str | None = Field(
        default=None,
        description="LLM model override (must be an enabled model; defaults to system default)",
    )


class CandidateResult(BaseModel):
    """A single SQL candidate in the response."""

    id: str
    sql_text: str
    confidence: float
    generation_mode: str
    dialect: str | None = None
    database_name: str = ""
    db_id: str = ""
    validation: dict[str, Any] | None = None
    execution: dict[str, Any] | None = None


class QueryResponse(BaseModel):
    """Response body for POST /api/v1/query."""

    query_id: str
    session_id: str = ""
    status: QueryStatus = "completed"
    plan_id: str = "request_query"
    workflow_ids: list[str] = []
    llm_calls: int = 0
    llm_usage: dict[str, Any] = Field(default_factory=dict)
    nl_text: str
    language: str = ""
    intent: str = ""
    confidence: float = 0.0
    candidates: list[CandidateResult] = []
    primary_sql: str | None = None
    validation: dict[str, Any] | None = None
    linked_tables: list[str] = []
    linked_columns: list[dict[str, Any]] = []
    execution: dict[str, Any] | None = None
    errors: list[str] = []
    warnings: list[str] = []
    source: str = "database"
    source_note: str = ""


# ── Endpoint ───────────────────────────────────────────────────────────


@router.post("/query", response_model=QueryResponse)
async def submit_query(body: QueryRequest) -> QueryResponse:
    from app.config.settings import settings
    from app.workflow.query_service import run_query

    _validate_model(body.model)
    try:
        response = await asyncio.wait_for(
            run_query(body, llm_router=get_default_router()), settings.query_timeout_seconds
        )
        return QueryResponse.model_validate(response)
    except Exception as exc:
        failure = classify_query_failure(exc)
        if failure.code == "INTERNAL_ERROR":
            logger.exception("Query request failed")
        raise HTTPException(
            status_code=failure.http_status,
            detail=failure.payload(),
            headers={"Retry-After": str(failure.retry_after_seconds)}
            if failure.retry_after_seconds is not None else None,
        ) from exc


class ExecuteRequest(BaseModel):
    """Request body for POST /api/v1/execute — direct SQL execution."""

    sql: str = Field(..., description="SQL statement to execute", min_length=1, max_length=50000)
    database: str = Field(default="", description="Database identifier")
    dialect: str = Field(default="ansi", description="SQL dialect")
    max_rows: int = Field(default=100, ge=1, le=10000, description="Max rows to return")


@router.post("/execute")
async def execute_sql_direct(body: ExecuteRequest) -> dict:
    """Execute a raw SQL statement directly against a connected database.

    This is the direct execution endpoint for the WorkbenchPanel.
    It does NOT go through the NL→SQL pipeline — it runs the SQL as-is.

    Only SELECT queries are allowed by default (read-only mode).
    """
    warnings: list[str] = []

    # Resolve database connection
    db_id = body.database.strip() if body.database else ""

    if db_id:
        try:
            ConnectionFactory.get(db_id)
        except KeyError:
            available = [c.db_id for c in ConnectionFactory.list_all()]
            raise HTTPException(
                status_code=404,
                detail=f"Database '{db_id}' not found. Available: {available or 'none'}",
            ) from None
    else:
        all_conns = ConnectionFactory.list_all()
        if not all_conns:
            raise HTTPException(
                status_code=400,
                detail="No database connected. Connect first via POST /api/v1/databases/connect",
            )
        db_id = all_conns[0].db_id
        try:
            ConnectionFactory.get(db_id)
        except KeyError:
            raise HTTPException(
                status_code=500,
                detail=f"Auto-selected database '{db_id}' disappeared.",
            ) from None

    # Execute the SQL directly
    exec_node = ExecuteSQLNode()
    try:
        conn = ConnectionFactory.get(db_id)
        exec_output = await exec_node.execute(
            NodeInput(
                query_text=body.sql,
                context={"sql": body.sql, "connection": conn, "db_id": db_id},
                config={"max_rows": body.max_rows},
            )
        )
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Execution error: {e}",
        ) from e

    if exec_output.metadata.get("status") == "success":
        return {
            "status": "success",
            "database": db_id,
            "dialect": body.dialect,
            "columns": exec_output.result.get("columns", []),
            "rows": exec_output.result.get("rows", []),
            "row_count": exec_output.result.get("row_count", 0),
            "truncated": exec_output.result.get("truncated", False),
            "elapsed": exec_output.result.get("elapsed", ""),
            "warnings": exec_output.warnings if hasattr(exec_output, "warnings") else [],
        }
    else:
        return {
            "status": "error",
            "database": db_id,
            "errors": exec_output.errors
            if hasattr(exec_output, "errors")
            else ["Unknown execution error"],
            "warnings": warnings,
        }


# ── Simplified query endpoint (legacy path) ───────────────────────────


@router.post("/query/generate")
async def generate_only(body: QueryRequest) -> QueryResponse:
    """Generate SQL only — no execution, no schema linking.

    Lightweight endpoint for quick NL→SQL generation.
    """
    body.execute = False
    body.run_validation = False
    body.link_schema = False
    return await submit_query(body)


# ── Streaming endpoint (SSE) ───────────────────────────────────────────


@router.post("/query/stream")
async def submit_query_stream(body: QueryRequest) -> StreamingResponse:
    from fastapi.encoders import jsonable_encoder

    from app.workflow.query_service import run_query

    _validate_model(body.model)

    async def event_stream() -> AsyncGenerator[str, None]:
        queue: asyncio.Queue[tuple[str, dict[str, Any]]] = asyncio.Queue(maxsize=64)

        async def emit(event: QueryEventName, data: dict[str, Any]) -> None:
            await queue.put((event, data))

        async def execute() -> None:
            try:
                from app.config.settings import settings

                result = await asyncio.wait_for(
                    run_query(body, emit, llm_router=get_default_router()),
                    settings.query_timeout_seconds,
                )
                response = QueryResponse.model_validate(result)
                await queue.put(("done", response.model_dump(mode="json")))
            except Exception as exc:
                failure = classify_query_failure(exc)
                if failure.code == "INTERNAL_ERROR":
                    logger.exception("Query stream failed")
                await queue.put(("error", dict(failure.payload())))
            finally:
                current = asyncio.current_task()
                if current is not None and not current.cancelling():
                    await queue.put(("_end", {}))

        task = asyncio.create_task(execute())
        try:
            while True:
                event, data = await queue.get()
                if event == "_end":
                    break
                encoded = json.dumps(jsonable_encoder(data), ensure_ascii=False)
                yield f"event: {event}\ndata: {encoded}\n\n"
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# In-memory session store (populated by query submissions)
_SESSION_STORE: dict[str, dict[str, Any]] = {}


def _prune_sessions() -> None:
    from app.config.settings import settings
    from app.db.runtime import discard_gate, gate

    now = datetime.now()
    for key, session in list(_SESSION_STORE.items()):
        updated = session.get("updated_at", session.get("created_at"))
        if (
            updated
            and (now - datetime.fromisoformat(updated)).total_seconds()
            > settings.session_ttl_seconds
            and not gate(("session", key)).locked()
        ):
            _SESSION_STORE.pop(key, None)
            discard_gate(("session", key))
    while len(_SESSION_STORE) >= max(1, settings.session_max_entries):
        key = next((key for key in _SESSION_STORE if not gate(("session", key)).locked()), None)
        if key is None:
            break
        _SESSION_STORE.pop(key)
        discard_gate(("session", key))


def _get_or_create_session(session_id: str) -> dict[str, Any]:
    """Get or create an in-memory session record."""
    if session_id not in _SESSION_STORE:
        _prune_sessions()
        _SESSION_STORE[session_id] = {
            "session_id": session_id,
            "turns": [],
            "created_at": datetime.now().isoformat(),
            "updated_at": datetime.now().isoformat(),
        }
    session = _SESSION_STORE[session_id]
    from app.config.settings import settings

    if (
        datetime.now() - datetime.fromisoformat(session["updated_at"])
    ).total_seconds() > settings.session_ttl_seconds:
        _SESSION_STORE.pop(session_id)
        return _get_or_create_session(session_id)
    return session


@router.get("/sessions/{session_id}")
async def get_session(session_id: str) -> dict:
    """Get conversation session details including all turns."""
    # Try MySQL first
    try:
        store = get_store()
        if store is not None:
            mysql_session = await store.get_session(session_id)
            if mysql_session:
                return {"status": "ok", "session": mysql_session}
    except Exception:
        pass

    # Fallback to in-memory
    session = _SESSION_STORE.get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail=f"Session '{session_id}' not found")
    return {"status": "ok", "session": session}


@router.get("/sessions/{session_id}/versions")
async def get_session_versions(session_id: str) -> dict:
    """Get version history for a conversation session.

    Returns all SQL versions generated and edited during the session.
    """
    # Try MySQL first
    try:
        store = get_store()
        if store is not None:
            versions = await store.get_session_versions(session_id)
            if versions:
                return {
                    "status": "ok",
                    "session_id": session_id,
                    "versions": versions,
                    "total": len(versions),
                }
    except Exception:
        pass

    # Fallback to in-memory
    session = _SESSION_STORE.get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail=f"Session '{session_id}' not found")

    # Build version list: prefer explicit ``versions`` store (advanced
    # version management — SPEC §4.7.2), fall back to turns as versions.
    if "versions" in session and session["versions"]:
        versions = session["versions"]
    else:
        versions = [
            {
                "version_number": i + 1,
                "sql_text": turn.get("sql", ""),
                "source": "generated",
                "created_at": turn.get("timestamp", ""),
            }
            for i, turn in enumerate(session.get("turns", []))
        ]
    return {"status": "ok", "session_id": session_id, "versions": versions, "total": len(versions)}


# ── SPEC §4.7.2 — Advanced version management ──────────────────────


def _ensure_versions_store(session: dict[str, Any]) -> list[dict[str, Any]]:
    """Get or initialise the explicit ``versions`` list on a session.

    If the session only has ``turns`` (legacy), seed the versions list
    from them so rollback/diff/branch have a baseline to work from.
    """
    if "versions" not in session or not session["versions"]:
        session["versions"] = [
            {
                "version_number": i + 1,
                "sql_text": turn.get("sql", ""),
                "source": "generated",
                "parent_version": None,
                "created_at": turn.get("timestamp", "") or datetime.now().isoformat(),
            }
            for i, turn in enumerate(session.get("turns", []))
        ]
        if session["versions"]:
            session["current_version"] = len(session["versions"])
    return session["versions"]


def _find_version(versions: list[dict[str, Any]], version_number: int) -> dict[str, Any]:
    """Find a version by its number. Raises 404 if not found."""
    for v in versions:
        if v.get("version_number") == version_number:
            return v
    raise HTTPException(
        status_code=404,
        detail=f"Version {version_number} not found in session.",
    )


@router.post("/sessions/{session_id}/versions/{version_number}/rollback")
async def rollback_to_version(session_id: str, version_number: int) -> dict:
    """Rollback to a previous SQL version (SPEC §4.7.2).

    Creates a new version whose SQL is copied from version *N*. The
    original version N and all subsequent versions are preserved —
    rollback is non-destructive and appends a new entry to the timeline.

    The new version is marked ``source="rollback"`` with
    ``parent_version=N`` for traceability.
    """
    session = _SESSION_STORE.get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail=f"Session '{session_id}' not found")

    versions = _ensure_versions_store(session)
    target = _find_version(versions, version_number)

    from app.config.settings import settings

    if len(versions) >= settings.session_max_versions:
        raise HTTPException(status_code=409, detail="Session version capacity reached")
    new_number = len(versions) + 1
    new_version = {
        "version_number": new_number,
        "sql_text": target["sql_text"],
        "source": "rollback",
        "parent_version": version_number,
        "created_at": datetime.now().isoformat(),
    }
    versions.append(new_version)
    session["versions"] = versions
    session["current_version"] = new_number
    session["updated_at"] = datetime.now().isoformat()

    return {
        "status": "ok",
        "session_id": session_id,
        "rolled_back_to": version_number,
        "new_version": new_version,
        "total_versions": len(versions),
    }


@router.get("/sessions/{session_id}/versions/diff")
async def diff_versions(
    session_id: str,
    v1: int = Query(..., ge=1, description="First version number"),
    v2: int = Query(..., ge=1, description="Second version number"),
) -> dict:
    """Compare two SQL versions and return a unified diff (SPEC §4.7.2).

    Returns both the structured line-by-line diff and a unified diff
    string suitable for rendering in a terminal or code viewer.
    """
    import difflib

    session = _SESSION_STORE.get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail=f"Session '{session_id}' not found")

    versions = _ensure_versions_store(session)
    v1_data = _find_version(versions, v1)
    v2_data = _find_version(versions, v2)

    sql1 = v1_data.get("sql_text", "")
    sql2 = v2_data.get("sql_text", "")

    # Unified diff (text)
    diff_lines = list(
        difflib.unified_diff(
            sql1.splitlines(keepends=True),
            sql2.splitlines(keepends=True),
            fromfile=f"v{v1}",
            tofile=f"v{v2}",
            n=3,
        )
    )
    unified_diff = "".join(diff_lines)

    # Structured diff (line-by-line)
    matcher = difflib.SequenceMatcher(None, sql1.splitlines(), sql2.splitlines())
    structured: list[dict[str, Any]] = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            for k in range(i1, i2):
                structured.append(
                    {
                        "tag": "equal",
                        "old_line": k + 1,
                        "new_line": k + 1,
                        "content": sql1.splitlines()[k],
                    }
                )
        elif tag == "replace":
            for k in range(i1, i2):
                structured.append(
                    {"tag": "removed", "old_line": k + 1, "content": sql1.splitlines()[k]}
                )
            for k in range(j1, j2):
                structured.append(
                    {"tag": "added", "new_line": k + 1, "content": sql2.splitlines()[k]}
                )
        elif tag == "delete":
            for k in range(i1, i2):
                structured.append(
                    {"tag": "removed", "old_line": k + 1, "content": sql1.splitlines()[k]}
                )
        elif tag == "insert":
            for k in range(j1, j2):
                structured.append(
                    {"tag": "added", "new_line": k + 1, "content": sql2.splitlines()[k]}
                )

    # Compute similarity ratio
    similarity = matcher.ratio()

    return {
        "status": "ok",
        "session_id": session_id,
        "v1": {"version_number": v1, "sql_text": sql1, "source": v1_data.get("source", "")},
        "v2": {"version_number": v2, "sql_text": sql2, "source": v2_data.get("source", "")},
        "similarity": round(similarity, 4),
        "unified_diff": unified_diff,
        "structured_diff": structured,
        "lines_added": sum(1 for d in structured if d["tag"] == "added"),
        "lines_removed": sum(1 for d in structured if d["tag"] == "removed"),
    }


@router.post("/sessions/{session_id}/versions/{version_number}/branch")
async def branch_from_version(
    session_id: str, version_number: int, body: dict | None = None
) -> dict:
    """Create a new branch from version N (SPEC §4.7.2).

    Unlike rollback (which copies the SQL verbatim), a branch creates an
    editable copy of version N that the user can modify. The request
    body may include an optional ``edited_sql`` field to immediately set
    the branched version's SQL; otherwise it starts as a copy.

    The new version is marked ``source="branch"`` with
    ``parent_version=N``.
    """
    session = _SESSION_STORE.get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail=f"Session '{session_id}' not found")

    versions = _ensure_versions_store(session)
    target = _find_version(versions, version_number)

    # Optional: user-supplied edited SQL for the new branch
    edited_sql = ""
    if isinstance(body, dict):
        edited_sql = body.get("edited_sql", "")
        if not isinstance(edited_sql, str) or len(edited_sql) > 50000:
            raise HTTPException(
                status_code=422, detail="edited_sql must be a string <= 50000 characters"
            )

    from app.config.settings import settings

    if len(versions) >= settings.session_max_versions:
        raise HTTPException(status_code=409, detail="Session version capacity reached")
    new_number = len(versions) + 1
    new_version = {
        "version_number": new_number,
        "sql_text": edited_sql if edited_sql else target["sql_text"],
        "source": "branch",
        "parent_version": version_number,
        "created_at": datetime.now().isoformat(),
    }
    versions.append(new_version)
    session["versions"] = versions
    session["current_version"] = new_number
    session["updated_at"] = datetime.now().isoformat()

    return {
        "status": "ok",
        "session_id": session_id,
        "branched_from": version_number,
        "new_version": new_version,
        "total_versions": len(versions),
    }


# ── SPEC path aliases ──────────────────────────────────────────────────


@router.post("/query/explain")
async def explain_sql_alias(body: dict) -> dict:
    """Alias for POST /sql/explain — matches SPEC §6.1 path."""
    from app.api.sql_utils import SQLRequest, explain_sql

    req = SQLRequest(sql=body.get("sql", ""), dialect=body.get("dialect", "ansi"))
    return await explain_sql(req)


@router.post("/query/candidates")
async def candidates_alias(body: QueryRequest) -> QueryResponse:
    """Alias for POST /query/generate — matches SPEC §6.1 path."""
    return await generate_only(body)
