r"""Metric RAG — hybrid search over business KPIs and metric definitions.

See SPEC §4.2.4 and implementation-plan.md §4.8.2 for the full specification.

This module provides the second concrete RAG application: indexing business
:class:`~app.models.rag_metric.MetricDocument` objects (KPIs, formulas,
dimensions) into both LanceDB (dense vector) and BM25 (sparse keyword) backends,
then using RRF fusion to deliver a single ranked list of matching metrics for a
natural-language query.

Usage::

    from app.knowledge.retrieval.embedding import MockEmbeddingProvider, EmbeddingGenerator
    from app.knowledge.retrieval.lancedb_store import LanceDBStore
    from app.knowledge.retrieval.bm25_index import BM25Index

    rag = MetricRAG(
        lancedb_store=LanceDBStore(dimension=256),
        bm25_index=BM25Index(":memory:"),
        embedding_generator=EmbeddingGenerator(provider=MockEmbeddingProvider()),
    )

    # Index
    await rag.index_metrics([MetricDocument(...), ...])

    # Search
    results = await rag.match_metrics("monthly revenue by region", top_k=10)
    for r in results:
        print(r.name, r.rrf_score)

    # Suggested dimensions
    dims = rag.suggest_dimensions(results)
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass, field
from typing import Any

from app.knowledge.retrieval.bm25_index import BM25Index
from app.knowledge.retrieval.embedding import EmbeddingGenerator, MockEmbeddingProvider
from app.knowledge.retrieval.lancedb_store import (
    NAMESPACE_METRICS,
    LanceDBStore,
    metric_doc_to_record,
)
from app.knowledge.retrieval.rrf_fusion import RRFFusion

# ── Result model ───────────────────────────────────────────────────────


@dataclass
class MetricSearchResult:
    """A single result from metric hybrid search.

    Carries enough information for downstream metric resolution and SQL
    generation to select the right KPI and its associated dimensions.
    """

    doc_id: str
    domain: str = ""
    name: str = ""
    name_zh: str = ""
    aliases: list[str] = field(default_factory=list)
    description: str = ""
    formula: str = ""
    metric_type: str = "derived"
    aggregation: str = "sum"
    dimensions: list[str] = field(default_factory=list)
    time_grain: str = "day"
    precision: int = 2
    sql_template: str = ""
    embedding_text: str = ""
    rrf_score: float = 0.0
    dense_rank: int | None = None
    sparse_rank: int | None = None

    # Full raw dict from the winning backend (for debugging / advanced use)
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    @property
    def has_formula(self) -> bool:
        """True if this metric has a computable formula."""
        return bool(self.formula)

    @property
    def has_sql_template(self) -> bool:
        """True if this metric has a pre-defined SQL template."""
        return bool(self.sql_template)


# ── MetricRAG ──────────────────────────────────────────────────────────


# Key fields to store in BM25 metadata so results are self-describing even
# without a matching LanceDB result.
_INDEXED_META_KEYS: tuple[str, ...] = (
    "domain", "name", "name_zh", "description",
    "formula", "metric_type", "aggregation", "time_grain",
    "embedding_text",
)


def _extract_index_meta(doc: Any) -> dict[str, Any]:
    """Pull metadata fields from a MetricDocument for BM25 storage."""
    meta: dict[str, Any] = {}
    for key in _INDEXED_META_KEYS:
        val = getattr(doc, key, None)
        if val is not None:
            meta[key] = val
    return meta


def _row_to_metric_result(row: dict[str, Any]) -> MetricSearchResult:
    """Convert a fused result dict (from LanceDB or BM25) to MetricSearchResult."""
    # Parse JSON-serialised list fields
    aliases = _parse_json_list(row.get("aliases_json", "[]"))
    dimensions = _parse_json_list(row.get("dimensions_json", "[]"))

    return MetricSearchResult(
        doc_id=str(row.get("doc_id", "")),
        domain=str(row.get("domain", "")),
        name=str(row.get("name", "")),
        name_zh=str(row.get("name_zh", "")),
        aliases=aliases,
        description=str(row.get("description", "")),
        formula=str(row.get("formula", "")),
        metric_type=str(row.get("metric_type", "derived")),
        aggregation=str(row.get("aggregation", "sum")),
        dimensions=dimensions,
        time_grain=str(row.get("time_grain", "day")),
        precision=int(row.get("precision", 2)),
        sql_template=str(row.get("sql_template", "")),
        embedding_text=str(row.get("embedding_text", "")),
        rrf_score=float(row.get("rrf_score", 0.0)),
        dense_rank=row.get("dense_rank"),
        sparse_rank=row.get("sparse_rank"),
        raw=row,
    )


def _parse_json_list(raw: Any) -> list[str]:
    """Safely parse a JSON-serialised list of strings."""
    import json

    if isinstance(raw, list):
        return [str(item) for item in raw]
    try:
        parsed = json.loads(str(raw))
        if isinstance(parsed, list):
            return [str(item) for item in parsed]
    except (json.JSONDecodeError, TypeError):
        pass
    return []


# ── Main class ─────────────────────────────────────────────────────────


class MetricRAG:
    """Hybrid (dense + sparse) search over business metric definitions.

    Indexes :class:`~app.models.rag_metric.MetricDocument` objects into both
    LanceDB (vector) and BM25 (keyword) backends, then fuses search results via
    RRF for a single relevance-ranked list.

    Args:
        lancedb_store: Pre-configured :class:`LanceDBStore` (any dimension).
        bm25_index: Pre-configured :class:`BM25Index` (``:memory:`` or file).
        embedding_generator: :class:`EmbeddingGenerator` wrapping any provider.
            Defaults to ``MockEmbeddingProvider`` for testing.
        rrf_fusion: :class:`RRFFusion` with custom k/α.  Defaults to k=60, α=0.5.
    """

    NAMESPACE = NAMESPACE_METRICS

    def __init__(
        self,
        lancedb_store: LanceDBStore,
        bm25_index: BM25Index,
        embedding_generator: EmbeddingGenerator | None = None,
        rrf_fusion: RRFFusion | None = None,
    ):
        self._lancedb = lancedb_store
        self._bm25 = bm25_index
        self._embedding = embedding_generator or EmbeddingGenerator(
            provider=MockEmbeddingProvider()
        )
        self._rrf = rrf_fusion or RRFFusion()

        # Ensure both backends are connected and the namespace exists
        self._lancedb.connect()
        self._bm25.connect()
        self._lancedb.ensure_namespace(self.NAMESPACE)
        self._bm25.ensure_namespace(self.NAMESPACE)

    # ── Properties ─────────────────────────────────────────────────────

    @property
    def embedding_dimension(self) -> int:
        return self._embedding.dimension

    @property
    def rrf_alpha(self) -> float:
        return self._rrf.alpha

    @property
    def rrf_k(self) -> int:
        return self._rrf.k

    # ── Indexing ───────────────────────────────────────────────────────

    async def index_metrics(
        self,
        docs: list[Any],
        language: str = "auto",
    ) -> int:
        """Index a batch of :class:`~app.models.rag_metric.MetricDocument` objects.

        Each document is indexed into **both** LanceDB (with embedding vector) and
        BM25 (with tokenized ``embedding_text``).  Duplicate ``doc_id`` values
        overwrite the previous entry.

        Args:
            docs: List of ``MetricDocument`` instances.
            language: Language hint for BM25 tokenisation.

        Returns:
            Number of documents indexed.
        """
        if not docs:
            return 0

        # 1. Generate embeddings for all docs
        texts = [d.embedding_text for d in docs]
        emb_result = await self._embedding.embed(texts)

        # 2. Delete existing LanceDB records with same doc_ids, then insert
        doc_ids_str = ", ".join(f"'{d.doc_id}'" for d in docs)
        # table may be empty or filter syntax may fail — non-critical
        with contextlib.suppress(Exception):
            self._lancedb.delete(self.NAMESPACE, f"doc_id IN ({doc_ids_str})")

        lancedb_records = [
            metric_doc_to_record(doc, vec)
            for doc, vec in zip(docs, emb_result.vectors, strict=False)
        ]
        self._lancedb.insert(self.NAMESPACE, lancedb_records)

        # 3. Insert into BM25 (keyword)
        bm25_docs: list[dict[str, Any]] = []
        for doc in docs:
            bm25_docs.append({
                "doc_id": doc.doc_id,
                "text": doc.embedding_text,
                "metadata": _extract_index_meta(doc),
            })
        self._bm25.index(self.NAMESPACE, bm25_docs, language=language)

        return len(docs)

    async def index_metric(self, doc: Any, language: str = "auto") -> int:
        """Index a single :class:`~app.models.rag_metric.MetricDocument`.

        Convenience wrapper around :meth:`index_metrics`.
        """
        return await self.index_metrics([doc], language=language)

    # ── Search ─────────────────────────────────────────────────────────

    async def match_metrics(
        self,
        query: str,
        top_k: int = 10,
        *,
        domain: str | None = None,
        language: str = "auto",
    ) -> list[MetricSearchResult]:
        """Hybrid search for metrics relevant to a natural-language *query*.

        Executes three steps:
        1. **Dense** — vector similarity search via LanceDB.
        2. **Sparse** — BM25 keyword search via SQLite FTS5.
        3. **Fusion** — RRF rank aggregation into a single relevance-ranked list.

        Args:
            query: Natural-language search query (e.g. ``"monthly revenue by region"``).
            top_k: Maximum number of results to return.
            domain: Optional filter — only return results for this domain.
            language: Language hint for BM25 query tokenisation.

        Returns:
            List of :class:`MetricSearchResult`, sorted by RRF score (best first).
        """
        # 1. Generate query embedding
        query_vec = await self._embedding.embed_single(query)

        # 2. Dense search (LanceDB)
        filter_expr = f"domain = '{domain}'" if domain else None
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
            dense_results.append({
                **row,
                "score": 1.0 / (1.0 + dist),
            })

        # 3. Sparse search (BM25)
        sparse_raw = self._bm25.search(
            self.NAMESPACE,
            query,
            top_k=max(top_k * 2, 20),
            language=language,
        )
        # Unpack BM25 metadata → top-level fields so RRF merge works correctly
        sparse_results: list[dict[str, Any]] = []
        for row in sparse_raw:
            unpacked: dict[str, Any] = dict(row)
            meta = unpacked.pop("metadata", {})
            if isinstance(meta, dict):
                unpacked.update(meta)
            sparse_results.append(unpacked)

        # 4. RRF fusion
        fused = self._rrf.fuse(dense_results, sparse_results, top_k=top_k)

        # 5. Convert to MetricSearchResult
        results = [_row_to_metric_result(r) for r in fused]

        # 6. Post-filter by domain if requested (BM25 has no server-side filter)
        if domain:
            results = [r for r in results if r.domain == domain]

        return results

    # ── Management ─────────────────────────────────────────────────────

    def clear(self) -> None:
        """Remove all indexed metric documents from both backends."""
        self._lancedb.drop_namespace(self.NAMESPACE)
        self._lancedb.ensure_namespace(self.NAMESPACE)
        self._bm25.clear(self.NAMESPACE)

    def stats(self) -> dict[str, int]:
        """Return document counts for both backends."""
        return {
            "lancedb_count": self._lancedb.count(self.NAMESPACE),
            "bm25_count": self._bm25.count(self.NAMESPACE),
        }

    # ── Convenience: dimension helpers ─────────────────────────────────

    @staticmethod
    def suggest_dimensions(
        results: list[MetricSearchResult],
        metric_name: str | None = None,
    ) -> list[str]:
        """Extract a deduplicated list of suggested dimensions from search results.

        Args:
            results: Output of :meth:`match_metrics`.
            metric_name: Optional filter — only gather dimensions from results
                matching this metric name.

        Returns:
            Sorted list of unique dimension names across matching results.
        """
        dims: set[str] = set()
        for r in results:
            if metric_name is not None and r.name != metric_name:
                continue
            for d in r.dimensions:
                dims.add(d)
        return sorted(dims)

    def get_metric_names(self, results: list[MetricSearchResult]) -> list[str]:
        """Extract a deduplicated, ranked list of metric names from search results.

        Args:
            results: Output of :meth:`match_metrics`.

        Returns:
            Metric names in the order they first appear (highest RRF score first).
        """
        seen: set[str] = set()
        names: list[str] = []
        for r in results:
            if r.name and r.name not in seen:
                seen.add(r.name)
                names.append(r.name)
        return names
