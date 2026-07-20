r"""Schema Retriever — 5-step hierarchical schema retrieval strategy.

See implementation-plan §4.8.2, §4.8.5 and progress-log §3.8 for the full
specification.

This module provides a knowledge-layer orchestrator that implements the
five-step schema retrieval strategy, integrating RAG hybrid search with
keyword matching, term resolution, and FK expansion.

**Five-step strategy**::

    Step 1: 分析 (Analyse)
        Parse NL query + SQR into a RAG-optimised search query, extracting
        key entities, intent, and context from the structured query rep.

    Step 2: RAG 检索 (RAG Search)
        Execute hybrid (dense + sparse) search via ``SchemaMetadataRAG``
        to find semantically relevant tables and columns.

    Step 3: 术语解析 (Term Resolution)
        Map business terms to schema objects using domain glossary /
        term mappings.  Falls back gracefully when no glossary is available.

    Step 4: FK 扩展 (FK Expansion)
        Expand the table set by 1 level of foreign key references (both
        forward and reverse) so JOIN paths are available.

    Step 5: 注入 (Inject)
        Build a ``SchemaSnapshot`` containing:
          - **Full metadata** for candidate tables (columns, types, comments, FKs)
          - **Table-name-only** entries for non-candidate tables (context)

Usage::

    from app.knowledge.retrieval.schema_rag import SchemaMetadataRAG
    from app.knowledge.schema_retriever import SchemaRetriever

    retriever = SchemaRetriever(schema_rag)
    result = await retriever.retrieve(
        query="monthly sales by region",
        schema=db_schema,
        sqr=sqr,
    )
    print(result.tables)           # ["orders", "users", "products"]
    print(result.filtered_schema)  # SchemaSnapshot: 3 full tables + 10 names
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.models.query import SQR
from app.models.schema import SchemaSnapshot, TableSchema

# ── Result models ──────────────────────────────────────────────────────


@dataclass
class SchemaRetrievalColumn:
    """A single column-level match from schema retrieval."""

    name: str
    table: str | None = None
    confidence: float = 0.0
    candidates: list[str] | None = None  # alternative tables (when ambiguous)


@dataclass
class SchemaRetrievalResult:
    """Output of the 5-step schema retrieval strategy."""

    tables: list[str] = field(default_factory=list)
    columns: list[SchemaRetrievalColumn] = field(default_factory=list)
    filtered_schema: SchemaSnapshot | None = None
    method: str = "none"  # "keyword", "rag", "hybrid", "none"
    confidence: float = 0.0
    search_results: list[Any] = field(default_factory=list)  # raw RAG hits

    @property
    def table_count(self) -> int:
        return len(self.tables)

    @property
    def column_count(self) -> int:
        return len(self.columns)

    @property
    def has_schema(self) -> bool:
        return self.filtered_schema is not None and bool(self.filtered_schema.tables)


# ── SchemaRetriever ────────────────────────────────────────────────────


class SchemaRetriever:
    r"""Orchestrate the 5-step hierarchical schema retrieval strategy.

    Args:
        schema_rag: Optional ``SchemaMetadataRAG`` for hybrid search.
            When ``None``, falls back to keyword-only matching.
        glossary: Optional glossary / term-mapping object (Phase 3).
            Should expose a ``resolve(term: str) -> list[str]`` method
            returning candidate table/column names.

    **Configuration** (passed via ``**kwargs`` to ``retrieve()``):

    ================== ======= ==========================================
    Key                 Default Description
    ================== ======= ==========================================
    ``top_k``           10      Max RAG results
    ``fk_expand``       True    Enable FK expansion
    ``match_threshold`` 0.6     Min confidence for keyword matching
    ``language``        "auto"  Language hint for tokenisation
    ================== ======= ==========================================
    """

    def __init__(
        self,
        schema_rag: Any = None,
        glossary: Any = None,
    ) -> None:
        self._rag = schema_rag
        self._glossary = glossary

    # ── Properties ─────────────────────────────────────────────────────

    @property
    def has_rag(self) -> bool:
        return self._rag is not None

    @property
    def has_glossary(self) -> bool:
        return self._glossary is not None

    # ── Main entry point ───────────────────────────────────────────────

    async def retrieve(
        self,
        query: str,
        schema: SchemaSnapshot,
        *,
        sqr: SQR | None = None,
        db_id: str | None = None,
        top_k: int = 10,
        fk_expand: bool = True,
        match_threshold: float = 0.6,
        language: str = "auto",
        **__kwargs: Any,
    ) -> SchemaRetrievalResult:
        """Execute the full 5-step schema retrieval pipeline.

        Args:
            query: Natural-language query text.
            schema: Full ``SchemaSnapshot`` for the target database.
            sqr: Pre-parsed ``SQR`` (optional — built from *query* if absent).
            db_id: Optional database filter.
            top_k: Max RAG results.
            fk_expand: Whether to expand via foreign keys.
            match_threshold: Minimum confidence for candidate inclusion.
            language: Language hint.

        Returns:
            ``SchemaRetrievalResult`` with ranked tables, columns, and
            a filtered ``SchemaSnapshot``.
        """
        if sqr is None:
            sqr = SQR(raw_text=query)

        if not schema or not schema.tables:
            return SchemaRetrievalResult(method="none")

        # ── Step 1: 分析 ───────────────────────────────────────────
        search_query = _build_search_query(query, sqr)

        # ── Step 2: RAG 检索 ───────────────────────────────────────
        rag_hits: list[Any] = []
        rag_tables: set[str] = set()
        rag_columns: list[dict[str, Any]] = []

        if self.has_rag:
            try:
                rag_hits = await self._rag.find_relevant_tables(
                    search_query, top_k=top_k, db_id=db_id, language=language,
                )
            except Exception:
                rag_hits = []

            for hit in rag_hits:
                tname = getattr(hit, "table_name", None)
                if tname and tname in schema.tables:
                    rag_tables.add(tname)
                cname = getattr(hit, "column_name", None)
                if cname and tname:
                    rag_columns.append({
                        "name": cname,
                        "table": tname,
                        "confidence": hit.rrf_score,
                    })

        # ── Step 3: 术语解析 ───────────────────────────────────────
        term_tables, term_columns = _resolve_terms(
            query, sqr, schema, self._glossary, match_threshold,
        )

        # ── Step 4: Merge + FK 扩展 ────────────────────────────────
        # Merge RAG results + keyword/term matches
        all_tables: dict[str, float] = {}

        # RAG tables (RRF scores)
        for hit in rag_hits:
            tname = getattr(hit, "table_name", None)
            if tname and tname in schema.tables:
                score = getattr(hit, "rrf_score", 0.0)
                all_tables[tname] = max(all_tables.get(tname, 0.0), score)

        # Keyword-matched tables (from SQR candidates)
        keyword_tables = _keyword_match_tables(
            _gather_candidates(sqr), schema, match_threshold,
        )
        for tname, conf in keyword_tables.items():
            all_tables[tname] = max(all_tables.get(tname, 0.0), conf)

        # Term-resolved tables
        for tname, conf in term_tables.items():
            all_tables[tname] = max(all_tables.get(tname, 0.0), conf)

        # Sort and select
        ranked_tables = sorted(all_tables.items(), key=lambda x: -x[1])
        matched_tables = [t for t, _ in ranked_tables]

        # Merge columns
        matched_columns: dict[str, dict[str, Any]] = {}
        for col in rag_columns:
            key = f"{col['table']}.{col['name']}" if col.get("table") else col["name"]
            if key not in matched_columns or col["confidence"] > matched_columns[key].get(
                "confidence", 0
            ):
                matched_columns[key] = dict(col)
        for col in term_columns:
            key = f"{col.get('table', '')}.{col['name']}"
            if key not in matched_columns or col.get("confidence", 0) > matched_columns[key].get(
                "confidence", 0
            ):
                matched_columns[key] = dict(col)

        # ── Step 5: FK 扩展 ────────────────────────────────────────
        if fk_expand:
            matched_tables = _expand_foreign_keys(matched_tables, schema)

        # ── Step 6: 注入 (build filtered schema) ───────────────────
        linked_schema = _build_filtered_schema(schema, matched_tables)

        # Confidence
        confidence = _compute_retrieval_confidence(
            rag_hits, matched_tables, matched_columns,
        )
        method = "hybrid" if (self.has_rag and rag_hits) else "keyword"

        resolved_columns = [
            SchemaRetrievalColumn(
                name=c["name"],
                table=c.get("table"),
                confidence=c.get("confidence", 0.0),
                candidates=c.get("candidates"),
            )
            for c in matched_columns.values()
        ]

        return SchemaRetrievalResult(
            tables=matched_tables,
            columns=resolved_columns,
            filtered_schema=linked_schema,
            method=method,
            confidence=confidence,
            search_results=rag_hits,
        )


# ── Step 1: Analyse ───────────────────────────────────────────────────


def _build_search_query(query: str, sqr: SQR) -> str:
    """Build a RAG-optimised search query from NL text + SQR context.

    Augments the original query with entity names, table references,
    and intent hints extracted from the structured representation.
    """
    parts: list[str] = [query]

    # Intent hint
    if sqr.intent.value != "unknown":
        parts.append(sqr.intent.value)

    # Entity names
    for entity in sqr.entities:
        if entity.name:
            parts.append(entity.name)

    # Explicit target tables
    for t in sqr.target_tables:
        if t:
            parts.append(t)

    # Time range hints
    if sqr.time_range:
        if sqr.time_range.raw_expression:
            parts.append(sqr.time_range.raw_expression)
        if sqr.time_range.unit:
            parts.append(sqr.time_range.unit)

    return " ".join(parts)


# ── Step 2: RAG retrieval (delegated to SchemaMetadataRAG) ────────────
#   — No standalone logic; embedded directly in retrieve() above.


# ── Step 3: Term resolution ───────────────────────────────────────────


def _resolve_terms(
    query: str,
    sqr: SQR,
    schema: SchemaSnapshot,
    glossary: Any | None,
    threshold: float,
) -> tuple[dict[str, float], list[dict[str, Any]]]:
    """Resolve business terms to schema table/column names.

    Uses the optional *glossary* if available; otherwise falls back to
    extracting terms from the SQR entities and target tables.
    """
    tables: dict[str, float] = {}
    columns: list[dict[str, Any]] = []

    if glossary is not None:
        # Attempt to resolve via glossary
        candidates: list[str] = []
        for entity in sqr.entities:
            candidates.append(entity.name)
        # Simple keyword extraction from query
        for word in query.replace(",", " ").split():
            word = word.strip()
            if len(word) >= 2:
                candidates.append(word)

        for term in candidates:
            try:
                resolved: list[str] = glossary.resolve(term) if hasattr(glossary, "resolve") else []
                for name in resolved:
                    # Check if it's a table or column
                    if name in schema.tables:
                        tables[name] = max(tables.get(name, 0.0), 0.85)
                    else:
                        # Try column match
                        for t_name, t in schema.tables.items():
                            if t.get_column(name):
                                columns.append({
                                    "name": name,
                                    "table": t_name,
                                    "confidence": 0.85,
                                })
                                break
            except Exception:
                pass
    else:
        # Fallback: keyword matching on entity names
        for entity in sqr.entities:
            name = entity.name
            if name in schema.tables:
                tables[name] = max(tables.get(name, 0.0), 0.8)

    return tables, columns


# ── Step 4: Keyword matching (shared by analyse and term steps) ───────


def _gather_candidates(sqr: SQR) -> list[dict[str, Any]]:
    """Gather table/column name candidates from an SQR."""
    candidates: list[dict[str, Any]] = []

    for t in sqr.target_tables:
        candidates.append({"name": t, "kind": "table", "source": "target_tables"})

    for entity in sqr.entities:
        kind = "table" if entity.type in ("table", "table_ref") else "column"
        candidates.append({"name": entity.name, "kind": kind, "source": "entity"})

    # Tokenize raw query for Chinese text
    text = sqr.raw_text
    import re
    cleaned = re.sub(r"[，。！？、的了吗呢么在与和或从按按照以]", " ", text)
    for word in cleaned.split():
        word = word.strip()
        if len(word) >= 2 and word not in {c["name"] for c in candidates}:
            candidates.append({"name": word, "kind": "column", "source": "nl_text"})

    return candidates


def _keyword_match_tables(
    candidates: list[dict[str, Any]],
    schema: SchemaSnapshot,
    threshold: float,
) -> dict[str, float]:
    """Match candidate names against schema tables using keyword/fuzzy matching."""
    matched: dict[str, float] = {}

    for candidate in candidates:
        name = candidate["name"]
        kind = candidate["kind"]

        if kind == "table":
            best_table, best_conf = _best_table_match(name, schema)
            if best_table and best_conf >= threshold:
                matched[best_table] = max(matched.get(best_table, 0.0), best_conf)

    return matched


def _best_table_match(name: str, schema: SchemaSnapshot) -> tuple[str | None, float]:
    """Find the best table match for *name*."""
    name_lower = name.lower().strip()

    for t_name in schema.tables:
        if t_name == name:
            return t_name, 1.0
        if t_name.lower() == name_lower:
            return t_name, 0.95

    for t_name, t in schema.tables.items():
        if t.comment and name_lower in t.comment.lower():
            return t_name, 0.7

    for t_name in schema.tables:
        t_lower = t_name.lower()
        if name_lower in t_lower or t_lower in name_lower:
            return t_name, 0.65

    return None, 0.0


# ── Step 5: FK expansion ──────────────────────────────────────────────


def _expand_foreign_keys(
    tables: list[str], schema: SchemaSnapshot
) -> list[str]:
    """Expand table list by 1 level of foreign key references.

    Iterates until the set stabilises (handles FK chains).
    """
    expanded = set(tables)

    while True:
        size_before = len(expanded)
        for t_name in list(expanded):
            t = schema.get_table(t_name)
            if t is None:
                continue
            for fk in t.foreign_keys:
                expanded.add(fk.ref_table)
        for other_name, other_t in schema.tables.items():
            for fk in other_t.foreign_keys:
                if fk.ref_table in expanded:
                    expanded.add(other_name)
        if len(expanded) == size_before:
            break

    # Preserve original order, append new entries
    result = list(tables)
    for t in sorted(expanded):
        if t not in result:
            result.append(t)
    return result


# ── Step 6: Inject (build filtered schema) ─────────────────────────────


def _build_filtered_schema(
    schema: SchemaSnapshot, tables: list[str]
) -> SchemaSnapshot:
    """Build a filtered SchemaSnapshot — full metadata for candidates,
    table-name-only stubs for the rest.
    """
    filtered: dict[str, TableSchema] = {}

    for t_name in tables:
        t = schema.get_table(t_name)
        if t is not None:
            filtered[t_name] = t
        else:
            # Unknown table — create a minimal stub
            filtered[t_name] = TableSchema(
                name=t_name,
                columns=[],
                comment="[inferred — not in source schema]",
            )

    # Add non-candidate tables as name-only stubs (for LLM context)
    for t_name in schema.tables:
        if t_name not in filtered:
            filtered[t_name] = TableSchema(
                name=t_name,
                columns=[],  # no column detail for non-candidates
                comment=schema.tables[t_name].comment if schema.tables[t_name].comment else "",
            )

    return SchemaSnapshot(
        database_type=schema.database_type,
        database_name=schema.database_name,
        tables=filtered,
    )


# ── Confidence scoring ────────────────────────────────────────────────


def _compute_retrieval_confidence(
    rag_hits: list[Any],
    tables: list[str],
    columns: dict[str, dict[str, Any]],
) -> float:
    """Compute overall retrieval confidence from RAG scores + coverage."""
    if not tables:
        return 0.0

    # Average RRF score of matched tables
    rrf_scores: list[float] = []
    for hit in rag_hits:
        tname = getattr(hit, "table_name", None)
        if tname in tables:
            score = getattr(hit, "rrf_score", 0.0)
            if score > 0:
                rrf_scores.append(score)

    rrf_avg = sum(rrf_scores) / max(len(rrf_scores), 1) if rrf_scores else 0.0

    # Column confidence average
    col_avg = (
        sum(c.get("confidence", 0.0) for c in columns.values()) / max(len(columns), 1)
        if columns
        else 1.0
    )

    # Blend: 40% RRF average + 40% column confidence + 20% coverage
    coverage = min(len(tables) / max(len(rag_hits), 1), 1.0) if rag_hits else 0.5

    return round(0.4 * rrf_avg + 0.4 * col_avg + 0.2 * coverage, 3)
