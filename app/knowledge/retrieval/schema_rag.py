r"""Schema metadata RAG — hybrid search over table/column metadata.

See SPEC §4.2.4 and implementation-plan.md §4.8.5 for the full specification.

This module provides the first concrete RAG application: indexing table/column
:class:`~app.models.rag_schema.SchemaDocument` objects into both LanceDB (dense
vector) and BM25 (sparse keyword) backends, then using RRF fusion to deliver a
single ranked list of relevant tables for a natural-language query.

Usage::

    from app.knowledge.retrieval.embedding import MockEmbeddingProvider, EmbeddingGenerator
    from app.knowledge.retrieval.lancedb_store import LanceDBStore
    from app.knowledge.retrieval.bm25_index import BM25Index
from app.knowledge.retrieval.filters import literal

    rag = SchemaMetadataRAG(
        lancedb_store=LanceDBStore(dimension=256),
        bm25_index=BM25Index(":memory:"),
        embedding_generator=EmbeddingGenerator(provider=MockEmbeddingProvider()),
    )

    # Index
    await rag.index_schemas([SchemaDocument(...), ...])

    # Search
    results = await rag.find_relevant_tables("monthly sales by region", top_k=10)
    for r in results:
        print(r.table_name, r.column_name, r.rrf_score)
"""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.knowledge.retrieval.bm25_index import BM25Index
from app.knowledge.retrieval.embedding import EmbeddingGenerator, MockEmbeddingProvider
from app.knowledge.retrieval.filters import literal
from app.knowledge.retrieval.lancedb_store import (
    NAMESPACE_SCHEMA_METADATA,
    LanceDBStore,
    schema_doc_to_record,
)
from app.knowledge.retrieval.rrf_fusion import RRFFusion
from app.models.rag_schema import (
    SchemaIndexClearResult,
    SchemaIndexNotReadyError,
    SchemaIndexWriteError,
)

# ── Result model ───────────────────────────────────────────────────────


@dataclass
class SchemaSearchResult:
    """A single result from schema hybrid search.

    Carries enough information for downstream Schema Linking to decide
    which tables/columns to include in the SQL generation prompt.
    """

    doc_id: str
    db_id: str
    table_name: str
    column_name: str | None = None
    data_type: str | None = None
    comment: str | None = None
    is_primary_key: bool = False
    is_foreign_key: bool = False
    embedding_text: str = ""
    rrf_score: float = 0.0
    dense_rank: int | None = None
    sparse_rank: int | None = None

    # Full raw dict from the winning backend (for debugging / advanced use)
    raw: dict[str, Any] = field(default_factory=dict, repr=False)
    # Append provenance to preserve the existing positional constructor contract.
    schema_name: str | None = None

    @property
    def is_table_level(self) -> bool:
        """True if this result represents a whole table (not a single column)."""
        return self.column_name is None


# ── SchemaMetadataRAG ──────────────────────────────────────────────────


# Key fields to store in BM25 metadata so results are self-describing even
# without a matching LanceDB result.
_INDEXED_META_KEYS: tuple[str, ...] = (
    "db_id",
    "schema_name",
    "table_name",
    "column_name",
    "data_type",
    "comment",
    "is_primary_key",
    "is_foreign_key",
    "embedding_text",
)


def _extract_index_meta(doc: Any) -> dict[str, Any]:
    """Pull metadata fields from a SchemaDocument for BM25 storage."""
    meta: dict[str, Any] = {}
    for key in _INDEXED_META_KEYS:
        val = getattr(doc, key, None)
        if val is not None:
            meta[key] = val
    return meta


def _row_to_schema_result(row: dict[str, Any]) -> SchemaSearchResult:
    """Convert a fused result dict (from LanceDB or BM25) to SchemaSearchResult."""
    return SchemaSearchResult(
        doc_id=str(row.get("doc_id", "")),
        db_id=str(row.get("db_id", "")),
        table_name=str(row.get("table_name", "")),
        column_name=row.get("column_name") or None,
        data_type=row.get("data_type") or None,
        comment=row.get("comment") or None,
        is_primary_key=bool(row.get("is_primary_key", False)),
        is_foreign_key=bool(row.get("is_foreign_key", False)),
        embedding_text=str(row.get("embedding_text", "")),
        rrf_score=float(row.get("rrf_score", 0.0)),
        dense_rank=row.get("dense_rank"),
        sparse_rank=row.get("sparse_rank"),
        raw=row,
        schema_name=row.get("schema_name"),
    )


# ── Main class ─────────────────────────────────────────────────────────


class SchemaMetadataRAG:
    """Hybrid (dense + sparse) search over table/column metadata.

    Indexes :class:`~app.models.rag_schema.SchemaDocument` objects into both
    LanceDB (vector) and BM25 (keyword) backends, then fuses search results via
    RRF for a single relevance-ranked list.

    Args:
        lancedb_store: Pre-configured :class:`LanceDBStore` (any dimension).
        bm25_index: Pre-configured :class:`BM25Index` (``:memory:`` or file).
        embedding_generator: :class:`EmbeddingGenerator` wrapping any provider.
            Defaults to ``MockEmbeddingProvider`` for testing.
        rrf_fusion: :class:`RRFFusion` with custom k/α.  Defaults to k=60, α=0.5.
    """

    NAMESPACE = NAMESPACE_SCHEMA_METADATA

    def __init__(
        self,
        lancedb_store: LanceDBStore,
        bm25_index: BM25Index,
        embedding_generator: EmbeddingGenerator | None = None,
        rrf_fusion: RRFFusion | None = None,
        state_dir: Path | str | None = None,
    ):
        self._lancedb = lancedb_store
        self._bm25 = bm25_index
        self._embedding = embedding_generator or EmbeddingGenerator(
            provider=MockEmbeddingProvider()
        )
        self._rrf = rrf_fusion or RRFFusion()
        self._unavailable_databases: set[str] = set()
        self._refresh_required_databases: set[str] = set()
        self._state_dir = Path(state_dir) if state_dir is not None else None

        # Ensure both backends are connected and the namespace exists
        self._lancedb.connect()
        self._bm25.connect()
        self._lancedb.ensure_namespace(self.NAMESPACE)
        self._bm25.ensure_namespace(self.NAMESPACE)
        # A marker left behind means a previous replacement never reported success,
        # so the on-disk index cannot be trusted until a full replacement succeeds.
        self._refresh_required_databases.update(self.pending_replacements())

    # ── Properties ─────────────────────────────────────────────────────

    @property
    def embedding_dimension(self) -> int:
        return self._embedding.dimension

    def set_fusion(self, fusion: RRFFusion) -> None:
        """Publish a new immutable-per-search fusion reference, without touching indexes."""
        self._rrf = fusion

    @property
    def rrf_alpha(self) -> float:
        return self._rrf.alpha

    @property
    def rrf_k(self) -> int:
        return self._rrf.k

    # ── Indexing ───────────────────────────────────────────────────────

    async def index_schemas(
        self,
        docs: list[Any],
        language: str = "auto",
        *,
        replace_db_id: str | None = None,
        before_replace: Callable[[], Awaitable[None]] | None = None,
    ) -> int:
        """Serialize replacements per database and validate before any deletion.

        The optional source check runs after embedding validation, including
        empty replacements. It does not create a transaction across backends.
        """
        if before_replace is not None and replace_db_id is None:
            raise ValueError("Source validation requires database replacement")
        if replace_db_id is not None:
            from app.db.runtime import gate

            marker = self._begin_replacement(replace_db_id)
            async with gate(("schema-rag-replace", id(self), replace_db_id)):
                result = await self._index_schemas(
                    docs, language, replace_db_id=replace_db_id, before_replace=before_replace,
                )
            self._finish_replacement(marker)
            return result
        return await self._index_schemas(docs, language)

    async def _index_schemas(
        self, docs: list[Any], language: str = "auto", *,
        replace_db_id: str | None = None,
        before_replace: Callable[[], Awaitable[None]] | None = None,
    ) -> int:
        """Index a batch of :class:`~app.models.rag_schema.SchemaDocument` objects.

        Each document is indexed into **both** LanceDB (with embedding vector) and
        BM25 (with tokenized ``embedding_text``).  Duplicate ``doc_id`` values
        overwrite the previous entry.

        Args:
            docs: List of ``SchemaDocument`` instances.
            language: Language hint for BM25 tokenization.

        Returns:
            Number of documents indexed.
        """
        if replace_db_id is not None and any(d.db_id != replace_db_id for d in docs):
            raise ValueError("Replacement documents must belong to the specified database")
        if not docs:
            if replace_db_id is not None:
                if before_replace is not None:
                    await before_replace()
                self._replace_database(replace_db_id, [], [], language)
            return 0

        # 1. Generate embeddings for all docs
        texts = [d.embedding_text for d in docs]
        emb_result = await self._embedding.embed(texts)
        if len(emb_result.vectors) != len(texts) or any(
            len(vector) != self._lancedb.dimension for vector in emb_result.vectors
        ):
            raise ValueError("Embedding batch count/dimension mismatch; existing index preserved")

        lancedb_records = [
            schema_doc_to_record(doc, vec)
            for doc, vec in zip(docs, emb_result.vectors, strict=False)
        ]
        bm25_docs: list[dict[str, Any]] = []
        for doc in docs:
            bm25_docs.append(
                {
                    "doc_id": doc.doc_id,
                    "text": doc.embedding_text,
                    "metadata": _extract_index_meta(doc),
                }
            )
        if replace_db_id is not None:
            if before_replace is not None:
                await before_replace()
            self._replace_database(replace_db_id, lancedb_records, bm25_docs, language)
        else:
            doc_ids_str = ", ".join(literal(d.doc_id) for d in docs)
            self._lancedb.delete(self.NAMESPACE, f"doc_id IN ({doc_ids_str})")
            self._lancedb.insert(self.NAMESPACE, lancedb_records)
            self._bm25.index(self.NAMESPACE, bm25_docs, language=language)

        return len(docs)

    @property
    def unavailable_databases(self) -> tuple[str, ...]:
        """Process-local recovery failures; a successful scoped replacement repairs them."""
        return tuple(sorted(self._unavailable_databases))

    @property
    def refresh_required_databases(self) -> tuple[str, ...]:
        return tuple(sorted(self._refresh_required_databases))

    def require_refresh(self, db_ids: set[str]) -> None:
        """Block existing contents until a complete replacement or removal succeeds."""
        self._refresh_required_databases.update(db_ids)

    def _marker_path(self, db_id: str) -> Path | None:
        """Where an in-flight replacement marker lives; ``None`` disables markers."""
        if self._state_dir is None:
            return None
        digest = hashlib.sha256(db_id.encode("utf-8")).hexdigest()[:16]
        return self._state_dir / f"index-pending-{digest}.json"

    def pending_replacements(self) -> tuple[str, ...]:
        """Database ids whose last replacement never reported success.

        A marker survives a crash because it is only removed after both backends
        returned successfully. Seeing one at construction time means the on-disk
        index may be half-written, so it must not be treated as ready.
        """
        if self._state_dir is None or not self._state_dir.is_dir():
            return ()
        pending: list[str] = []
        for path in sorted(self._state_dir.glob("index-pending-*.json")):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            db_id = payload.get("db_id") if isinstance(payload, dict) else None
            if isinstance(db_id, str) and db_id:
                pending.append(db_id)
        return tuple(sorted(set(pending)))

    def _begin_replacement(self, db_id: str) -> Path | None:
        marker = self._marker_path(db_id)
        if marker is None:
            return None
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(
            json.dumps({"db_id": db_id, "started_unix": int(time.time())}, ensure_ascii=False),
            encoding="utf-8",
        )
        return marker

    @staticmethod
    def _finish_replacement(marker: Path | None) -> None:
        if marker is not None:
            marker.unlink(missing_ok=True)

    def _replace_database(
        self, db_id: str, records: list[dict[str, Any]],
        sparse_docs: list[dict[str, Any]], language: str,
    ) -> tuple[int, int]:
        # Both snapshots must succeed before any mutation. No await in this section.
        ids = [record["doc_id"] for record in records]
        if len(ids) != len(set(ids)):
            raise ValueError("Replacement document IDs must be unique")
        old_dense = self._lancedb.snapshot_database(self.NAMESPACE, db_id, new_doc_ids=ids)
        old_sparse = self._bm25.snapshot_database(self.NAMESPACE, db_id, new_doc_ids=ids)
        was_unavailable = db_id in self._unavailable_databases
        try:
            self._lancedb.delete(self.NAMESPACE, f"db_id = {literal(db_id)}")
            self._bm25.delete_database(self.NAMESPACE, db_id)
            self._lancedb.insert(self.NAMESPACE, records)
            self._bm25.index(self.NAMESPACE, sparse_docs, language=language)
        except Exception as exc:
            restored = not was_unavailable
            try:
                self._lancedb.restore_database(self.NAMESPACE, db_id, old_dense)
            except Exception:
                restored = False
            try:
                self._bm25.restore_database(self.NAMESPACE, db_id, old_sparse)
            except Exception:
                restored = False
            if not restored:
                self._unavailable_databases.add(db_id)
            raise SchemaIndexWriteError(restored=restored) from exc
        self._unavailable_databases.discard(db_id)
        self._refresh_required_databases.discard(db_id)
        return len(old_dense), len(old_sparse)

    def _check_available(self, db_id: str | None) -> None:
        if (not db_id and self._unavailable_databases) or (
            db_id in self._unavailable_databases
        ):
            raise SchemaIndexWriteError(restored=False)
        if (not db_id and self._refresh_required_databases) or (
            db_id in self._refresh_required_databases
        ):
            raise SchemaIndexNotReadyError("Schema index requires successful source refresh")

    async def index_schema(self, doc: Any, language: str = "auto") -> int:
        """Index a single :class:`~app.models.rag_schema.SchemaDocument`.

        Convenience wrapper around :meth:`index_schemas`.
        """
        return await self.index_schemas([doc], language=language)

    # ── Search ─────────────────────────────────────────────────────────

    async def find_relevant_tables(
        self,
        query: str,
        top_k: int = 10,
        *,
        db_id: str | None = None,
        language: str = "auto",
    ) -> list[SchemaSearchResult]:
        """Hybrid search for tables/columns relevant to a natural-language *query*.

        Executes three steps:
        1. **Dense** — vector similarity search via LanceDB.
        2. **Sparse** — BM25 keyword search via SQLite FTS5.
        3. **Fusion** — RRF rank aggregation into a single relevance-ranked list.

        Args:
            query: Natural-language search query (e.g. ``"monthly sales by region"``).
            top_k: Maximum number of results to return.
            db_id: Optional filter — only return results for this database.
            language: Language hint for BM25 query tokenization.

        Returns:
            List of :class:`SchemaSearchResult`, sorted by RRF score (best first).
        """
        fusion = self._rrf  # Keep one configuration across embedding awaits.
        self._check_available(db_id)
        # 1. Generate query embedding
        query_vec = await self._embedding.embed_single(query)
        self._check_available(db_id)

        # 2. Dense search (LanceDB)
        filter_expr = f"db_id = {literal(db_id)}" if db_id else None
        dense_raw = self._lancedb.search(
            self.NAMESPACE,
            query_vec,
            top_k=max(top_k * 2, 20),  # oversample for better fusion
            filter_expr=filter_expr,
        )
        # Normalise LanceDB distance → pseudo-score (lower distance = better)
        dense_results: list[dict[str, Any]] = []
        for row in dense_raw:
            dist = row.get("_distance", 1.0)
            dense_results.append(
                {
                    **row,
                    "score": 1.0 / (1.0 + dist),  # convert distance to similarity score
                }
            )

        # 3. Sparse search (BM25)
        sparse_raw = self._bm25.search(
            self.NAMESPACE,
            query,
            top_k=max(top_k * 2, 20),
            language=language,
            metadata_filter={"db_id": db_id} if db_id else {},
        )
        # Unpack BM25 metadata → top-level fields so RRF merge works correctly
        sparse_results: list[dict[str, Any]] = []
        for row in sparse_raw:
            unpacked: dict[str, Any] = dict(row)
            meta = unpacked.pop("metadata", {})
            if isinstance(meta, dict):
                unpacked.update(meta)  # surface db_id, table_name, etc.
            sparse_results.append(unpacked)

        # 4. RRF fusion
        fused = fusion.fuse(dense_results, sparse_results, top_k=top_k)

        # 5. Convert to SchemaSearchResult
        results = [_row_to_schema_result(r) for r in fused]

        # 6. Post-filter by db_id if requested (BM25 has no server-side filter)
        if db_id:
            results = [r for r in results if r.db_id == db_id]

        return results

    # ── Management ─────────────────────────────────────────────────────

    async def clear_database(self, db_id: str) -> SchemaIndexClearResult:
        """Compensated scoped clear; the service also serializes full source refreshes."""
        from app.db.runtime import gate

        async with gate(("schema-rag-replace", id(self), db_id)):
            dense_count, sparse_count = self._replace_database(db_id, [], [], "auto")
            return SchemaIndexClearResult(dense_count, sparse_count)

    def clear(self) -> None:
        """Remove all indexed schema documents from both backends."""
        self._lancedb.delete(self.NAMESPACE, "true")
        self._bm25.clear(self.NAMESPACE)
        self._unavailable_databases.clear()
        self._refresh_required_databases.clear()

    def remove_database(self, db_id: str) -> None:
        """Remove all schema documents for a specific *db_id* from both backends.

        Args:
            db_id: Database identifier to remove (e.g. ``"sqlite_test"``).
        """
        self._lancedb.delete(self.NAMESPACE, f"db_id = {literal(db_id)}")
        self._bm25.delete_database(self.NAMESPACE, db_id)
        self._unavailable_databases.discard(db_id)
        self._refresh_required_databases.discard(db_id)

    def stats(self) -> dict[str, int]:
        """Return document counts for both backends."""
        return {
            "lancedb_count": self._lancedb.count(self.NAMESPACE),
            "bm25_count": self._bm25.count(self.NAMESPACE),
        }

    # ── Convenience: table-level aggregation ───────────────────────────

    def get_distinct_tables(self, results: list[SchemaSearchResult]) -> list[str]:
        """Extract a deduplicated, ranked list of table names from search results.

        Args:
            results: Output of :meth:`find_relevant_tables`.

        Returns:
            Table names in the order they first appear (highest RRF score first).
        """
        seen: set[str] = set()
        tables: list[str] = []
        for r in results:
            if r.table_name and r.table_name not in seen:
                seen.add(r.table_name)
                tables.append(r.table_name)
        return tables

    def get_columns_for_table(
        self, results: list[SchemaSearchResult], table_name: str
    ) -> list[SchemaSearchResult]:
        """Filter results to only columns belonging to *table_name*.

        Returns results in their original order.
        """
        return [r for r in results if r.table_name == table_name and r.column_name]
