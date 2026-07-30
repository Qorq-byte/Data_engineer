"""Tests for EvolvableContext — ingestion, retrieval, lifecycle, and stats.

Covers all 4 knowledge types, the active→stale→archived lifecycle,
keyword-based retrieval scoring, and bulk operations.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.knowledge.evolvable_context import (
    EvolvableContext,
    KnowledgeItem,
    KnowledgeType,
    _generate_id,
    _keyword_match_score,
    _tokenize,
)

# ── Helpers ────────────────────────────────────────────────────────────


def _make_item(
    type: KnowledgeType = KnowledgeType.SCHEMA,
    content: dict | None = None,
    source: str = "test",
    domain_id: str = "",
    score: float = 0.5,
    status: str = "active",
    tags: list[str] | None = None,
) -> KnowledgeItem:
    return KnowledgeItem(
        type=type,
        content=content or {"test": True},
        source=source,
        domain_id=domain_id,
        score=score,
        status=status,
        tags=tags or [],
    )


# ── KnowledgeType tests ────────────────────────────────────────────────


class TestKnowledgeType:
    def test_values(self):
        assert KnowledgeType.SCHEMA.value == "SCHEMA"
        assert KnowledgeType.REFERENCE_SQL.value == "REFERENCE_SQL"
        assert KnowledgeType.SEMANTIC_MODEL.value == "SEMANTIC_MODEL"
        assert KnowledgeType.METRIC.value == "METRIC"

    def test_is_str_enum(self):
        assert isinstance(KnowledgeType.SCHEMA, str)
        assert KnowledgeType.SCHEMA == "SCHEMA"


# ── KnowledgeItem tests ────────────────────────────────────────────────


class TestKnowledgeItem:
    def test_construction_defaults(self):
        item = KnowledgeItem(type=KnowledgeType.SCHEMA)
        assert item.id  # auto-generated
        assert len(item.id) == 8
        assert item.type == KnowledgeType.SCHEMA
        assert item.content == {}
        assert item.source == ""
        assert item.version == 1
        assert item.score == 0.5
        assert item.status == "active"
        assert item.domain_id == ""
        assert item.tags == []

    def test_construction_full(self):
        now = datetime.now(UTC)
        item = KnowledgeItem(
            type=KnowledgeType.REFERENCE_SQL,
            content={"nl": "test", "sql": "SELECT 1"},
            source="user_confirm",
            id="my_id_123",
            version=3,
            created_at=now,
            score=0.9,
            status="stale",
            domain_id="ecommerce",
            tags=["sql", "verified"],
        )
        assert item.id == "my_id_123"
        assert item.type == KnowledgeType.REFERENCE_SQL
        assert item.content["nl"] == "test"
        assert item.version == 3
        assert item.score == 0.9
        assert item.status == "stale"
        assert item.domain_id == "ecommerce"
        assert item.tags == ["sql", "verified"]

    def test_timestamps_set_on_creation(self):
        item = KnowledgeItem(type=KnowledgeType.SCHEMA)
        assert isinstance(item.created_at, datetime)
        assert isinstance(item.updated_at, datetime)
        assert item.last_accessed_at is None

    def test_touch_updates_last_accessed(self):
        item = KnowledgeItem(type=KnowledgeType.SCHEMA)
        assert item.last_accessed_at is None
        item.touch()
        assert isinstance(item.last_accessed_at, datetime)

    def test_repr(self):
        item = KnowledgeItem(
            type=KnowledgeType.SCHEMA,
            domain_id="ecommerce",
            score=0.8,
            id="abc12345",
        )
        r = repr(item)
        assert "abc12345" in r
        assert "SCHEMA" in r
        assert "ecommerce" in r

    def test_unique_ids(self):
        """Each item gets a unique auto-generated ID."""
        ids = {KnowledgeItem(type=KnowledgeType.SCHEMA).id for _ in range(100)}
        assert len(ids) == 100


# ── Helpers tests ──────────────────────────────────────────────────────


class TestTokenize:
    def test_simple(self):
        assert _tokenize("hello world") == ["hello", "world"]

    def test_chinese(self):
        tokens = _tokenize("查询订单金额")
        assert "查询" in tokens or "订单金额" in tokens or len(tokens) >= 1

    def test_short_tokens_filtered(self):
        tokens = _tokenize("a b c ab cd ef xyz")
        for t in tokens:
            assert len(t) >= 2

    def test_empty(self):
        assert _tokenize("") == []


class TestKeywordMatchScore:
    def test_exact_word_match(self):
        item = _make_item(
            type=KnowledgeType.REFERENCE_SQL,
            content={"nl": "查询订单金额", "sql": "SELECT amount FROM orders"},
        )
        score = _keyword_match_score(item, "订单金额")
        assert score > 0.0

    def test_tag_match(self):
        item = _make_item(tags=["sql", "user_confirmed"])
        score = _keyword_match_score(item, "sql query")
        assert score > 0.0

    def test_no_match(self):
        item = _make_item(content={"nl": "hello"})
        score = _keyword_match_score(item, "xyzabc")
        assert score == 0.0

    def test_empty_query(self):
        item = _make_item(content={"nl": "test"})
        assert _keyword_match_score(item, "") == 0.0

    def test_list_values_searched(self):
        item = _make_item(content={"tables": ["orders", "users", "products"]})
        score = _keyword_match_score(item, "orders users")
        assert score >= 0.5


# ── Ingestion tests ────────────────────────────────────────────────────


class TestEvolvableContextIngestion:
    @pytest.fixture
    def ctx(self) -> EvolvableContext:
        return EvolvableContext()

    def test_ingest_new_item(self, ctx: EvolvableContext):
        item = _make_item(type=KnowledgeType.SCHEMA)
        result = ctx.ingest(item)
        assert result is item
        assert ctx.item_count == 1
        assert item.id in ctx

    def test_ingest_duplicate_updates(self, ctx: EvolvableContext):
        item1 = _make_item(type=KnowledgeType.SCHEMA, content={"v": 1})
        ctx.ingest(item1)

        item2 = KnowledgeItem(
            type=KnowledgeType.SCHEMA,
            content={"v": 2},
            id=item1.id,  # same ID
            score=0.9,
        )
        result = ctx.ingest(item2)
        assert result is item1  # returned the existing item
        assert result.version == 2  # bumped
        assert result.score == 0.9  # updated
        assert result.content["v"] == 2  # merged

    def test_ingest_duplicate_merges_tags(self, ctx: EvolvableContext):
        item1 = _make_item(tags=["a", "b"])
        ctx.ingest(item1)
        item2 = KnowledgeItem(
            type=KnowledgeType.SCHEMA,
            content={"test": True},
            id=item1.id,
            tags=["b", "c"],
        )
        result = ctx.ingest(item2)
        assert set(result.tags) == {"a", "b", "c"}

    def test_ingest_schema(self, ctx: EvolvableContext):
        item = ctx.ingest_schema(
            tables=["orders", "users"],
            columns={"orders": ["id", "amount"], "users": ["id", "name"]},
            domain="ecommerce",
            version=2,
        )
        assert item.type == KnowledgeType.SCHEMA
        assert item.content["tables"] == ["orders", "users"]
        assert item.content["columns"]["orders"] == ["id", "amount"]
        assert item.content["schema_version"] == 2
        assert item.domain_id == "ecommerce"
        assert item.source == "schema_sync"
        assert item.score == 0.8
        assert "schema" in item.tags

    def test_ingest_schema_defaults(self, ctx: EvolvableContext):
        item = ctx.ingest_schema()
        assert item.type == KnowledgeType.SCHEMA
        assert item.content["tables"] == []
        assert item.content["columns"] == {}
        assert item.content["schema_version"] == 1

    def test_ingest_schema_with_extra(self, ctx: EvolvableContext):
        item = ctx.ingest_schema(
            extra={"database_type": "postgresql", "row_count": 10000},
        )
        assert item.content["database_type"] == "postgresql"
        assert item.content["row_count"] == 10000

    def test_ingest_reference_sql(self, ctx: EvolvableContext):
        item = ctx.ingest_reference_sql(
            nl="查询订单金额",
            sql="SELECT SUM(amount) FROM orders",
            confidence=0.95,
            domain="ecommerce",
            tables_used=["orders"],
            intent="AGGREGATE",
        )
        assert item.type == KnowledgeType.REFERENCE_SQL
        assert item.content["nl"] == "查询订单金额"
        assert item.content["sql"] == "SELECT SUM(amount) FROM orders"
        assert item.content["confidence"] == 0.95
        assert item.content["tables_used"] == ["orders"]
        assert item.content["intent"] == "AGGREGATE"
        assert item.score == 0.95  # score = confidence
        assert "user_confirmed" in item.tags

    def test_ingest_reference_sql_defaults(self, ctx: EvolvableContext):
        item = ctx.ingest_reference_sql(nl="test", sql="SELECT 1")
        assert item.score == 0.5
        assert item.content["tables_used"] == []
        assert item.content["intent"] == ""

    def test_ingest_semantic_model(self, ctx: EvolvableContext):
        item = ctx.ingest_semantic_model(
            term="订单金额",
            mapping={"expression": "SUM(orders.amount)", "type": "derived_column"},
            domain="ecommerce",
            description="订单总金额",
        )
        assert item.type == KnowledgeType.SEMANTIC_MODEL
        assert item.content["term"] == "订单金额"
        assert item.content["mapping"]["type"] == "derived_column"
        assert item.content["description"] == "订单总金额"
        assert item.score == 0.7
        assert "term_mapping" in item.tags

    def test_ingest_metric(self, ctx: EvolvableContext):
        item = ctx.ingest_metric(
            name="revenue",
            formula="SUM(amount)",
            dimensions=["date", "region"],
            domain="ecommerce",
            time_grain="day",
            aggregation="SUM",
            description="总营收",
        )
        assert item.type == KnowledgeType.METRIC
        assert item.content["name"] == "revenue"
        assert item.content["formula"] == "SUM(amount)"
        assert item.content["dimensions"] == ["date", "region"]
        assert item.content["time_grain"] == "day"
        assert item.content["aggregation"] == "SUM"
        assert item.score == 0.7
        assert "kpi" in item.tags


# ── Retrieval tests ────────────────────────────────────────────────────


class TestEvolvableContextRetrieval:
    @pytest.fixture
    def ctx(self) -> EvolvableContext:
        c = EvolvableContext()
        c.ingest_schema(tables=["orders", "users"], domain="ecommerce")
        c.ingest_reference_sql(
            nl="订单金额查询", sql="SELECT SUM(amount) FROM orders",
            confidence=0.9, domain="ecommerce",
        )
        c.ingest_reference_sql(
            nl="用户活跃度", sql="SELECT COUNT(*) FROM users",
            confidence=0.8, domain="ecommerce",
        )
        c.ingest_metric(
            name="revenue", formula="SUM(amount)",
            dimensions=["date"], domain="ecommerce",
        )
        c.ingest_semantic_model(
            term="客单价", mapping={"expression": "AVG(amount)"}, domain="ecommerce",
        )
        # Finance domain items
        c.ingest_metric(
            name="NAV", formula="SUM(assets) - SUM(liabilities)",
            domain="finance",
        )
        return c

    def test_get_existing(self, ctx: EvolvableContext):
        # Find an item by listing and getting its ID
        items = ctx.list_all()
        assert len(items) > 0
        item_id = items[0].id
        retrieved = ctx.get(item_id)
        assert retrieved is not None
        assert retrieved.id == item_id
        assert retrieved.last_accessed_at is not None  # touched

    def test_get_missing(self, ctx: EvolvableContext):
        assert ctx.get("nonexistent") is None

    def test_list_all(self, ctx: EvolvableContext):
        items = ctx.list_all()
        assert len(items) == 6

    def test_list_all_filter_by_type(self, ctx: EvolvableContext):
        items = ctx.list_all(type=KnowledgeType.REFERENCE_SQL)
        assert len(items) == 2
        for item in items:
            assert item.type == KnowledgeType.REFERENCE_SQL

    def test_list_all_filter_by_domain(self, ctx: EvolvableContext):
        items = ctx.list_all(domain="finance")
        assert len(items) == 1
        assert items[0].domain_id == "finance"

    def test_list_all_filter_by_status(self, ctx: EvolvableContext):
        items = ctx.list_all(status="active")
        assert len(items) == 6

    def test_list_all_excludes_archived(self, ctx: EvolvableContext):
        items = ctx.list_all()
        ctx.archive(items[0].id)
        active = ctx.list_all(status="active")
        archived = ctx.list_all(status="archived")
        assert len(active) == 5
        assert len(archived) == 1

    def test_list_all_sorted_by_score(self, ctx: EvolvableContext):
        items = ctx.list_all()
        scores = [it.score for it in items]
        assert scores == sorted(scores, reverse=True)

    def test_retrieve_with_query(self, ctx: EvolvableContext):
        results = ctx.retrieve(query="订单金额", domain="ecommerce")
        assert len(results) >= 1
        # The REFERENCE_SQL item with "订单金额查询" should rank high
        for item in results:
            assert item.last_accessed_at is not None  # touched

    def test_retrieve_no_query(self, ctx: EvolvableContext):
        results = ctx.retrieve(query="", top_k=10)
        assert len(results) == 6  # all active items

    def test_retrieve_top_k(self, ctx: EvolvableContext):
        results = ctx.retrieve(query="", top_k=2)
        assert len(results) == 2

    def test_retrieve_domain_filter(self, ctx: EvolvableContext):
        results = ctx.retrieve(query="", domain="finance")
        assert len(results) == 1
        assert results[0].domain_id == "finance"

    def test_retrieve_type_filter(self, ctx: EvolvableContext):
        results = ctx.retrieve(query="", type=KnowledgeType.METRIC, top_k=10)
        assert len(results) == 2
        for item in results:
            assert item.type == KnowledgeType.METRIC

    def test_retrieve_excludes_archived(self, ctx: EvolvableContext):
        items = ctx.list_all()
        ctx.archive(items[0].id)
        results = ctx.retrieve(query="", top_k=10)
        assert len(results) == 5  # 6 - 1 archived

    def test_retrieve_empty_result(self, ctx: EvolvableContext):
        results = ctx.retrieve(query="xyzabc_nonexistent_term_12345")
        assert results == []


# ── Lifecycle tests ────────────────────────────────────────────────────


class TestEvolvableContextLifecycle:
    @pytest.fixture
    def ctx(self) -> EvolvableContext:
        c = EvolvableContext()
        item = c.ingest_schema(tables=["orders"], domain="ecommerce")
        self._item_id = item.id
        return c

    def test_update(self, ctx: EvolvableContext):
        assert ctx.update(self._item_id, {"score": 0.99, "source": "updated"})
        item = ctx.get(self._item_id)
        assert item.score == 0.99
        assert item.source == "updated"
        assert item.version == 2

    def test_update_nonexistent(self, ctx: EvolvableContext):
        assert ctx.update("nonexistent", {"score": 0.5}) is False

    def test_mark_stale(self, ctx: EvolvableContext):
        assert ctx.mark_stale(self._item_id)
        item = ctx.get(self._item_id)
        assert item.status == "stale"
        assert item.version == 2

    def test_archive(self, ctx: EvolvableContext):
        assert ctx.archive(self._item_id)
        item = ctx.get(self._item_id)
        assert item.status == "archived"

    def test_reactivate(self, ctx: EvolvableContext):
        ctx.archive(self._item_id)
        assert ctx.reactivate(self._item_id)
        item = ctx.get(self._item_id)
        assert item.status == "active"
        assert item.score == 0.6  # reset on reactivation

    def test_purge_archived(self, ctx: EvolvableContext):
        ctx.archive(self._item_id)
        assert ctx.purge_archived() == 1
        assert ctx.get(self._item_id) is None
        assert ctx.item_count == 0

    def test_purge_archived_with_before(self, ctx: EvolvableContext):
        ctx.archive(self._item_id)
        # Item was archived just now — purge before an hour ago should keep it
        past = datetime.now(UTC) - timedelta(hours=1)
        assert ctx.purge_archived(before=past) == 0
        assert ctx.item_count == 1

    def test_purge_archived_before_future(self, ctx: EvolvableContext):
        ctx.archive(self._item_id)
        future = datetime.now(UTC) + timedelta(hours=1)
        assert ctx.purge_archived(before=future) == 1
        assert ctx.item_count == 0

    def test_purge_only_removes_archived(self, ctx: EvolvableContext):
        # Mark as stale (not archived) — purge should not remove it
        ctx.mark_stale(self._item_id)
        assert ctx.purge_archived() == 0
        assert ctx.item_count == 1

    def test_full_lifecycle_roundtrip(self, ctx: EvolvableContext):
        """active → stale → archived → reactivate → active"""
        item_id = self._item_id

        # active → stale
        ctx.mark_stale(item_id)
        assert ctx.get(item_id).status == "stale"

        # stale → archived
        ctx.archive(item_id)
        assert ctx.get(item_id).status == "archived"

        # archived → active (reactivate)
        ctx.reactivate(item_id)
        assert ctx.get(item_id).status == "active"


# ── Bulk operations tests ──────────────────────────────────────────────


class TestEvolvableContextBulk:
    @pytest.fixture
    def ctx(self) -> EvolvableContext:
        c = EvolvableContext()
        c.ingest_schema(tables=["orders"], domain="ecommerce")
        c.ingest_schema(tables=["accounts"], domain="finance")
        c.ingest_reference_sql(nl="test", sql="SELECT 1", domain="ecommerce")
        return c

    def test_clear_all(self, ctx: EvolvableContext):
        assert ctx.clear() == 3
        assert ctx.item_count == 0

    def test_clear_by_type(self, ctx: EvolvableContext):
        removed = ctx.clear(type=KnowledgeType.SCHEMA)
        assert removed == 2
        assert ctx.item_count == 1
        assert ctx.list_all()[0].type == KnowledgeType.REFERENCE_SQL

    def test_clear_by_domain(self, ctx: EvolvableContext):
        removed = ctx.clear(domain="ecommerce")
        assert removed == 2
        assert ctx.item_count == 1
        assert ctx.list_all()[0].domain_id == "finance"

    def test_clear_by_type_and_domain(self, ctx: EvolvableContext):
        removed = ctx.clear(type=KnowledgeType.SCHEMA, domain="finance")
        assert removed == 1
        assert ctx.item_count == 2


# ── Stats tests ────────────────────────────────────────────────────────


class TestEvolvableContextStats:
    def test_empty_stats(self):
        ctx = EvolvableContext()
        s = ctx.stats()
        assert s["total"] == 0
        assert s["by_type"] == {}
        assert s["by_status"] == {}
        assert s["by_domain"] == {}
        assert s["avg_score"] == 0.0
        assert s["oldest"] is None
        assert s["newest"] is None

    def test_populated_stats(self):
        ctx = EvolvableContext()
        ctx.ingest_schema(tables=["t1"], domain="ecommerce")
        ctx.ingest_reference_sql(nl="q1", sql="SELECT 1", domain="ecommerce")
        ctx.ingest_metric(name="m1", domain="finance")

        s = ctx.stats()
        assert s["total"] == 3
        assert s["by_type"] == {"METRIC": 1, "REFERENCE_SQL": 1, "SCHEMA": 1}
        assert s["by_status"] == {"active": 3}
        assert s["by_domain"] == {"ecommerce": 2, "finance": 1}
        assert 0.0 < s["avg_score"] < 1.0
        assert isinstance(s["oldest"], datetime)
        assert isinstance(s["newest"], datetime)
        assert s["newest"] >= s["oldest"]

    def test_stats_reflects_status_changes(self):
        ctx = EvolvableContext()
        item = ctx.ingest_schema(tables=["t1"])
        ctx.archive(item.id)

        s = ctx.stats()
        assert s["by_status"] == {"archived": 1}


# ── Container protocol tests ───────────────────────────────────────────


class TestEvolvableContextContainer:
    def test_len(self):
        ctx = EvolvableContext()
        assert len(ctx) == 0
        ctx.ingest_schema()
        assert len(ctx) == 1

    def test_contains(self):
        ctx = EvolvableContext()
        item = ctx.ingest_schema()
        assert item.id in ctx
        assert "nonexistent" not in ctx

    def test_iter(self):
        ctx = EvolvableContext()
        ctx.ingest_schema()
        ctx.ingest_metric(name="test")
        items = list(ctx)
        assert len(items) == 2
        for item in items:
            assert isinstance(item, KnowledgeItem)


# ── Properties tests ───────────────────────────────────────────────────


class TestEvolvableContextProperties:
    def test_item_count(self):
        ctx = EvolvableContext()
        assert ctx.item_count == 0
        ctx.ingest_schema()
        assert ctx.item_count == 1

    def test_type_counts(self):
        ctx = EvolvableContext()
        ctx.ingest_schema()
        ctx.ingest_schema()
        ctx.ingest_metric(name="m1")
        tc = ctx.type_counts
        assert tc["SCHEMA"] == 2
        assert tc["METRIC"] == 1

    def test_domain_counts(self):
        ctx = EvolvableContext()
        ctx.ingest_schema(domain="ecommerce")
        ctx.ingest_schema(domain="ecommerce")
        ctx.ingest_metric(name="m1", domain="finance")
        dc = ctx.domain_counts
        assert dc["ecommerce"] == 2
        assert dc["finance"] == 1

    def test_domain_counts_global(self):
        ctx = EvolvableContext()
        ctx.ingest_schema()  # no domain
        dc = ctx.domain_counts
        assert dc["__global__"] == 1


# ── Edge cases ─────────────────────────────────────────────────────────


class TestEvolvableContextEdgeCases:
    def test_duplicate_ingest_preserves_created_at(self):
        ctx = EvolvableContext()
        item1 = _make_item(type=KnowledgeType.SCHEMA)
        ctx.ingest(item1)
        original_created = item1.created_at

        item2 = KnowledgeItem(
            type=KnowledgeType.SCHEMA,
            content={"updated": True},
            id=item1.id,
        )
        result = ctx.ingest(item2)
        assert result.created_at == original_created

    def test_retrieve_touches_accessed_items(self):
        ctx = EvolvableContext()
        item = ctx.ingest_reference_sql(nl="订单查询", sql="SELECT 1")
        assert item.last_accessed_at is None
        ctx.retrieve(query="订单")
        assert item.last_accessed_at is not None

    def test_generate_id_length(self):
        id_ = _generate_id()
        assert len(id_) == 8
        assert all(c in "0123456789abcdef" for c in id_)

    def test_multiple_ingest_of_same_id(self):
        ctx = EvolvableContext()
        item = ctx.ingest_schema()
        vid = item.id
        for _ in range(5):
            dup = KnowledgeItem(type=KnowledgeType.SCHEMA, content={}, id=vid)
            ctx.ingest(dup)
        updated = ctx.get(vid)
        assert updated.version == 6  # original + 5 updates

    def test_keyword_score_with_nested_content(self):
        """Content with nested dict values should not crash."""
        item = _make_item(
            type=KnowledgeType.SCHEMA,
            content={"nested": {"key": "value"}, "tables": ["orders"]},
        )
        score = _keyword_match_score(item, "orders")
        assert score >= 0.0

    def test_tokenize_mixed_language(self):
        tokens = _tokenize("查询 orders 金额")
        assert len(tokens) >= 2
