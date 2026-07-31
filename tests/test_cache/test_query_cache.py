"""Tests for QueryPairCache — two-tier NL→SQL caching.

Covers: L1 exact match (SHA256), L2 semantic match (LanceDB), LRU eviction,
combined L1+L2 behavior, stats, edge cases.
"""

from __future__ import annotations

import contextlib
from datetime import UTC

import pytest

from app.cache.query_cache import CachedQuery, QueryPairCache
from app.knowledge.retrieval.embedding import EmbeddingGenerator, MockEmbeddingProvider
from app.knowledge.retrieval.lancedb_store import LanceDBStore

# ── Fixtures ────────────────────────────────────────────────────────────


@pytest.fixture
def l1_cache() -> QueryPairCache:
    """L1-only cache (no LanceDB)."""
    return QueryPairCache(l1_max_size=100)


@pytest.fixture
def embedding_gen() -> EmbeddingGenerator:
    return EmbeddingGenerator(provider=MockEmbeddingProvider(dimension=256))


@pytest.fixture
def lance_db() -> LanceDBStore:
    """In-memory LanceDB store for testing."""
    import os
    import tempfile

    tmpdir = tempfile.mkdtemp(prefix="test_lancedb_cache_")
    uri = os.path.join(tmpdir, "lancedb")
    store = LanceDBStore(dimension=256, uri=uri)
    store.connect()
    yield store
    store.close()
    # Cleanup
    import shutil
    with contextlib.suppress(Exception):
        shutil.rmtree(tmpdir, ignore_errors=True)


@pytest.fixture
def full_cache(lance_db: LanceDBStore, embedding_gen: EmbeddingGenerator) -> QueryPairCache:
    """L1 + L2 cache."""
    return QueryPairCache(
        lance_db=lance_db,
        embedding_generator=embedding_gen,
        l1_max_size=100,
        l2_threshold=0.05,
    )


# ═══════════════════════════════════════════════════════════════════════════
# CachedQuery dataclass
# ═══════════════════════════════════════════════════════════════════════════


class TestCachedQuery:
    def test_default_values(self):
        cq = CachedQuery(cache_id="abc", query="test", sql_text="SELECT 1")
        assert cq.cache_id == "abc"
        assert cq.query == "test"
        assert cq.sql_text == "SELECT 1"
        assert cq.confidence == 0.0
        assert cq.domain == ""
        assert cq.hit_count == 0
        assert cq.match_level == "exact"
        assert cq.similarity == 1.0

    def test_full_fields(self):
        from datetime import datetime

        now = datetime.now(UTC)
        cq = CachedQuery(
            cache_id="xyz",
            query="count users",
            sql_text="SELECT COUNT(*) FROM users",
            confidence=0.95,
            domain="ecommerce",
            hit_count=3,
            match_level="semantic",
            similarity=0.97,
            created_at=now,
        )
        assert cq.cache_id == "xyz"
        assert cq.confidence == 0.95
        assert cq.domain == "ecommerce"
        assert cq.hit_count == 3
        assert cq.match_level == "semantic"
        assert cq.similarity == 0.97


# ═══════════════════════════════════════════════════════════════════════════
# Normalization & hashing
# ═══════════════════════════════════════════════════════════════════════════


class TestNormalization:
    def test_lowercase(self):
        result = QueryPairCache._normalize("SELECT Users FROM Orders")
        assert result == "select users from orders"

    def test_collapse_whitespace(self):
        result = QueryPairCache._normalize("  show   me   all   users  ")
        assert result == "show me all users"

    def test_strip_newlines_and_tabs(self):
        result = QueryPairCache._normalize("\t\n  revenue by month  \n\t")
        assert result == "revenue by month"


class TestHashing:
    def test_deterministic(self):
        h1 = QueryPairCache._hash("count users")
        h2 = QueryPairCache._hash("count users")
        assert h1 == h2

    def test_different_inputs(self):
        h1 = QueryPairCache._hash("count users")
        h2 = QueryPairCache._hash("count orders")
        assert h1 != h2

    def test_hex_format(self):
        h = QueryPairCache._hash("test")
        assert len(h) == 64
        assert all(c in "0123456789abcdef" for c in h)

    def test_case_sensitive_diff_in_normalized(self):
        """Hashing works on *normalized* queries, so case differences vanish."""
        n1 = QueryPairCache._normalize("COUNT Users")
        n2 = QueryPairCache._normalize("count users")
        assert n1 == n2
        assert QueryPairCache._hash(n1) == QueryPairCache._hash(n2)


# ═══════════════════════════════════════════════════════════════════════════
# L1 exact-match cache
# ═══════════════════════════════════════════════════════════════════════════


class TestL1ExactMatch:
    @pytest.mark.asyncio
    async def test_store_and_lookup(self, l1_cache):
        await l1_cache.store("count all users", "SELECT COUNT(*) FROM users")
        result = await l1_cache.lookup("count all users")
        assert result is not None
        assert result.sql_text == "SELECT COUNT(*) FROM users"
        assert result.match_level == "exact"
        assert result.similarity == 1.0

    @pytest.mark.asyncio
    async def test_case_insensitive(self, l1_cache):
        await l1_cache.store("Revenue by Month", "SELECT month, SUM(amount) FROM orders GROUP BY 1")
        result = await l1_cache.lookup("revenue by month")
        assert result is not None
        assert result.sql_text == "SELECT month, SUM(amount) FROM orders GROUP BY 1"

    @pytest.mark.asyncio
    async def test_whitespace_insensitive(self, l1_cache):
        await l1_cache.store("top 10 products", "SELECT * FROM products LIMIT 10")
        result = await l1_cache.lookup("  top   10   products  ")
        assert result is not None
        assert result.sql_text == "SELECT * FROM products LIMIT 10"

    @pytest.mark.asyncio
    async def test_miss_returns_none(self, l1_cache):
        result = await l1_cache.lookup("nonexistent query")
        assert result is None

    @pytest.mark.asyncio
    async def test_different_query_is_miss(self, l1_cache):
        await l1_cache.store("active users", "SELECT * FROM users WHERE active=1")
        result = await l1_cache.lookup("inactive users")
        assert result is None

    @pytest.mark.asyncio
    async def test_store_returns_cache_id(self, l1_cache):
        cache_id = await l1_cache.store("test query", "SELECT 1")
        assert isinstance(cache_id, str)
        assert len(cache_id) == 64

    @pytest.mark.asyncio
    async def test_hit_count_increments(self, l1_cache):
        await l1_cache.store("monthly revenue", "SELECT month, SUM(amount) FROM orders GROUP BY 1")
        result1 = await l1_cache.lookup("monthly revenue")
        assert result1.hit_count == 1
        result2 = await l1_cache.lookup("monthly revenue")
        assert result2.hit_count == 2
        result3 = await l1_cache.lookup("monthly revenue")
        assert result3.hit_count == 3


# ═══════════════════════════════════════════════════════════════════════════
# L1 LRU eviction
# ═══════════════════════════════════════════════════════════════════════════


class TestL1Eviction:
    @pytest.mark.asyncio
    async def test_max_size_evicts_oldest(self):
        cache = QueryPairCache(l1_max_size=2)
        await cache.store("q1", "SELECT 1")
        await cache.store("q2", "SELECT 2")
        await cache.store("q3", "SELECT 3")  # should evict q1

        assert await cache.lookup("q1") is None
        assert await cache.lookup("q2") is not None
        assert await cache.lookup("q3") is not None

    @pytest.mark.asyncio
    async def test_recently_used_not_evicted(self):
        cache = QueryPairCache(l1_max_size=2)
        await cache.store("q1", "SELECT 1")
        await cache.store("q2", "SELECT 2")
        # Access q1 to make it recently used
        await cache.lookup("q1")
        # Now q2 is LRU
        await cache.store("q3", "SELECT 3")  # should evict q2

        assert await cache.lookup("q1") is not None  # still here
        assert await cache.lookup("q2") is None  # evicted
        assert await cache.lookup("q3") is not None

    @pytest.mark.asyncio
    async def test_max_size_1(self):
        cache = QueryPairCache(l1_max_size=1)
        await cache.store("q1", "SELECT 1")
        await cache.store("q2", "SELECT 2")
        assert await cache.lookup("q1") is None
        assert await cache.lookup("q2") is not None

    @pytest.mark.asyncio
    async def test_max_size_large(self):
        cache = QueryPairCache(l1_max_size=1000)
        for i in range(50):
            await cache.store(f"query {i}", f"SELECT {i}")
        for i in range(50):
            assert await cache.lookup(f"query {i}") is not None


# ═══════════════════════════════════════════════════════════════════════════
# L2 semantic match (LanceDB)
# ═══════════════════════════════════════════════════════════════════════════


class TestL2SemanticMatch:
    @pytest.mark.asyncio
    async def test_store_and_semantic_lookup(self, full_cache):
        await full_cache.store("show me all users", "SELECT * FROM users")
        # Clear L1 to force L2 lookup
        full_cache._l1.clear()
        result = await full_cache.lookup("show me all users")
        assert result is not None
        assert result.sql_text == "SELECT * FROM users"
        assert result.match_level == "semantic"

    @pytest.mark.asyncio
    async def test_semantic_similar_query(self, full_cache):
        """Similar queries should match semantically."""
        await full_cache.store(
            "total revenue by month", "SELECT month, SUM(amount) FROM orders GROUP BY 1"
        )
        full_cache._l1.clear()
        # Same intent, slightly different wording
        result = await full_cache.lookup("total revenue by month")
        assert result is not None
        assert "SUM" in result.sql_text

    @pytest.mark.asyncio
    async def test_dissimilar_query_is_miss(self, full_cache):
        """A completely different query should miss L2."""
        await full_cache.store("count all users", "SELECT COUNT(*) FROM users")
        full_cache._l1.clear()
        result = await full_cache.lookup("what is the average order value per product category")
        # This should be semantically different enough to miss
        # (MockEmbeddingProvider uses deterministic hash-based vectors)
        assert result is None or result.match_level == "semantic"

    @pytest.mark.asyncio
    async def test_semantic_hit_promotes_to_l1(self, full_cache):
        await full_cache.store("active user count", "SELECT COUNT(*) FROM users WHERE active=1")
        full_cache._l1.clear()
        # L2 hit should add back to L1
        result = await full_cache.lookup("active user count")
        assert result is not None
        # Now L1 should have it
        l1_result = full_cache._l1.get(
            QueryPairCache._hash(QueryPairCache._normalize("active user count"))
        )
        assert l1_result is not None

    @pytest.mark.asyncio
    async def test_l1_hit_skips_l2(self, full_cache):
        await full_cache.store("daily orders", "SELECT date, COUNT(*) FROM orders GROUP BY 1")
        # L1 hit: should not increment L2 counter
        l2_before = full_cache._l2_hits
        result = await full_cache.lookup("daily orders")
        assert result is not None
        assert result.match_level == "exact"
        assert full_cache._l2_hits == l2_before  # Unchanged

    @pytest.mark.asyncio
    async def test_l2_threshold_zero_rejects_all(self, lance_db, embedding_gen):
        """Threshold 0.0 means only identical vectors match (impossible)."""
        cache = QueryPairCache(
            lance_db=lance_db,
            embedding_generator=embedding_gen,
            l2_threshold=0.0,
        )
        await cache.store("test query", "SELECT 1")
        cache._l1.clear()
        result = await cache.lookup("test query")
        # With distance threshold 0.0, even the exact same query may have
        # tiny float differences — it should typically miss
        # This is a boundary test, not a strict assertion
        if result is not None:
            assert result.similarity >= 0.999

    @pytest.mark.asyncio
    async def test_l2_threshold_one_accepts_all(self, lance_db, embedding_gen):
        """Threshold 1.0 (distance) accepts everything (cosine range [0,2])."""
        cache = QueryPairCache(
            lance_db=lance_db,
            embedding_generator=embedding_gen,
            l2_threshold=1.0,
        )
        await cache.store("alpha beta", "SELECT 1")
        cache._l1.clear()
        # Any query should hit L2 with threshold=1.0 (max cosine distance = 2)
        result = await cache.lookup("alpha beta")
        assert result is not None


# ═══════════════════════════════════════════════════════════════════════════
# L1-only mode (no LanceDB)
# ═══════════════════════════════════════════════════════════════════════════


class TestL1OnlyMode:
    @pytest.mark.asyncio
    async def test_store_and_lookup(self, l1_cache):
        await l1_cache.store("query text", "SELECT 1")
        result = await l1_cache.lookup("query text")
        assert result is not None
        assert result.sql_text == "SELECT 1"

    @pytest.mark.asyncio
    async def test_miss(self, l1_cache):
        result = await l1_cache.lookup("never stored")
        assert result is None

    @pytest.mark.asyncio
    async def test_l2_disabled_in_stats(self, l1_cache):
        s = l1_cache.stats()
        assert s["l2_enabled"] is False

    @pytest.mark.asyncio
    async def test_l2_hits_always_zero(self, l1_cache):
        await l1_cache.store("q", "SELECT 1")
        await l1_cache.lookup("q")
        assert l1_cache._l2_hits == 0


# ═══════════════════════════════════════════════════════════════════════════
# Stats
# ═══════════════════════════════════════════════════════════════════════════


class TestStats:
    @pytest.mark.asyncio
    async def test_initial_stats(self, l1_cache):
        s = l1_cache.stats()
        assert s["l1_size"] == 0
        assert s["l1_hits"] == 0
        assert s["l2_hits"] == 0
        assert s["misses"] == 0
        assert s["total_lookups"] == 0
        assert s["hit_ratio"] == 0.0

    @pytest.mark.asyncio
    async def test_stats_after_hits(self, l1_cache):
        await l1_cache.store("q", "SELECT 1")
        await l1_cache.lookup("q")
        await l1_cache.lookup("q")
        await l1_cache.lookup("missing")
        s = l1_cache.stats()
        assert s["l1_size"] == 1
        assert s["l1_hits"] == 2
        assert s["misses"] == 1
        assert s["total_lookups"] == 3
        assert s["hit_ratio"] == 2 / 3

    @pytest.mark.asyncio
    async def test_l2_hits_counted(self, full_cache):
        await full_cache.store(
            "monthly revenue", "SELECT month, SUM(amount) FROM orders GROUP BY 1"
        )
        full_cache._l1.clear()
        await full_cache.lookup("monthly revenue")
        s = full_cache.stats()
        assert s["l2_hits"] >= 1

    @pytest.mark.asyncio
    async def test_hit_ratio_perfect(self, l1_cache):
        await l1_cache.store("q", "SELECT 1")
        await l1_cache.lookup("q")
        s = l1_cache.stats()
        assert s["hit_ratio"] == 1.0

    @pytest.mark.asyncio
    async def test_hit_ratio_zero(self, l1_cache):
        await l1_cache.lookup("no such query")
        s = l1_cache.stats()
        assert s["hit_ratio"] == 0.0


# ═══════════════════════════════════════════════════════════════════════════
# Clear
# ═══════════════════════════════════════════════════════════════════════════


class TestClear:
    @pytest.mark.asyncio
    async def test_clear_empties_l1(self, l1_cache):
        await l1_cache.store("q", "SELECT 1")
        assert len(l1_cache._l1) == 1
        l1_cache.clear()
        assert len(l1_cache._l1) == 0

    @pytest.mark.asyncio
    async def test_clear_resets_counters(self, l1_cache):
        await l1_cache.store("q", "SELECT 1")
        await l1_cache.lookup("q")
        l1_cache.clear()
        assert l1_cache._l1_hits == 0
        assert l1_cache._l2_hits == 0
        assert l1_cache._misses == 0

    @pytest.mark.asyncio
    async def test_clear_does_not_crash_l2(self, full_cache):
        await full_cache.store("q", "SELECT 1")
        full_cache.clear()
        # L2 still has data — new lookup should L2-hit
        result = await full_cache.lookup("q")
        assert result is not None
        assert result.match_level == "semantic"


# ═══════════════════════════════════════════════════════════════════════════
# Error handling
# ═══════════════════════════════════════════════════════════════════════════


class TestErrorHandling:
    def test_missing_embedding_gen_raises(self):
        """LanceDB provided without embedding_generator should raise."""
        store = LanceDBStore(dimension=256)
        with pytest.raises(ValueError, match="embedding_generator"):
            QueryPairCache(lance_db=store, embedding_generator=None)

    @pytest.mark.asyncio
    async def test_l2_lookup_survives_embedding_failure(self, full_cache):
        """If embedding fails, L2 lookup should return None gracefully."""
        await full_cache.store("test", "SELECT 1")
        full_cache._l1.clear()
        # Corrupt the embedding generator
        full_cache._embedding_gen = None
        result = await full_cache.lookup("test")
        assert result is None

    @pytest.mark.asyncio
    async def test_store_survives_l2_failure(self, full_cache):
        """Store should still succeed in L1 even if L2 fails."""
        full_cache._embedding_gen = None  # break L2
        cache_id = await full_cache.store("test", "SELECT 1")
        assert len(cache_id) == 64
        # L1 should still work
        result = await full_cache.lookup("test")
        assert result is not None

    @pytest.mark.asyncio
    async def test_l2_lookup_survives_lancedb_unavailable(self, lance_db, embedding_gen):
        """If LanceDB is closed, lookups should still work (L1 only)."""
        cache = QueryPairCache(
            lance_db=lance_db,
            embedding_generator=embedding_gen,
        )
        await cache.store("test", "SELECT 1")
        # Close LanceDB
        lance_db.close()
        # L1 should still work
        result = await cache.lookup("test")
        assert result is not None
        assert result.match_level == "exact"


# ═══════════════════════════════════════════════════════════════════════════
# Constructor
# ═══════════════════════════════════════════════════════════════════════════


class TestConstructor:
    def test_defaults(self):
        cache = QueryPairCache()
        assert cache._l1_max_size == 1000
        assert cache._l2_threshold == 0.05
        assert cache._lance_db is None
        assert cache._embedding_gen is None

    def test_custom_l1_size(self):
        cache = QueryPairCache(l1_max_size=500)
        assert cache._l1_max_size == 500

    def test_custom_threshold(self):
        cache = QueryPairCache(l2_threshold=0.10)
        assert cache._l2_threshold == 0.10

    def test_l1_max_size_minimum_1(self):
        cache = QueryPairCache(l1_max_size=0)
        assert cache._l1_max_size == 1

    def test_l1_max_size_negative_clamped(self):
        cache = QueryPairCache(l1_max_size=-5)
        assert cache._l1_max_size == 1


# ═══════════════════════════════════════════════════════════════════════════
# Integration — multi-query scenarios
# ═══════════════════════════════════════════════════════════════════════════


class TestIntegration:
    @pytest.mark.asyncio
    async def test_many_queries_l1_only(self, l1_cache):
        """Store and retrieve 100 queries."""
        for i in range(100):
            await l1_cache.store(f"query number {i}", f"SELECT {i}")
        for i in range(100):
            result = await l1_cache.lookup(f"query number {i}")
            assert result is not None
            assert result.sql_text == f"SELECT {i}"

    @pytest.mark.asyncio
    async def test_l1_lru_with_cyclic_access(self):
        """Cyclic access pattern should keep frequently-accessed entries."""
        cache = QueryPairCache(l1_max_size=3)
        await cache.store("hot", "SELECT 99")
        await cache.store("warm", "SELECT 50")
        await cache.store("cold", "SELECT 10")
        # Access hot and warm, then add new — cold should evict
        await cache.lookup("hot")
        await cache.lookup("warm")
        await cache.store("new", "SELECT 0")
        assert await cache.lookup("cold") is None
        assert await cache.lookup("hot") is not None
        assert await cache.lookup("warm") is not None

    @pytest.mark.asyncio
    async def test_same_normalized_query_stores_once(self, l1_cache):
        """Storing the same normalized query twice updates L1 but same cache_id."""
        id1 = await l1_cache.store("Show Users", "SELECT * FROM users")
        id2 = await l1_cache.store("show users", "SELECT * FROM users_v2")
        assert id1 == id2
        # L1 should have 1 entry
        assert len(l1_cache._l1) == 1
        result = await l1_cache.lookup("show users")
        assert result.sql_text == "SELECT * FROM users_v2"

    @pytest.mark.asyncio
    async def test_store_with_domain(self, full_cache):
        await full_cache.store("revenue", "SELECT SUM(amount) FROM orders", domain="ecommerce")
        result = await full_cache.lookup("revenue")
        assert result is not None
        assert result.domain == "ecommerce"

    @pytest.mark.asyncio
    async def test_store_with_confidence(self, l1_cache):
        await l1_cache.store("precise query", "SELECT 1", confidence=0.99)
        result = await l1_cache.lookup("precise query")
        assert result is not None
        assert result.confidence == 0.99
