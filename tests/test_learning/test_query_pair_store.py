"""Tests for QueryPairStore — store, retrieve, search, stats."""

from __future__ import annotations

import os
import tempfile

import pytest

from app.learning.query_pair_store import (
    QueryPairStore,
    _hash_sql,
    _normalize_nl,
    _normalize_sql,
)


@pytest.fixture
def store():
    """Create a QueryPairStore backed by a temporary SQLite database."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    s = QueryPairStore(db_path=path)
    yield s
    s.close()
    os.unlink(path)


# ── helpers ───────────────────────────────────────────────────────────────


class TestNormalizeSQL:
    def test_lowercase(self):
        assert _normalize_sql("SELECT * FROM Users") == "select * from users"

    def test_collapse_whitespace(self):
        assert _normalize_sql("SELECT  1\nFROM  t") == "select 1 from t"

    def test_empty(self):
        assert _normalize_sql("") == ""


class TestNormalizeNL:
    def test_lowercase(self):
        assert _normalize_nl("Hello World") == "hello world"

    def test_collapse_whitespace(self):
        assert _normalize_nl("  查询  订单  ") == "查询 订单"


class TestHashSQL:
    def test_deterministic(self):
        h1 = _hash_sql("SELECT 1")
        h2 = _hash_sql("SELECT 1")
        assert h1 == h2

    def test_different_sql_different_hash(self):
        assert _hash_sql("SELECT 1") != _hash_sql("SELECT 2")

    def test_whitespace_insensitive(self):
        assert _hash_sql("SELECT 1") == _hash_sql("  SELECT  1  ")


# ── store ─────────────────────────────────────────────────────────────────


class TestStore:
    @pytest.mark.asyncio
    async def test_store_returns_id(self, store):
        rid = await store.store(nl="test query", sql="SELECT 1")
        assert len(rid) == 32

    @pytest.mark.asyncio
    async def test_store_with_all_fields(self, store):
        await store.store(
            nl="orders by region",
            sql="SELECT region, COUNT(*) FROM orders GROUP BY region",
            domain="ecommerce",
            rating=5,
            language="en",
            intent="AGGREGATE",
            source="generated",
            edit_count=0,
        )
        data = store.get_training_data(domain="ecommerce")
        assert len(data) == 1
        assert data[0]["nl_original"] == "orders by region"
        assert data[0]["rating"] == 5
        assert data[0]["language"] == "en"

    @pytest.mark.asyncio
    async def test_store_upsert_same_sql(self, store):
        """Same (domain, sql_hash) should update, not duplicate."""
        rid1 = await store.store(nl="q1", sql="SELECT 1", domain="ecom", rating=3)
        rid2 = await store.store(nl="q1 updated", sql="SELECT 1", domain="ecom", rating=5)
        assert rid1 == rid2  # same hash
        assert store.count() == 1

    @pytest.mark.asyncio
    async def test_store_different_domains(self, store):
        await store.store(nl="q", sql="SELECT 1", domain="ecom")
        await store.store(nl="q", sql="SELECT 1", domain="finance")
        assert store.count() == 2

    @pytest.mark.asyncio
    async def test_store_batch(self, store):
        pairs = [
            {"nl": "q1", "sql": "SELECT 1", "domain": "ecom"},
            {"nl": "q2", "sql": "SELECT 2", "domain": "ecom"},
            {"nl": "q3", "sql": "SELECT 3", "domain": "ecom"},
        ]
        ids = await store.store_batch(pairs)
        assert len(ids) == 3
        assert store.count() == 3


# ── retrieval ─────────────────────────────────────────────────────────────


class TestGetTrainingData:
    @pytest.mark.asyncio
    async def test_returns_pairs(self, store):
        await store.store(nl="q1", sql="SELECT 1", rating=4)
        await store.store(nl="q2", sql="SELECT 2", rating=3)
        data = store.get_training_data()
        assert len(data) == 2

    @pytest.mark.asyncio
    async def test_domain_filter(self, store):
        await store.store(nl="q1", sql="SELECT 1", domain="ecom")
        await store.store(nl="q2", sql="SELECT 2", domain="fin")
        data = store.get_training_data(domain="ecom")
        assert len(data) == 1

    @pytest.mark.asyncio
    async def test_min_rating_filter(self, store):
        await store.store(nl="q1", sql="SELECT 1", rating=2)
        await store.store(nl="q2", sql="SELECT 2", rating=5)
        data = store.get_training_data(min_rating=4)
        assert len(data) == 1
        assert data[0]["rating"] == 5

    @pytest.mark.asyncio
    async def test_limit(self, store):
        for i in range(10):
            await store.store(nl=f"q{i}", sql=f"SELECT {i}")
        data = store.get_training_data(limit=5)
        assert len(data) == 5


class TestGetEmbeddingPairs:
    @pytest.mark.asyncio
    async def test_no_embeddings_returns_empty(self, store):
        await store.store(nl="q1", sql="SELECT 1")
        pairs = store.get_embedding_pairs()
        assert pairs == []  # no embedding_gen configured


class TestSearchSimilar:
    @pytest.mark.asyncio
    async def test_finds_similar(self, store):
        await store.store(
            nl="get total sales by region",
            sql="SELECT region, SUM(sales) FROM orders GROUP BY region",
        )
        await store.store(nl="count active users", sql="SELECT COUNT(*) FROM users WHERE active=1")
        results = store.search_similar("sales total by region", threshold=0.3)
        assert len(results) >= 1

    @pytest.mark.asyncio
    async def test_empty_query(self, store):
        await store.store(nl="test", sql="SELECT 1")
        assert store.search_similar("") == []

    @pytest.mark.asyncio
    async def test_threshold_filters(self, store):
        await store.store(nl="订单查询", sql="SELECT * FROM orders")
        results = store.search_similar("完全不相关的文本xyz", threshold=0.9)
        assert results == []


# ── stats ─────────────────────────────────────────────────────────────────


class TestStats:
    def test_empty(self, store):
        s = store.stats()
        assert s["total"] == 0

    @pytest.mark.asyncio
    async def test_with_data(self, store):
        await store.store(nl="q1", sql="SELECT 1", rating=4, source="generated", domain="ecom")
        await store.store(nl="q2", sql="SELECT 2", rating=2, source="human_written", domain="ecom")
        s = store.stats()
        assert s["total"] == 2
        assert s["avg_rating"] == 3.0
        assert "generated" in s["by_source"]
        assert "ecom" in s["by_domain"]

    def test_domain_filtered(self, store):
        s = store.stats(domain="ecom")
        assert s["domain_id"] == "ecom"


class TestCount:
    @pytest.mark.asyncio
    async def test_count(self, store):
        await store.store(nl="q1", sql="SELECT 1")
        await store.store(nl="q2", sql="SELECT 2")
        assert store.count() == 2

    @pytest.mark.asyncio
    async def test_count_domain_filtered(self, store):
        await store.store(nl="q1", sql="SELECT 1", domain="ecom")
        await store.store(nl="q2", sql="SELECT 2", domain="fin")
        assert store.count(domain="ecom") == 1
