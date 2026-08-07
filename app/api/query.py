"""Query API endpoints — NL input → SQL generation → validation → execution.

See SPEC §6.1 (Core API) for the full API specification.

Phase 2: Full NL→SQL pipeline with all 5 nodes.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import traceback
import uuid
from collections.abc import AsyncGenerator
from datetime import datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.storage.mysql_store import get_store

logger = logging.getLogger(__name__)

from app.db.connections import ConnectionFactory
from app.db.schema_extractor import SchemaExtractor
from app.llm.factory import get_default_router
from app.models.query import SQLCandidate, ValidationReport
from app.models.schema import SchemaSnapshot
from app.nodes.base import NodeInput
from app.nodes.execute_sql import ExecuteSQLNode
from app.nodes.generate_sql import GenerateSQLNode
from app.nodes.parse_nl import ParseNLNode
from app.nodes.schema_linking import SchemaLinkingNode
from app.nodes.validate_sql import ValidateSQLNode

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
            detail=f"Unknown or disabled model '{model}'. "
            f"Available: {sorted(known)}",
        )


async def _resolve_schema(
    database: str, warnings: list[str]
) -> SchemaSnapshot | None:
    """Resolve a SchemaSnapshot from a connected database.

    Strategy:
    1. If *database* is specified and non-empty, look up that exact db_id.
    2. Otherwise, pick the first available connection from the registry.
    3. Extract schema via ``SchemaExtractor.extract()``.

    Returns ``None`` when no connection is available and adds a warning.
    """
    conn = None
    info = None
    db_id = database.strip() if database else ""

    # ── Resolve connection ────────────────────────────────────────
    if db_id:
        try:
            conn = ConnectionFactory.get(db_id)
            info = ConnectionFactory.get_info(db_id)
        except KeyError:
            available = [c.db_id for c in ConnectionFactory.list_all()]
            warnings.append(
                f"Database '{db_id}' not found. "
                f"Available: {available or 'none (connect via POST /api/v1/databases/connect)'}"
            )
            return None
    else:
        # Auto-select first available connection
        all_conns = ConnectionFactory.list_all()
        if not all_conns:
            warnings.append(
                "No database connected. "
                "Connect first via POST /api/v1/databases/connect, "
                "then pass 'database' in the query request."
            )
            return None
        db_id = all_conns[0].db_id
        try:
            conn = ConnectionFactory.get(db_id)
            info = ConnectionFactory.get_info(db_id)
        except KeyError:
            warnings.append(f"Auto-selected database '{db_id}' disappeared.")
            return None

    # ── Extract schema ────────────────────────────────────────────
    try:
        snapshot = await SchemaExtractor.extract(conn, info.db_type, info.database_name)
        return snapshot
    except NotImplementedError:
        warnings.append(
            f"Schema extraction for '{info.db_type.value}' is not yet implemented. "
            "Supported: sqlite, duckdb, postgresql."
        )
        return None
    except Exception as e:
        warnings.append(f"Schema extraction failed for '{db_id}': {e}")
        return None


async def _resolve_all_schemas(
    database: str, warnings: list[str]
) -> dict[str, tuple[SchemaSnapshot, str, str]]:
    """Resolve SchemaSnapshots from ALL connected databases.

    Returns a dict mapping db_id → (schema_snapshot, database_name, db_type).
    When *database* is specified, only that database is resolved.
    When *database* is empty, ALL connected databases are resolved.
    """
    result: dict[str, tuple[SchemaSnapshot, str, str]] = {}

    db_id = database.strip() if database else ""
    if db_id:
        # Single database mode
        snapshot = await _resolve_schema(database, warnings)
        if snapshot is not None:
            try:
                info = ConnectionFactory.get_info(db_id)
                result[db_id] = (snapshot, info.database_name, info.db_type.value)
            except KeyError:
                pass
        return result

    # Multi-database mode: resolve all (deduplicated by database_name)
    all_conns = ConnectionFactory.list_all()
    if not all_conns:
        warnings.append(
            "No database connected. "
            "Connect first via POST /api/v1/databases/connect."
        )
        return result

    seen_db_names: set[str] = set()
    for info in all_conns:
        # Skip duplicate databases (same database_name, different db_id).
        # For databases with EMPTY database_name (e.g., SQLite), use db_id
        # as the dedup key so each file-based database is processed separately.
        dedup_key = info.database_name if info.database_name else info.db_id
        if dedup_key in seen_db_names:
            continue
        seen_db_names.add(dedup_key)
        try:
            conn = ConnectionFactory.get(info.db_id)
            snapshot = await SchemaExtractor.extract(conn, info.db_type, info.database_name)
            result[info.db_id] = (snapshot, info.database_name, info.db_type.value)
        except NotImplementedError:
            warnings.append(
                f"Schema extraction for '{info.db_type.value}' is not yet implemented "
                f"(db: {info.db_id}). Skipping."
            )
        except Exception as e:
            warnings.append(f"Schema extraction failed for '{info.db_id}': {e}")

    return result


# ── Metadata query detection ──────────────────────────────────────────

import re as _re_module

_METADATA_PATTERNS: list[tuple[str, _re_module.Pattern[str]]] = [
    # ── Table count queries ──
    ("table_count", _re_module.compile(r"(多少[张个]?(?:表|数据表)|表[的的]*数量|表[的的]*总数|count.*table|how\s+many\s+table)", _re_module.IGNORECASE)),
    # ── Table list queries ──
    ("table_list", _re_module.compile(r"(所有表名?|表[的的]*列表?|列出表|显示表|show\s+table|list\s+table|所有表|全部表)", _re_module.IGNORECASE)),
    # ── Column/list queries about table structure ──
    ("table_structure", _re_module.compile(r"(表结构|表定义|describe\s+table|表[的的]*字段|表[的的]*列)", _re_module.IGNORECASE)),
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

    for db_id, (snapshot, db_name, db_type) in schema_map.items():
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

    nl_text: str = Field(..., description="Natural language query text", min_length=1)
    session_id: str = Field(default="", description="Session identifier")
    domain: str = Field(default="default", description="Domain name (e.g., ecommerce)")
    database: str = Field(default="", description="Database identifier")
    dialect: str = Field(default="ansi", description="SQL dialect")
    num_candidates: int = Field(default=3, ge=1, le=5, description="Number of SQL candidates")
    execute: bool = Field(default=False, description="Execute the generated SQL")
    run_validation: bool = Field(
        default=True,
        description="Run SQL validation",
        validation_alias="validate",
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
    validation: dict[str, Any] | None = None
    execution: dict[str, Any] | None = None


class QueryResponse(BaseModel):
    """Response body for POST /api/v1/query."""

    query_id: str
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
    """Submit a natural language query, run full NL→SQL pipeline.

    Pipeline: parse_nl → schema_linking → generate_sql → validate_sql → execute_sql.

    Returns structured results including SQL candidates,
    validation report, and (optionally) execution results.
    """
    start_time = time.perf_counter()
    query_id = f"q_{uuid.uuid4().hex[:12]}"
    session_id = body.session_id or f"s_{uuid.uuid4().hex[:8]}"
    errors: list[str] = []
    warnings: list[str] = []
    linked_tables: list[str] = []
    linked_columns: list[dict[str, Any]] = []

    # ── Model override validation ───────────────────────────────────
    _validate_model(body.model)

    # ── Resolve domain config (for glossary + business rules) ──────
    domain_config = None
    if body.domain and body.domain != "default":
        try:
            from app.api.domains import _domain_manager
            domain_config = _domain_manager.get(body.domain)
            if domain_config is None:
                warnings.append(
                    f"Domain '{body.domain}' not found. "
                    f"Available: {list(_domain_manager.list_names())}"
                )
        except Exception as e:
            warnings.append(f"Domain resolution failed: {e}")

    # ── Auto-derive dialect from connected database ────────────────
    # When a database is connected, use its actual type as the SQL
    # dialect instead of the user-supplied or default "ansi".  This
    # ensures the LLM prompt ("You are a postgresql SQL expert") and
    # the schema section agree on the target dialect.
    query_dialect = body.dialect
    db_id_for_dialect = body.database.strip() if body.database else ""
    if db_id_for_dialect:
        try:
            info_for_dialect = ConnectionFactory.get_info(db_id_for_dialect)
            query_dialect = info_for_dialect.db_type.value
        except KeyError:
            pass  # DB not found — keep user-supplied dialect
    else:
        all_conns_for_dialect = ConnectionFactory.list_all()
        if all_conns_for_dialect:
            query_dialect = all_conns_for_dialect[0].db_type.value

    # ── Step 1: Parse NL → SQR ─────────────────────────────────────
    parse_node = ParseNLNode()
    try:
        parse_output = await parse_node.execute(
            NodeInput(
                query_text=body.nl_text,
                context={"domain": domain_config},
            )
        )
        if parse_output.errors:
            errors.extend(parse_output.errors)
            # Continue with best effort
        sqr = parse_output.result
        if sqr is None:
            raise HTTPException(status_code=400, detail="Failed to parse NL input")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"NL parsing error: {e}",
        ) from e

    # ── Step 2: Schema Linking (per database) ─────────────────────
    schema_map: dict[str, tuple[SchemaSnapshot, str, str]] = {}  # db_id → (schema, db_name, db_type)
    all_multi_table_hints: dict[str, dict[str, Any]] = {}  # db_id → multi_table_hint
    all_linked_tables: dict[str, list[str]] = {}  # db_id → linked_tables
    if body.link_schema:
        schema_map = await _resolve_all_schemas(body.database, warnings)

    # ── Step 2b: Detect metadata queries (table count, table list, etc.) ─
    # These queries ask about the database STRUCTURE, not data in tables.
    # The schema linker won't find matching tables, so we handle them here.
    metadata_candidates = _detect_metadata_query(body.nl_text, schema_map)
    if metadata_candidates is not None:
        # Metadata query detected — return results directly
        return QueryResponse(
            query_id=query_id,
            nl_text=body.nl_text,
            language=sqr.language if sqr else "",
            intent=sqr.intent if sqr else "",
            confidence=0.95,
            candidates=[
                CandidateResult(
                    id=c.id,
                    sql_text=c.sql_text,
                    confidence=c.confidence,
                    generation_mode=c.generation_mode,
                    dialect=c.dialect,
                    database_name=c.database_name,
                    validation=None,
                    execution=None,
                )
                for c in metadata_candidates
            ],
            primary_sql=metadata_candidates[0].sql_text if metadata_candidates else None,
            validation=None,
            linked_tables=[],
            linked_columns=[],
            execution=None,
            errors=errors,
            warnings=warnings,
            source="database",
            source_note="",
        )

    if schema_map:
        # Run schema linking for each database in parallel
        async def _link_schema_for_db(
            db_id: str, schema: SchemaSnapshot, db_name: str, db_type: str
        ) -> tuple[str, list[str], list[dict[str, Any]], dict[str, Any]]:
            db_linked_tables: list[str] = []
            db_linked_columns: list[dict[str, Any]] = []
            db_multi_table_hint: dict[str, Any] = {}

            link_node = SchemaLinkingNode()
            try:
                link_output = await link_node.execute(
                    NodeInput(
                        query_text=body.nl_text,
                        context={"sqr": sqr, "schema": schema, "domain": domain_config},
                        config={
                            "db_id": db_id,
                            "language": "auto",
                            "fk_expand": True,
                            "match_threshold": 0.6,
                        },
                    )
                )
                if link_output.metadata["status"] == "success":
                    db_linked_tables = link_output.result.get("tables", [])
                    db_linked_columns = link_output.result.get("columns", [])
                    db_multi_table_hint = link_output.context.get("multi_table_hint", {})
                    logger.debug(
                        "Schema linking for DB %s: %d tables linked, %d columns matched, hint_alternatives=%s",
                        db_id, len(db_linked_tables), len(db_linked_columns),
                        db_multi_table_hint.get("has_alternatives", False),
                    )
            except Exception as e:
                warnings.append(f"Schema linking error for '{db_id}': {e}")
            return (db_id, db_linked_tables, db_linked_columns, db_multi_table_hint)

        tasks = []
        for db_id, (schema, db_name, db_type) in schema_map.items():
            tasks.append(_link_schema_for_db(db_id, schema, db_name, db_type))
        link_results = await asyncio.gather(*tasks, return_exceptions=True)

        for result in link_results:
            if isinstance(result, Exception):
                continue
            db_id, db_tables, db_cols, db_hint = result
            all_linked_tables[db_id] = db_tables
            all_multi_table_hints[db_id] = db_hint
            if db_tables:
                linked_tables.extend(db_tables)
            if db_cols:
                linked_columns.extend(db_cols)

    # ── Step 3: Generate SQL (per database, in parallel) ──────────
    gen_node = GenerateSQLNode(router=get_default_router())
    if getattr(gen_node.router, "mock_mode", False):
        warnings.append(
            "LLM router is in MOCK mode — generated SQL will be placeholder text. "
            "Configure an API key (e.g., DEEPSEEK_API_KEY) in .env for real SQL generation."
        )

    all_candidates: list[SQLCandidate] = []
    skipped_dbs: list[str] = []
    source = "database"
    source_note = ""
    rag_prompt_context: str | None = None
    primary_sql: str | None = None

    if schema_map:
        async def _gen_for_db(
            db_id: str, schema: SchemaSnapshot, db_name: str, db_type: str
        ) -> list[SQLCandidate]:
            db_dialect = db_type
            db_hint = all_multi_table_hints.get(db_id, {})
            db_linked = all_linked_tables.get(db_id, [])

            # Skip SQL generation if no relevant tables or columns found
            if not db_linked:
                skipped_dbs.append(db_name)
                warnings.append(f"数据库 '{db_name}' 中未找到与查询相关的表或列，跳过SQL生成")
                return []

            try:
                gen_output = await gen_node.execute(
                    NodeInput(
                        query_text=body.nl_text,
                        context={"sqr": sqr, "schema": schema, "domain": domain_config},
                        config={
                            "num_candidates": body.num_candidates,
                            "dialect": db_dialect,
                            "rag_context": None,
                            "multi_table_hint": db_hint,
                            "database_name": db_name,
                            "model": body.model,
                        },
                    )
                )
                if gen_output.errors:
                    warnings.append(f"SQL generation error for '{db_name}': {gen_output.errors}")
                return gen_output.result.get("candidates", [])
            except Exception as e:
                warnings.append(f"SQL generation failed for '{db_name}': {e}")
                return []

        gen_tasks = []
        for db_id, (schema, db_name, db_type) in schema_map.items():
            gen_tasks.append(_gen_for_db(db_id, schema, db_name, db_type))
        gen_results = await asyncio.gather(*gen_tasks, return_exceptions=True)

        for result in gen_results:
            if isinstance(result, list):
                all_candidates.extend(result)
            elif isinstance(result, Exception):
                errors.append(f"SQL generation failed: {result}")

        # ── Deduplicate candidates by (sql_text, database_name) ──
        # Temperature sampling may produce identical SQL across multiple
        # rounds. Keep only the highest-confidence candidate per unique
        # (sql_text, database_name) pair so the frontend doesn't show
        # redundant entries.
        if all_candidates:
            seen: dict[tuple[str, str], int] = {}  # (sql_key, db_name) → index
            deduped: list[SQLCandidate] = []
            for cand in all_candidates:
                # Normalize SQL for comparison:
                # - strip whitespace and lowercase
                # - strip trailing semicolons (so "SELECT 1" == "SELECT 1;")
                # - strip SQL comments (so "-- note\nSELECT 1" == "SELECT 1")
                from app.learning.pattern_analyzer import _strip_sql_comments
                sql_clean = _strip_sql_comments(cand.sql_text).strip()
                sql_key = " ".join(sql_clean.split()).lower().rstrip(";")
                db_key = getattr(cand, "database_name", "") or ""
                key = (sql_key, db_key)
                if key in seen:
                    # Keep the one with higher confidence
                    idx = seen[key]
                    if cand.confidence > deduped[idx].confidence:
                        deduped[idx] = cand
                else:
                    seen[key] = len(deduped)
                    deduped.append(cand)
            all_candidates = deduped

        if all_candidates:
            primary_sql = all_candidates[0].sql_text
        else:
            # All databases skipped — clear to trigger fallback below
            if skipped_dbs:
                warnings.append(
                    "已连接的数据库中未找到相关表，正在尝试从领域知识/RAG知识库生成SQL..."
                )
            schema_map = {}  # Clear to trigger fallback

    # ── Fallback: no DB match (no DBs connected OR all skipped) ──
    if not schema_map:
        source = "database"
        source_note = ""

        # If domain is already set from the request, use it as the source
        if domain_config:
            domain_label = domain_config.label.get("zh", domain_config.name)
            source = "domain"
            source_note = f"此SQL基于领域知识「{domain_label}」生成，不涉及已连接的数据库"
            warnings.append(source_note)

        # 1. Try domain detection (if not already set by request)
        if not domain_config:
            try:
                from app.api.domains import _domain_manager
                _domain_manager.reload()
                domain_matches = _domain_manager.detect(body.nl_text)
                if domain_matches and (
                    domain_matches[0].matched_keywords
                    or domain_matches[0].matched_terms
                ):
                    domain_match = domain_matches[0]
                    domain_config = domain_match.domain
                    domain_label = domain_match.domain.label.get("zh", domain_match.domain.name)
                    source = "domain"
                    source_note = f"此SQL基于领域知识「{domain_label}」生成，不涉及已连接的数据库"
                    warnings.append(source_note)
            except Exception:
                pass

        # 2. Try RAG lookup (if domain didn't match)
        if source == "database":
            try:
                from app.rag import get_schema_rag
                schema_rag = get_schema_rag()
                rag_results = await schema_rag.find_relevant_tables(
                    body.nl_text, top_k=10, db_id=None
                )
                # Only use RAG if at least one result has a BM25 (sparse) match —
                # dense-only matches (sparse_rank=None) are unreliable with
                # MockEmbeddingProvider and often return unrelated schema.
                rag_relevant = [r for r in rag_results if r.sparse_rank is not None]
                if rag_relevant:
                    rag_lines = ["RAG-indexed schema knowledge (from previous connections):"]
                    for r in rag_relevant[:15]:
                        rag_lines.append(
                            f"  - {r.table_name}.{r.column_name} ({r.data_type})"
                            + (f" — {r.comment}" if r.comment else "")
                        )
                    rag_prompt_context = "\n".join(rag_lines)
                    source = "rag"
                    source_note = "此SQL基于RAG知识库生成，不涉及已连接的数据库"
                    warnings.append(source_note)
            except RuntimeError:
                pass  # RAG not initialized
            except Exception:
                pass

        # 3. If still no source, LLM auto-generate
        if source == "database":
            source = "llm_auto"
            source_note = "此SQL由LLM自动生成，不涉及已连接数据库、领域知识或RAG知识库"
            warnings.append(source_note)

        # Generate SQL using the appropriate template
        try:
            gen_output = await gen_node.execute(
                NodeInput(
                    query_text=body.nl_text,
                    context={"sqr": sqr, "schema": None, "domain": domain_config},
                    config={
                        "num_candidates": body.num_candidates,
                        "dialect": query_dialect,
                        "rag_context": rag_prompt_context,
                    },
                )
            )
            if gen_output.errors:
                errors.extend(gen_output.errors)
            all_candidates = gen_output.result.get("candidates", [])
            primary_sql = gen_output.result.get("primary_sql") or ""
        except Exception as e:
            raise HTTPException(
                status_code=500,
                detail=f"SQL generation error: {e}",
            ) from e

    # ── Step 4: Validate ALL candidates ─────────────────────────────
    validation_result: dict[str, Any] | None = None
    if body.run_validation:
        for cand in all_candidates:
            if not cand.sql_text:
                continue
            # Determine dialect for this candidate's database
            cand_dialect = cand.dialect or query_dialect
            val_node = ValidateSQLNode(dialect=cand_dialect)
            try:
                # Find the schema for this candidate's database
                cand_schema = None
                cand_db_name = cand.database_name
                for db_id, (s, name, _) in schema_map.items():
                    if name == cand_db_name:
                        cand_schema = s
                        break

                val_output = await val_node.execute(
                    NodeInput(
                        query_text=body.nl_text,
                        context={"primary_sql": cand.sql_text, "schema": cand_schema},
                    )
                )
                report: ValidationReport = val_output.context.get("validation_report")  # type: ignore[assignment]
                if report:
                    cand.validation = {
                        "passed": report.passed,
                        "score": report.score,
                        "syntax_ok": report.syntax_ok,
                        "schema_valid": report.schema_valid,
                        "type_valid": report.type_valid,
                        "syntax_errors": report.syntax_errors,
                        "schema_errors": report.schema_errors,
                        "type_errors": report.type_errors,
                        "warnings": report.warnings,
                    }
                    # Keep top-level validation_result for primary candidate
                    if cand.id == (all_candidates[0].id if all_candidates else ""):
                        validation_result = cand.validation
            except Exception as e:
                warnings.append(f"Validation error for candidate {cand.id}: {e}")

    # ── Step 4b: Auto-retry on validation failure (SelfHealingRetry) ─
    primary_cand = all_candidates[0] if all_candidates else None
    primary_validation = primary_cand.validation if primary_cand else None
    if (
        body.run_validation
        and primary_cand
        and primary_validation
        and not primary_validation["passed"]
        and schema_map  # Only self-heal when schema is available
    ):
        try:
            from app.core.prompt_builder import PromptBuilder
            from app.core.self_heal import SelfHealingRetry
            from app.core.sql_generator import SQLGenerator
            from app.core.sql_validator import SQLValidator

            # Find the schema for the primary candidate's database
            primary_db_name = primary_cand.database_name
            primary_schema = None
            for db_id, (s, name, _) in schema_map.items():
                if name == primary_db_name:
                    primary_schema = s
                    break

            healer = SelfHealingRetry(
                generator=SQLGenerator(
                    router=get_default_router(),
                    prompt_builder=PromptBuilder(),
                ),
                validator=SQLValidator(dialect=primary_cand.dialect or query_dialect),
            )
            healed = await healer.retry_until_valid(
                sqr=sqr,
                failed_sql=primary_cand.sql_text,
                errors=(
                    primary_validation.get("syntax_errors", [])
                    + primary_validation.get("schema_errors", [])
                ),
                schema=primary_schema,
                domain=domain_config,
                dialect=primary_cand.dialect or query_dialect,
            )
            if healed is not None:
                # Set database_name on the healed candidate
                healed.database_name = primary_db_name
                val_node2 = ValidateSQLNode(dialect=primary_cand.dialect or query_dialect)
                val_output2 = await val_node2.execute(
                    NodeInput(
                        query_text=body.nl_text,
                        context={"primary_sql": healed.sql_text, "schema": primary_schema},
                    )
                )
                healed_report: ValidationReport = val_output2.context.get("validation_report")  # type: ignore[assignment]
                if healed_report:
                    healed.validation = {
                        "passed": healed_report.passed,
                        "score": healed_report.score,
                        "syntax_ok": healed_report.syntax_ok,
                        "schema_valid": healed_report.schema_valid,
                        "type_valid": healed_report.type_valid,
                        "syntax_errors": healed_report.syntax_errors,
                        "schema_errors": healed_report.schema_errors,
                        "type_errors": healed_report.type_errors,
                        "warnings": healed_report.warnings,
                    }
                    if healed_report.passed:
                        validation_result = healed.validation

                # Prepend the healed candidate (it becomes the new primary)
                all_candidates.insert(0, healed)
                primary_sql = healed.sql_text
                warnings.append(
                    f"SQL auto-corrected via self-heal retry "
                    f"(rounds: {len(healer.history)})"
                )
            else:
                warnings.append(
                    f"Self-heal retry exhausted after "
                    f"{len(healer.history)} round(s) — "
                    f"returning best-effort SQL"
                )
        except Exception as e:
            warnings.append(f"Self-heal retry skipped: {e}")

    # ── Step 4c: Filter out candidates that failed schema validation ──
    # Only keep candidates that either passed validation or had no
    # validation run.  Candidates with schema_errors (e.g. "Column 'name'
    # not found") would fail at execution time, so there's no point
    # showing them to the user.
    if body.run_validation and all_candidates:
        filtered = [
            cand for cand in all_candidates
            if not cand.validation
            or cand.validation.get("passed", False)
            or not cand.validation.get("schema_errors")
        ]
        if filtered:
            all_candidates = filtered
            if not primary_sql or (
                all_candidates
                and all_candidates[0].sql_text != primary_sql
            ):
                primary_sql = all_candidates[0].sql_text

    # ── Step 5: Execute ONLY database-sourced candidates ───────────
    # Candidates from domain/RAG/llm_auto sources don't target any
    # connected database, so executing them would always fail with
    # "table doesn't exist" errors.  Skip execution for those.
    execution_result: dict[str, Any] | None = None
    if body.execute and source == "database":
        exec_node = ExecuteSQLNode()
        for cand in all_candidates:
            if not cand.sql_text:
                continue
            # Skip candidates without a database_name (non-database sources)
            if not cand.database_name:
                continue
            # Find the correct connection for this candidate's database
            cand_exec_conn = None
            cand_db_name = cand.database_name
            for db_id, (s, name, _) in schema_map.items():
                if name == cand_db_name:
                    try:
                        cand_exec_conn = ConnectionFactory.get(db_id)
                    except KeyError:
                        pass
                    break

            if cand_exec_conn is None:
                continue

            try:
                exec_output = await exec_node.execute(
                    NodeInput(
                        query_text=body.nl_text,
                        context={"sql": cand.sql_text, "connection": cand_exec_conn},
                    )
                )
                if exec_output.metadata.get("status") == "success":
                    cand.execution = {
                        "columns": exec_output.result.get("columns", []),
                        "rows": exec_output.result.get("rows", []),
                        "row_count": exec_output.result.get("row_count", 0),
                        "truncated": exec_output.result.get("truncated", False),
                    }
                    # Keep top-level execution_result for primary candidate
                    if cand.id == (all_candidates[0].id if all_candidates else ""):
                        execution_result = cand.execution
                else:
                    cand.execution = {
                        "error": str(exec_output.errors) if exec_output.errors else "Unknown error",
                    }
            except Exception as e:
                cand.execution = {"error": str(e)}

    # ── Build response ──────────────────────────────────────────────
    response_time_ms = int((time.perf_counter() - start_time) * 1000)

    # Record turn in in-memory session (for version management API — SPEC §4.7.2).
    # _get_or_create_session is defined later in this module; the lookup
    # is wrapped in try/except so a failure here never breaks the query.
    try:
        session = _get_or_create_session(session_id)
        session["turns"].append({
            "turn_id": query_id,
            "nl_input": body.nl_text,
            "sql": primary_sql or "",
            "source": source,
            "timestamp": datetime.now().isoformat(),
        })
        session["updated_at"] = datetime.now().isoformat()
    except Exception:
        pass

    # Save query to MySQL for persistence
    detected_domain = body.domain
    store = get_store()
    if store is not None:
        try:
            await store.save_query({
                "session_id": session_id,
                "query_id": query_id,
                "nl_text": body.nl_text,
                "sql_generated": primary_sql or "",
                "sql_final": execution_result.get("sql", "") if execution_result else "",
                "domain": detected_domain,
                "db_id": body.database,
                "response_time_ms": response_time_ms,
                "rating": 0,
            })
            if primary_sql:
                await store.save_query_pair({
                    "session_id": session_id,
                    "nl_text": body.nl_text,
                    "sql_text": primary_sql,
                    "domain": detected_domain,
                    "rating": 0,
                })
        except Exception as e:
            logger.warning("Failed to save query to MySQL: %s", e)

    # Record workflow trace for real-time stats
    try:
        from app.api.workflows import record_trace
        nodes_executed = ["parse_nl"]
        if body.link_schema:
            nodes_executed.append("schema_linking")
        nodes_executed.append("generate_sql")
        if body.run_validation and primary_sql:
            nodes_executed.append("validate_sql")
        if body.execute and primary_sql:
            nodes_executed.append("execute_sql")
        exec_success = bool(primary_sql) and not errors
        record_trace(
            plan="gensql_agentic",
            status="success" if exec_success else "failed",
            duration_ms=response_time_ms,
            nl_input=body.nl_text,
            sql_generated=primary_sql or "",
            row_count=execution_result.get("row_count", 0) if execution_result else 0,
            nodes_executed=nodes_executed,
            error="; ".join(errors) if errors else "",
        )
    except Exception as e:
        logger.warning("Failed to record workflow trace: %s", e)

    # Record query for learning stats
    try:
        from app.api.learning import record_query
        record_query(
            nl_text=body.nl_text,
            sql_text=primary_sql or "",
            domain=detected_domain,
        )
    except Exception as e:
        logger.warning("Failed to record learning query: %s", e)

    return QueryResponse(
        query_id=query_id,
        nl_text=body.nl_text,
        language=sqr.language if sqr else "",
        intent=sqr.intent.value if sqr and sqr.intent else "UNKNOWN",
        confidence=sqr.confidence if sqr else 0.0,
        candidates=[
            CandidateResult(
                id=c.id,
                sql_text=c.sql_text,
                confidence=c.confidence,
                generation_mode=c.generation_mode,
                dialect=getattr(c, "dialect", None),
                database_name=getattr(c, "database_name", ""),
                validation=getattr(c, "validation", None),
                execution=getattr(c, "execution", None),
            )
            for c in all_candidates
        ],
        primary_sql=primary_sql,
        validation=validation_result,
        linked_tables=linked_tables,
        linked_columns=linked_columns,
        execution=execution_result,
        errors=errors,
        warnings=warnings,
        source=source,
        source_note=source_note,
    )


# ── Direct SQL Execution endpoint ──────────────────────────────────────


class ExecuteRequest(BaseModel):
    """Request body for POST /api/v1/execute — direct SQL execution."""

    sql: str = Field(..., description="SQL statement to execute", min_length=1)
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
                detail=f"Database '{db_id}' not found. "
                f"Available: {available or 'none'}",
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
                context={"sql": body.sql, "connection": conn, "max_rows": body.max_rows},
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
            "errors": exec_output.errors if hasattr(exec_output, "errors") else ["Unknown execution error"],
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
    """Submit a natural language query and stream the SQL generation token by token.

    Uses Server-Sent Events (SSE) to push real-time updates (SPEC §6.7):
      - ``event: workflow`` → workflow selection result (first event)
      - ``event: stage`` → workflow stage change
      - ``event: parse`` → NL parsed into SQR
      - ``event: schema`` → schema linking complete
      - ``event: token`` → SQL token (repeated per token)
      - ``event: validation`` → validation result
      - ``event: agent_trace`` → agent/node decision trace (SPEC §6.7)
      - ``event: agent_handoff`` → handoff between stages (SPEC §6.7)
      - ``event: rag_result`` → RAG retrieval summary (SPEC §6.7)
      - ``event: ambiguity`` → ambiguity detected (SPEC §6.7)
      - ``event: mcp_call`` → MCP tool invocation (SPEC §6.7)
      - ``event: collaboration`` → collaboration mode (SPEC §6.7)
      - ``event: done`` → all done
      - ``event: error`` → error occurred
    """

    async def event_stream() -> AsyncGenerator[str, None]:
        start_time = time.perf_counter()
        query_id = f"q_{uuid.uuid4().hex[:12]}"

        def _emit(event: str, data: dict[str, Any]) -> str:
            return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"

        # ── Model override validation ───────────────────────────────
        try:
            _validate_model(body.model)
        except HTTPException as e:
            yield _emit("error", {"message": e.detail})
            return

        # ── SPEC §6.7: emit ``workflow`` event as the very first event ──
        # Declare the plan that will be used for this query so the
        # frontend can render the expected pipeline immediately.
        workflow_stages = ["parse", "retrieve", "generate", "validate", "execute"]
        yield _emit("workflow", {
            "workflow_id": "gensql_agentic",
            "name": "标准查询",
            "estimated_cost": "medium",
            "stages": workflow_stages,
        })

        # ── Resolve domain config (for glossary + business rules) ──
        stream_domain_config = None
        if body.domain and body.domain != "default":
            try:
                from app.api.domains import _domain_manager
                stream_domain_config = _domain_manager.get(body.domain)
            except Exception:
                pass

        # ── Auto-derive dialect from connected database ────────
        stream_dialect = body.dialect
        stream_db_id = body.database.strip() if body.database else ""
        if stream_db_id:
            try:
                stream_info = ConnectionFactory.get_info(stream_db_id)
                stream_dialect = stream_info.db_type.value
            except KeyError:
                pass
        else:
            stream_all = ConnectionFactory.list_all()
            if stream_all:
                stream_dialect = stream_all[0].db_type.value

        try:
            # ── SPEC §6.7: stage event — parse ──
            yield _emit("stage", {"stage": "parse", "status": "started", "progress": 0.1})
            stage_start = time.perf_counter()

            # ── Step 1: Parse NL ──────────────────────────────
            parse_node = ParseNLNode()
            parse_output = await parse_node.execute(
                NodeInput(
                    query_text=body.nl_text,
                    context={"domain": stream_domain_config},
                )
            )
            if parse_output.errors or parse_output.result is None:
                yield _emit("error", {"message": "NL parsing failed",
                           "errors": parse_output.errors})
                return

            sqr = parse_output.result
            yield _emit("parse", {
                "query_id": query_id,
                "language": sqr.language,
                "intent": sqr.intent.value,
                "confidence": sqr.confidence,
                "entities": len(sqr.entities),
            })

            # SPEC §6.7: agent_trace for parse_nl
            yield _emit("agent_trace", {
                "agent": "nl_understander",
                "status": "completed",
                "latency_ms": int((time.perf_counter() - stage_start) * 1000),
                "output": {
                    "language": sqr.language,
                    "intent": sqr.intent.value,
                    "confidence": sqr.confidence,
                    "entities_count": len(sqr.entities),
                },
            })

            # SPEC §6.7: ambiguity detection — emit if SQR has low confidence
            # or contains time/entity ambiguity markers.
            if sqr.confidence < 0.5:
                yield _emit("ambiguity", {
                    "aspect": "intent",
                    "question": body.nl_text,
                    "options": [],
                    "confidence": sqr.confidence,
                })

            # SPEC §6.7: agent_handoff — parse → retrieve
            yield _emit("agent_handoff", {
                "from": "nl_understander",
                "to": "schema_retriever",
                "reason": "sqr_ready",
                "context_summary": f"intent={sqr.intent.value}, entities={len(sqr.entities)}",
            })

            # ── SPEC §6.7: stage event — retrieve ──
            yield _emit("stage", {"stage": "retrieve", "status": "started", "progress": 0.3})
            stage_start = time.perf_counter()

            # ── Initialize generation state (used by metadata + generation) ──
            stream_all_candidates: list[SQLCandidate] = []
            primary_sql = ""
            stream_rag_context: str | None = None
            stream_source = "database"
            stream_source_note = ""

            # ── Step 2: Schema linking (per database) ────────
            stream_schema_map: dict[str, tuple[SchemaSnapshot, str, str]] = {}
            stream_all_multi_hints: dict[str, dict[str, Any]] = {}
            stream_db_linked_tables: dict[str, list[str]] = {}  # db_id → matched tables
            stream_is_metadata = False  # set True when a metadata query is detected
            if body.link_schema:
                stream_warnings: list[str] = []
                stream_schema_map = await _resolve_all_schemas(body.database, stream_warnings)
                for w in stream_warnings:
                    yield _emit("warning", {"message": w})

                # ── Step 2b: Detect metadata queries (table count, table list) ──
                # These queries ask about the database STRUCTURE, not data in
                # tables.  The schema linker won't find matching tables, so we
                # generate precise information_schema / sqlite_master SQL here.
                if stream_schema_map:
                    metadata_candidates = _detect_metadata_query(
                        body.nl_text, stream_schema_map
                    )
                    if metadata_candidates is not None:
                        stream_is_metadata = True
                        stream_source = "database"
                        stream_source_note = ""
                        # Emit schema events for each database
                        for mc in metadata_candidates:
                            yield _emit("schema", {
                                "database": mc.database_name,
                                "db_id": "",
                                "tables": [],
                                "columns_count": 0,
                                "hint_alternatives": False,
                            })
                        # Emit SQL tokens for each candidate
                        for mc in metadata_candidates:
                            yield _emit("database", {
                                "name": mc.database_name, "db_id": "",
                            })
                            for word in mc.sql_text.split():
                                yield _emit("token", {"text": word + " "})
                            if not primary_sql:
                                primary_sql = mc.sql_text
                        stream_all_candidates = list(metadata_candidates)
                        # SPEC §6.7: agent_trace for metadata query handler
                        yield _emit("agent_trace", {
                            "agent": "schema_retriever",
                            "status": "completed",
                            "tables_found": [],
                            "metadata_query": True,
                        })

                if stream_schema_map and not stream_is_metadata:
                    # Run schema linking for each database sequentially
                    for db_id, (db_schema, db_name, db_type) in stream_schema_map.items():
                        try:
                            link_node = SchemaLinkingNode()
                            link_output = await link_node.execute(
                                NodeInput(
                                    query_text=body.nl_text,
                                    context={"sqr": sqr, "schema": db_schema, "domain": stream_domain_config},
                                )
                            )
                            db_linked_tables = link_output.result.get("tables", [])
                            db_multi_hint = link_output.context.get("multi_table_hint", {})
                            stream_all_multi_hints[db_id] = db_multi_hint
                            stream_db_linked_tables[db_id] = db_linked_tables

                            # Always keep the full schema — the multi_table_hint
                            # handles prioritization in the prompt builder.
                            yield _emit("schema", {
                                "database": db_name,
                                "db_id": db_id,
                                "tables": db_linked_tables,
                                "columns_count": len(link_output.result.get("columns", [])),
                                "hint_alternatives": db_multi_hint.get("has_alternatives", False),
                            })

                            # SPEC §6.7: agent_trace for schema_retriever
                            yield _emit("agent_trace", {
                                "agent": "schema_retriever",
                                "status": "completed",
                                "tools_called": ["hybrid_search"],
                                "tables_found": db_linked_tables,
                                "database": db_name,
                            })
                        except Exception as e:
                            yield _emit("warning", {"message": f"Schema linking error for '{db_name}': {e}"})

            yield _emit("stage", {"stage": "retrieve", "status": "completed", "progress": 0.4,
                                   "duration_ms": int((time.perf_counter() - stage_start) * 1000)})

            # ── SPEC §6.7: stage event — generate ──
            yield _emit("stage", {"stage": "generate", "status": "started", "progress": 0.5})
            stage_start = time.perf_counter()
            # SPEC §6.7: collaboration mode announcement
            yield _emit("collaboration", {
                "mode": "pipeline",
                "agents": ["nl_understander", "schema_retriever", "sql_generator"],
                "status": "started",
            })

            # ── Step 3: Stream SQL generation (per database) ─
            gen_node = GenerateSQLNode(router=get_default_router())

            if stream_schema_map and not stream_is_metadata:
                stream_skipped_dbs: list[str] = []
                for db_id, (db_schema, db_name, db_type) in stream_schema_map.items():
                    db_dialect = db_type
                    db_hint = stream_all_multi_hints.get(db_id, {})
                    db_linked = stream_db_linked_tables.get(db_id, [])
                    db_independent = db_hint.get("independent_tables", [])

                    # Skip SQL generation if no relevant tables or columns found
                    if not db_linked:
                        stream_skipped_dbs.append(db_name)
                        yield _emit("warning", {
                            "message": f"数据库 '{db_name}' 中未找到与查询相关的表或列，跳过SQL生成"
                        })
                        continue

                    yield _emit("schema", {
                        "database": db_name,
                        "db_id": db_id,
                        "generating": True,
                    })

                    try:
                        gen_input = await gen_node.setup_input({
                            "query_text": body.nl_text,
                            "context": {"sqr": sqr, "schema": db_schema, "domain": stream_domain_config},
                            "config": {
                                "num_candidates": body.num_candidates,
                                "dialect": db_dialect,
                                "streaming": True,
                                "rag_context": None,
                                "multi_table_hint": db_hint,
                                "database_name": db_name,
                                "model": body.model,
                            },
                        })
                        gen_output = await gen_node.execute(gen_input)
                        db_candidates: list[SQLCandidate] = gen_output.result.get("candidates", [])
                        db_primary = gen_output.result.get("primary_sql", "")

                        # Emit tokens for this database's SQL
                        if db_primary:
                            yield _emit("database", {"name": db_name, "db_id": db_id})
                            words = db_primary.split()
                            for word in words:
                                yield _emit("token", {"text": word + " "})
                            if not primary_sql:
                                primary_sql = db_primary

                        if gen_output.errors:
                            yield _emit("warning", {"message": f"Generation error for '{db_name}': {gen_output.errors}"})

                        stream_all_candidates.extend(db_candidates)

                        # SPEC §6.7: agent_handoff — schema_retriever → sql_generator
                        yield _emit("agent_handoff", {
                            "from": "schema_retriever",
                            "to": "sql_generator",
                            "reason": "schema_ready",
                            "context_summary": f"db={db_name}, tables={len(db_linked)}",
                        })
                        # SPEC §6.7: agent_trace for sql_generator
                        yield _emit("agent_trace", {
                            "agent": "sql_generator",
                            "status": "completed",
                            "candidates_count": len(db_candidates),
                            "primary_sql_length": len(db_primary),
                            "database": db_name,
                        })
                    except Exception as e:
                        yield _emit("warning", {"message": f"Generation error for '{db_name}': {e}"})

                # ── Deduplicate stream candidates by (sql_text, database_name) ──
                # Same logic as non-streaming path: temperature sampling may
                # produce identical SQL across multiple rounds.  Normalize SQL
                # by stripping comments and trailing semicolons so that
                # "SELECT 1" and "SELECT 1;" are treated as duplicates.
                if stream_all_candidates:
                    from app.learning.pattern_analyzer import _strip_sql_comments as _stream_strip
                    _seen: dict[tuple[str, str], int] = {}
                    _deduped: list[SQLCandidate] = []
                    for cand in stream_all_candidates:
                        _sql_clean = _stream_strip(cand.sql_text).strip()
                        sql_key = " ".join(_sql_clean.split()).lower().rstrip(";")
                        db_key = getattr(cand, "database_name", "") or ""
                        key = (sql_key, db_key)
                        if key in _seen:
                            idx = _seen[key]
                            if cand.confidence > _deduped[idx].confidence:
                                _deduped[idx] = cand
                        else:
                            _seen[key] = len(_deduped)
                            _deduped.append(cand)
                    stream_all_candidates = _deduped

                # If all databases were skipped, fall through to domain/RAG/LLM-auto
                if not stream_all_candidates and stream_skipped_dbs:
                    yield _emit("warning", {
                        "message": f"已连接的数据库中未找到相关表，正在尝试从领域知识/RAG知识库生成SQL..."
                    })
                    stream_schema_map = {}  # Clear to trigger fallback below

            yield _emit("stage", {"stage": "generate", "status": "completed", "progress": 0.7,
                                   "duration_ms": int((time.perf_counter() - stage_start) * 1000)})

            # ── Fallback: no DB match (no DBs connected OR all skipped) ──
            if not stream_schema_map:
                stream_source = "database"
                stream_source_note = ""

                # If domain is already set from the request, use it as the source
                if stream_domain_config:
                    domain_label = stream_domain_config.label.get("zh", stream_domain_config.name)
                    stream_source = "domain"
                    stream_source_note = f"此SQL基于领域知识「{domain_label}」生成，不涉及已连接的数据库"
                    yield _emit("warning", {"message": stream_source_note})

                # 1. Try domain detection (if not already set)
                if not stream_domain_config:
                    try:
                        from app.api.domains import _domain_manager
                        _domain_manager.reload()
                        domain_matches = _domain_manager.detect(body.nl_text)
                        if domain_matches and (
                            domain_matches[0].matched_keywords
                            or domain_matches[0].matched_terms
                        ):
                            domain_match = domain_matches[0]
                            stream_domain_config = domain_match.domain
                            domain_label = domain_match.domain.label.get("zh", domain_match.domain.name)
                            stream_source = "domain"
                            stream_source_note = f"此SQL基于领域知识「{domain_label}」生成，不涉及已连接的数据库"
                            yield _emit("warning", {"message": stream_source_note})
                    except Exception:
                        pass

                # 2. Try RAG lookup (if domain didn't match)
                if stream_source == "database":
                    try:
                        from app.rag import get_schema_rag
                        schema_rag = get_schema_rag()
                        rag_results = await schema_rag.find_relevant_tables(
                            body.nl_text, top_k=10, db_id=None
                        )
                        # Only use RAG if at least one result has a BM25 (sparse) match —
                        # dense-only matches (sparse_rank=None) are unreliable with
                        # MockEmbeddingProvider and often return unrelated schema.
                        rag_relevant = [r for r in rag_results if r.sparse_rank is not None]
                        # SPEC §6.7: rag_result event — surface the retrieval
                        # summary to the frontend for transparency.
                        yield _emit("rag_result", {
                            "matched_tables": sorted({r.table_name for r in rag_relevant}),
                            "matched_terms": [],
                            "similar_queries_found": len(rag_relevant),
                            "total_candidates": len(rag_results),
                        })
                        if rag_relevant:
                            rag_lines = ["RAG-indexed schema knowledge (from previous connections):"]
                            for r in rag_relevant[:15]:
                                rag_lines.append(
                                    f"  - {r.table_name}.{r.column_name} ({r.data_type})"
                                    + (f" — {r.comment}" if r.comment else "")
                                )
                            stream_rag_context = "\n".join(rag_lines)
                            stream_source = "rag"
                            stream_source_note = "此SQL基于RAG知识库生成，不涉及已连接的数据库"
                            yield _emit("warning", {"message": stream_source_note})
                    except Exception:
                        pass

                # 3. If still no source, LLM auto-generate
                if stream_source == "database":
                    stream_source = "llm_auto"
                    stream_source_note = "此SQL由LLM自动生成，不涉及已连接数据库、领域知识或RAG知识库"
                    yield _emit("warning", {"message": stream_source_note})

                # Generate SQL using the appropriate template
                try:
                    gen_input = await gen_node.setup_input({
                        "query_text": body.nl_text,
                        "context": {"sqr": sqr, "schema": None, "domain": stream_domain_config},
                        "config": {
                            "num_candidates": body.num_candidates,
                            "dialect": stream_dialect,
                            "streaming": True,
                            "rag_context": stream_rag_context,
                        },
                    })
                    gen_output = await gen_node.execute(gen_input)
                    primary_sql = gen_output.result.get("primary_sql") or ""
                    words = primary_sql.split()
                    for word in words:
                        yield _emit("token", {"text": word + " "})
                    stream_all_candidates = gen_output.result.get("candidates", [])
                    if gen_output.errors:
                        yield _emit("error", {"message": "Generation error", "errors": gen_output.errors})
                        return
                except Exception as e:
                    yield _emit("error", {"message": f"Generation error: {e}"})
                    return

            # ── Step 4: Validate ALL candidates ──────────────────
            if body.run_validation:
                # SPEC §6.7: stage event — validate
                yield _emit("stage", {"stage": "validate", "status": "started", "progress": 0.85})
                stage_start = time.perf_counter()
                for cand in stream_all_candidates:
                    if not cand.sql_text:
                        continue
                    # Skip validation for metadata queries — they reference
                    # system tables (information_schema, sqlite_master) that
                    # aren't in the schema snapshot, so validation would
                    # incorrectly flag them as schema errors.
                    if getattr(cand, "generation_mode", "") == "metadata_query":
                        cand.validation = {
                            "passed": True,
                            "score": 1.0,
                            "syntax_ok": True,
                            "schema_valid": True,
                            "type_valid": True,
                            "syntax_errors": [],
                            "schema_errors": [],
                            "type_errors": [],
                            "warnings": [],
                        }
                        yield _emit("validation", {
                            "candidate_id": cand.id,
                            "passed": True,
                            "score": 1.0,
                            "syntax_ok": True,
                            "schema_valid": True,
                            "type_valid": True,
                        })
                        continue
                    cand_dialect = cand.dialect or stream_dialect
                    val_node = ValidateSQLNode(dialect=cand_dialect)
                    try:
                        # Find the schema for this candidate's database
                        cand_schema = None
                        cand_db_name = cand.database_name
                        for db_id, (s, name, _) in stream_schema_map.items():
                            if name == cand_db_name:
                                cand_schema = s
                                break

                        val_output = await val_node.execute(
                            NodeInput(
                                query_text=body.nl_text,
                                context={"primary_sql": cand.sql_text, "schema": cand_schema},
                            )
                        )
                        report = val_output.context.get("validation_report")
                        if report:
                            cand.validation = {
                                "passed": report.passed,
                                "score": report.score,
                                "syntax_ok": report.syntax_ok,
                                "schema_valid": report.schema_valid,
                                "type_valid": report.type_valid,
                                "syntax_errors": report.syntax_errors,
                                "schema_errors": report.schema_errors,
                                "type_errors": report.type_errors,
                                "warnings": report.warnings,
                            }
                            yield _emit("validation", {
                                "candidate_id": cand.id,
                                "passed": report.passed,
                                "score": report.score,
                                "syntax_ok": report.syntax_ok,
                                "schema_valid": report.schema_valid,
                                "type_valid": report.type_valid,
                            })
                    except Exception as e:
                        yield _emit("warning", {"message": f"Validation error: {e}"})

            # ── Step 4b: Auto-retry on validation failure ─────────
            primary_stream = stream_all_candidates[0] if stream_all_candidates else None
            primary_stream_val = primary_stream.validation if primary_stream else None
            if (
                body.run_validation
                and primary_stream
                and primary_stream_val
                and not primary_stream_val["passed"]
                and stream_schema_map  # Only self-heal when schema is available
            ):
                try:
                    from app.core.prompt_builder import PromptBuilder
                    from app.core.self_heal import SelfHealingRetry
                    from app.core.sql_generator import SQLGenerator
                    from app.core.sql_validator import SQLValidator

                    primary_db_name = primary_stream.database_name
                    primary_stream_schema = None
                    for db_id, (s, name, _) in stream_schema_map.items():
                        if name == primary_db_name:
                            primary_stream_schema = s
                            break

                    healer = SelfHealingRetry(
                        generator=SQLGenerator(
                            router=get_default_router(),
                            prompt_builder=PromptBuilder(),
                        ),
                        validator=SQLValidator(dialect=primary_stream.dialect or stream_dialect),
                    )
                    healed = await healer.retry_until_valid(
                        sqr=sqr,
                        failed_sql=primary_stream.sql_text,
                        errors=(
                            primary_stream_val.get("syntax_errors", [])
                            + primary_stream_val.get("schema_errors", [])
                        ),
                        schema=primary_stream_schema,
                        domain=stream_domain_config,
                        dialect=primary_stream.dialect or stream_dialect,
                    )
                    if healed is not None:
                        healed.database_name = primary_db_name
                        stream_all_candidates.insert(0, healed)
                        primary_sql = healed.sql_text
                        yield _emit("candidate", {
                            "id": healed.id,
                            "sql": healed.sql_text,
                            "confidence": healed.confidence,
                            "generation_mode": healed.generation_mode,
                            "database_name": primary_db_name,
                        })
                        yield _emit("warning", {
                            "message": f"SQL auto-corrected via self-heal retry "
                                       f"(rounds: {len(healer.history)})"
                        })
                except Exception as e:
                    yield _emit("warning", {"message": f"Self-heal skipped: {e}"})

                # SPEC §6.7: agent_trace for sql_validator
                yield _emit("agent_trace", {
                    "agent": "sql_validator",
                    "status": "completed",
                    "candidates_validated": len(stream_all_candidates),
                })

                # ── Filter out candidates that failed schema validation ──
                # Remove candidates with schema_errors (wrong column/table names)
                # so only executable SQL is shown to the user.
                if stream_all_candidates:
                    _filtered = [
                        cand for cand in stream_all_candidates
                        if not cand.validation
                        or cand.validation.get("passed", False)
                        or not cand.validation.get("schema_errors")
                    ]
                    if _filtered:
                        stream_all_candidates = _filtered

                yield _emit("stage", {"stage": "validate", "status": "completed", "progress": 0.9,
                                       "duration_ms": int((time.perf_counter() - stage_start) * 1000)})

            # ── Step 5: Execute ONLY database-sourced candidates ───
            # Same logic as non-streaming: skip execution for domain/RAG/
            # llm_auto sources since they don't target connected databases.
            stream_execution: dict[str, Any] | None = None
            if body.execute and stream_source == "database":
                # SPEC §6.7: stage event — execute
                yield _emit("stage", {"stage": "execute", "status": "started", "progress": 0.95})
                stage_start = time.perf_counter()
                exec_node = ExecuteSQLNode()
                for cand in stream_all_candidates:
                    if not cand.sql_text:
                        continue
                    # Skip candidates without a database_name (non-database sources)
                    if not cand.database_name:
                        continue
                    # Find the correct connection for this candidate's database
                    cand_exec_conn = None
                    cand_db_name = cand.database_name
                    for db_id, (s, name, _) in stream_schema_map.items():
                        if name == cand_db_name:
                            try:
                                cand_exec_conn = ConnectionFactory.get(db_id)
                            except KeyError:
                                pass
                            break

                    if cand_exec_conn is None:
                        continue

                    # SPEC §6.7: agent_handoff — sql_generator → tool_executor
                    yield _emit("agent_handoff", {
                        "from": "sql_generator",
                        "to": "tool_executor",
                        "reason": "sql_ready",
                        "context_summary": f"candidate={cand.id}, sql_length={len(cand.sql_text)}",
                    })
                    exec_start = time.perf_counter()
                    try:
                        exec_output = await exec_node.execute(
                            NodeInput(
                                query_text=body.nl_text,
                                context={"sql": cand.sql_text, "connection": cand_exec_conn},
                            )
                        )
                        exec_duration_ms = int((time.perf_counter() - exec_start) * 1000)
                        # SPEC §6.7: mcp_call event — record DB tool execution
                        yield _emit("mcp_call", {
                            "tool": "execute_read_query",
                            "server": cand_db_name or "default",
                            "duration_ms": exec_duration_ms,
                        })
                        if exec_output.metadata.get("status") == "success":
                            cand.execution = {
                                "columns": exec_output.result.get("columns", []),
                                "rows": exec_output.result.get("rows", []),
                                "row_count": exec_output.result.get("row_count", 0),
                                "truncated": exec_output.result.get("truncated", False),
                            }
                            if cand.id == (stream_all_candidates[0].id if stream_all_candidates else ""):
                                stream_execution = cand.execution
                            yield _emit("execution", {
                                "candidate_id": cand.id,
                                "columns": cand.execution.get("columns", []),
                                "rows": cand.execution.get("rows", []),
                                "row_count": cand.execution.get("row_count", 0),
                                "truncated": cand.execution.get("truncated", False),
                            })
                    except Exception as e:
                        yield _emit("warning", {"message": f"Execution skipped for {cand.id}: {e}"})

                # SPEC §6.7: agent_trace for tool_executor + stage complete
                yield _emit("agent_trace", {
                    "agent": "tool_executor",
                    "status": "completed",
                    "candidates_executed": len(stream_all_candidates),
                })
                yield _emit("stage", {"stage": "execute", "status": "completed", "progress": 1.0,
                                       "duration_ms": int((time.perf_counter() - stage_start) * 1000)})

            # ── Done ───────────────────────────────────────────
            # Record workflow trace for real-time stats
            response_time_ms = int((time.perf_counter() - start_time) * 1000)
            try:
                from app.api.workflows import record_trace
                nodes_executed = ["parse_nl"]
                if body.link_schema:
                    nodes_executed.append("schema_linking")
                nodes_executed.append("generate_sql")
                if body.run_validation and primary_sql:
                    nodes_executed.append("validate_sql")
                if body.execute and primary_sql:
                    nodes_executed.append("execute_sql")
                record_trace(
                    plan="gensql_agentic",
                    status="success" if primary_sql else "failed",
                    duration_ms=response_time_ms,
                    nl_input=body.nl_text,
                    sql_generated=primary_sql,
                    row_count=stream_execution.get("row_count", 0) if stream_execution else 0,
                    nodes_executed=nodes_executed,
                    error="",
                )
            except Exception:
                pass
            # Record query for learning stats
            try:
                from app.api.learning import record_query
                record_query(
                    nl_text=body.nl_text,
                    sql_text=primary_sql,
                    domain=body.domain,
                )
            except Exception:
                pass

            # Serialize candidates for the done event
            stream_candidates_data = []
            for c in stream_all_candidates:
                stream_candidates_data.append({
                    "id": c.id,
                    "sql_text": c.sql_text,
                    "confidence": c.confidence,
                    "generation_mode": c.generation_mode,
                    "dialect": getattr(c, "dialect", None),
                    "database_name": getattr(c, "database_name", ""),
                    "validation": getattr(c, "validation", None),
                    "execution": getattr(c, "execution", None),
                })

            # Collect all linked tables across databases
            stream_all_linked: list[str] = []
            for db_id, (_, db_name, _) in stream_schema_map.items():
                stream_all_linked.append(db_name)

            yield _emit("done", {
                "query_id": query_id,
                "primary_sql": primary_sql,
                "linked_tables": stream_all_linked,
                "execution": stream_execution,
                "candidates": stream_candidates_data,
                "source": stream_source,
                "source_note": stream_source_note,
            })

        except Exception as e:
            yield _emit("error", {
                "message": str(e),
                "traceback": traceback.format_exc()[:500],
            })

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ── Session & Version endpoints ───────────────────────────────────────

# In-memory session store (populated by query submissions)
_SESSION_STORE: dict[str, dict[str, Any]] = {}


def _get_or_create_session(session_id: str) -> dict[str, Any]:
    """Get or create an in-memory session record."""
    if session_id not in _SESSION_STORE:
        _SESSION_STORE[session_id] = {
            "session_id": session_id,
            "turns": [],
            "created_at": datetime.now().isoformat(),
            "updated_at": datetime.now().isoformat(),
        }
    return _SESSION_STORE[session_id]


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
                return {"status": "ok", "session_id": session_id, "versions": versions, "total": len(versions)}
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
    diff_lines = list(difflib.unified_diff(
        sql1.splitlines(keepends=True),
        sql2.splitlines(keepends=True),
        fromfile=f"v{v1}",
        tofile=f"v{v2}",
        n=3,
    ))
    unified_diff = "".join(diff_lines)

    # Structured diff (line-by-line)
    matcher = difflib.SequenceMatcher(None, sql1.splitlines(), sql2.splitlines())
    structured: list[dict[str, Any]] = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            for k in range(i1, i2):
                structured.append({"tag": "equal", "old_line": k + 1, "new_line": k + 1, "content": sql1.splitlines()[k]})
        elif tag == "replace":
            for k in range(i1, i2):
                structured.append({"tag": "removed", "old_line": k + 1, "content": sql1.splitlines()[k]})
            for k in range(j1, j2):
                structured.append({"tag": "added", "new_line": k + 1, "content": sql2.splitlines()[k]})
        elif tag == "delete":
            for k in range(i1, i2):
                structured.append({"tag": "removed", "old_line": k + 1, "content": sql1.splitlines()[k]})
        elif tag == "insert":
            for k in range(j1, j2):
                structured.append({"tag": "added", "new_line": k + 1, "content": sql2.splitlines()[k]})

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
async def branch_from_version(session_id: str, version_number: int, body: dict | None = None) -> dict:
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
