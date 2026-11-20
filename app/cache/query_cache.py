"""Two-tier NL→SQL query pair cache.

**L1 — Exact match:** SHA256 hash of normalized query → O(1) dict lookup with
LRU eviction.  Catches verbatim or nearly-verbatim repeats (~20% of queries).

**L2 — Semantic match:** LanceDB cosine-distance vector search.  When a query
is not an exact match but is semantically very close (cosine similarity ≥ 0.95),
the cached SQL is reused.  Saves an LLM round-trip (~15% of queries).

See implementation-plan.md §4.10.5–4.10.6 for the design rationale.
"""

from __future__ import annotations

import contextlib
import hashlib
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from app.auth.tenant import get_current_tenant_id
from app.knowledge.retrieval.embedding import EmbeddingGenerator
from app.knowledge.retrieval.lancedb_store import (
    NAMESPACE_QUERY_CACHE,
    LanceDBStore,
    tenant_namespace,
)

# ── Data model ──────────────────────────────────────────────────────────


@dataclass
class CachedQuery:
    """A cached NL→SQL pair returned from :meth:`QueryPairCache.lookup`.

    Attributes:
        cache_id: SHA256 hex digest of the normalized query.
        query: The original (non-normalized) query text.
        sql_text: The cached SQL.
        confidence: Generation confidence from the original run (0.0–1.0).
        domain: Domain active at generation time.
        hit_count: How many times this entry has been returned.
        match_level: ``"exact"`` for L1 hash match, ``"semantic"`` for L2 vector match.
        similarity: Cosine similarity (1 − distance).  1.0 for exact matches.
    """

    cache_id: str
    query: str
    sql_text: str
    confidence: float = 0.0
    domain: str = ""
    hit_count: int = 0
    match_level: str = "exact"
    similarity: float = 1.0
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))


# ── Two-tier cache ──────────────────────────────────────────────────────


@dataclass
class _CacheState:
    entries: OrderedDict[str, CachedQuery] = field(default_factory=OrderedDict)
    exact_hits: int = 0
    semantic_hits: int = 0
    misses: int = 0


class QueryPairCache:
    """Two-tier NL→SQL cache combining exact-match hash and semantic vector search.

    Args:
        lance_db: :class:`~app.knowledge.retrieval.lancedb_store.LanceDBStore`
            instance for L2 semantic caching.  When ``None``, only L1 is used.
        embedding_generator: :class:`~app.knowledge.retrieval.embedding.EmbeddingGenerator`
            for query vectorization.  Required when *lance_db* is provided for L2.
        l1_max_size: Maximum L1 entries per tenant before LRU eviction.
        l2_threshold: Cosine **distance** threshold for L2 semantic match.
            Default ``0.05`` → cosine similarity ≥ 0.95.
    """

    def __init__(
        self,
        lance_db: LanceDBStore | None = None,
        embedding_generator: EmbeddingGenerator | None = None,
        l1_max_size: int = 1000,
        l2_threshold: float = 0.05,
    ) -> None:
        if lance_db is not None and embedding_generator is None:
            raise ValueError(
                "embedding_generator is required when lance_db is provided for L2 caching"
            )
        self._states: dict[int, _CacheState] = {}
        self._l1_max_size = max(1, l1_max_size)
        self._l2_threshold = l2_threshold
        self._lance_db = lance_db
        self._embedding_gen = embedding_generator

    def _tenant_state(self) -> _CacheState:
        tenant_id = get_current_tenant_id()
        if tenant_id not in self._states:
            self._states[tenant_id] = _CacheState()
        return self._states[tenant_id]

    # ── Public API ──────────────────────────────────────────────────────

    async def lookup(self, query: str) -> CachedQuery | None:
        """Look up a cached SQL result for *query*.

        Checks L1 (exact hash) first, then falls back to L2 (semantic vector
        search) when available.

        Returns:
            :class:`CachedQuery` on hit, ``None`` on miss.
        """
        normalized = self._normalize(query)
        cache_id = self._hash(normalized)
        state = self._tenant_state()

        # ── L1: exact match ─────────────────────────────────────────
        if cache_id in state.entries:
            entry = state.entries[cache_id]
            # Move to end (most-recently-used)
            state.entries.move_to_end(cache_id)
            entry.hit_count += 1
            state.exact_hits += 1
            return entry

        # ── L2: semantic match ──────────────────────────────────────
        if self._lance_db is not None and self._embedding_gen is not None:
            result = await self._semantic_lookup(normalized, cache_id)
            if result is not None:
                state.semantic_hits += 1
                # Promote to L1
                self._add_to_l1(state, result)
                return result

        state.misses += 1
        return None

    async def store(
        self,
        query: str,
        sql_text: str,
        confidence: float = 0.0,
        domain: str = "",
    ) -> str:
        """Store a query→SQL pair in both cache tiers.

        Args:
            query: The original natural-language query.
            sql_text: The generated SQL.
            confidence: Generation confidence (0.0–1.0).
            domain: Active domain at generation time.

        Returns:
            The ``cache_id`` (SHA256 hex digest) of the stored entry.
        """
        normalized = self._normalize(query)
        cache_id = self._hash(normalized)

        entry = CachedQuery(
            cache_id=cache_id,
            query=query,
            sql_text=sql_text,
            confidence=confidence,
            domain=domain,
            match_level="exact",
            similarity=1.0,
        )

        # L1
        self._add_to_l1(self._tenant_state(), entry)

        # L2
        if self._lance_db is not None and self._embedding_gen is not None:
            await self._store_semantic(cache_id, normalized, sql_text, confidence, domain)

        return cache_id

    def stats(self) -> dict[str, Any]:
        """Return cache statistics."""
        state = self._tenant_state()
        hits = state.exact_hits + state.semantic_hits
        total = hits + state.misses
        return {
            "l1_size": len(state.entries),
            "l1_max_size": self._l1_max_size,
            "l1_hits": state.exact_hits,
            "l2_hits": state.semantic_hits,
            "misses": state.misses,
            "total_lookups": total,
            "hit_ratio": hits / max(total, 1),
            "l2_enabled": self._lance_db is not None,
            "l2_threshold": self._l2_threshold,
            "l2_row_count": self._l2_count() if self._lance_db else 0,
        }

    def clear(self) -> None:
        """Clear the current tenant's L1 entries and counters, retaining L2."""
        state = self._tenant_state()
        state.entries.clear()
        state.exact_hits = 0
        state.semantic_hits = 0
        state.misses = 0

    # ── L1 helpers ──────────────────────────────────────────────────────

    @staticmethod
    def _normalize(query: str) -> str:
        """Normalize a query for hashing: lowercase, collapse whitespace."""
        return " ".join(query.strip().lower().split())

    @staticmethod
    def _hash(normalized: str) -> str:
        """SHA256 hex digest of a normalized query."""
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()

    def _add_to_l1(self, state: _CacheState, entry: CachedQuery) -> None:
        """Insert into L1, evicting LRU entry if at capacity."""
        entries = state.entries
        if entry.cache_id in entries:
            entries[entry.cache_id] = entry
            entries.move_to_end(entry.cache_id)
            return
        if len(entries) >= self._l1_max_size:
            entries.popitem(last=False)  # pop oldest (Least Recently Used)
        entries[entry.cache_id] = entry

    # ── L2 helpers ──────────────────────────────────────────────────────

    async def _semantic_lookup(
        self, normalized_query: str, cache_id: str
    ) -> CachedQuery | None:
        """Search LanceDB for a semantically similar cached query."""
        try:
            vector = await self._embedding_gen.embed_single(normalized_query)  # type: ignore[union-attr]
        except Exception:
            return None

        try:
            results = self._lance_db.search(  # type: ignore[union-attr]
                namespace=tenant_namespace(NAMESPACE_QUERY_CACHE),
                vector=vector,
                top_k=1,
            )
        except Exception:
            return None

        if not results:
            return None

        distance = results[0].get("_distance", 1.0)
        if distance > self._l2_threshold:
            return None

        return CachedQuery(
            cache_id=cache_id,
            query=normalized_query,
            sql_text=results[0].get("sql_text", ""),
            confidence=results[0].get("confidence", 0.0),
            domain=results[0].get("domain", ""),
            match_level="semantic",
            similarity=round(1.0 - distance, 6),
            created_at=results[0].get("created_at", datetime.now(UTC)),
        )

    async def _store_semantic(
        self,
        cache_id: str,
        normalized_query: str,
        sql_text: str,
        confidence: float,
        domain: str,
    ) -> None:
        """Insert a query→SQL pair into the LanceDB query_cache namespace."""
        try:
            vector = await self._embedding_gen.embed_single(normalized_query)  # type: ignore[union-attr]
        except Exception:
            return

        record = {
            "cache_id": cache_id,
            "query": normalized_query,
            "sql_text": sql_text,
            "confidence": float(confidence),
            "domain": domain,
            "created_at": datetime.now(UTC),
            "vector": vector,
        }

        with contextlib.suppress(Exception):
            self._lance_db.insert(  # type: ignore[union-attr]
                tenant_namespace(NAMESPACE_QUERY_CACHE), [record],
            )

    def _l2_count(self) -> int:
        """Row count of the L2 LanceDB namespace."""
        try:
            return self._lance_db.count(  # type: ignore[union-attr]
                tenant_namespace(NAMESPACE_QUERY_CACHE),
            )
        except Exception:
            return 0
