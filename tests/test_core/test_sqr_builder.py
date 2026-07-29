"""Unit tests for SQRBuilder — limit/order/table/entity extraction and SQR assembly."""

import pytest

from app.core.sqr_builder import SQRBuilder
from app.models.query import Ambiguity, IntentType, TimeRange


@pytest.fixture
def builder():
    return SQRBuilder()


# ═══════════════════════════════════════════════════════════════════════════
# Limit Extraction — Chinese
# ═══════════════════════════════════════════════════════════════════════════


class TestLimitExtractionZh:
    def test_top_n_ming(self, builder):
        r = builder.build("前10名产品", "zh", IntentType.SELECT)
        assert r.limit == 10

    def test_top_n_ge(self, builder):
        r = builder.build("查询前5个订单", "zh", IntentType.SELECT)
        assert r.limit == 5

    def test_top_n_tiao(self, builder):
        r = builder.build("前20条记录", "zh", IntentType.SELECT)
        assert r.limit == 20

    def test_top_n_no_unit(self, builder):
        r = builder.build("销售额前3的产品", "zh", IntentType.SELECT)
        assert r.limit == 3

    def test_qu_n_tiao(self, builder):
        r = builder.build("取10条数据", "zh", IntentType.SELECT)
        assert r.limit == 10

    def test_zhiyao_n(self, builder):
        r = builder.build("只要前5个用户", "zh", IntentType.SELECT)
        assert r.limit == 5

    def test_xianzhi(self, builder):
        r = builder.build("限制50条结果", "zh", IntentType.SELECT)
        assert r.limit == 50

    def test_xianliang(self, builder):
        r = builder.build("限量100个", "zh", IntentType.SELECT)
        assert r.limit == 100

    def test_top_n_da(self, builder):
        r = builder.build("前10大客户", "zh", IntentType.SELECT)
        assert r.limit == 10

    def test_no_limit(self, builder):
        r = builder.build("查询所有订单", "zh", IntentType.SELECT)
        assert r.limit is None


# ═══════════════════════════════════════════════════════════════════════════
# Limit Extraction — English
# ═══════════════════════════════════════════════════════════════════════════


class TestLimitExtractionEn:
    def test_top_n(self, builder):
        r = builder.build("top 10 products", "en", IntentType.SELECT)
        assert r.limit == 10

    def test_first_n(self, builder):
        r = builder.build("first 5 results", "en", IntentType.SELECT)
        assert r.limit == 5

    def test_limit_n(self, builder):
        r = builder.build("orders limit 20", "en", IntentType.SELECT)
        assert r.limit == 20

    def test_n_results(self, builder):
        r = builder.build("show 15 records", "en", IntentType.SELECT)
        assert r.limit == 15

    def test_n_rows(self, builder):
        r = builder.build("get 25 rows", "en", IntentType.SELECT)
        assert r.limit == 25

    def test_n_items(self, builder):
        r = builder.build("list 30 items", "en", IntentType.SELECT)
        assert r.limit == 30

    def test_top_uppercase(self, builder):
        r = builder.build("TOP 50 customers", "en", IntentType.SELECT)
        assert r.limit == 50

    def test_no_limit(self, builder):
        r = builder.build("find all orders", "en", IntentType.SELECT)
        assert r.limit is None


# ═══════════════════════════════════════════════════════════════════════════
# Limit — multiple matches
# ═══════════════════════════════════════════════════════════════════════════


class TestLimitMultipleMatches:
    def test_returns_smallest_limit(self, builder):
        """When multiple limit patterns match, return the smallest (most conservative)."""
        r = builder.build("取前10名 top 5 products", "mixed", IntentType.SELECT)
        assert r.limit == 5

    def test_top_and_limit(self, builder):
        r = builder.build("top 20 limit 10", "en", IntentType.SELECT)
        assert r.limit == 10


# ═══════════════════════════════════════════════════════════════════════════
# Order Extraction — Chinese
# ═══════════════════════════════════════════════════════════════════════════


class TestOrderExtractionZh:
    def test_asc_explicit(self, builder):
        r = builder.build("按价格升序排列", "zh", IntentType.SELECT)
        assert len(r.order_by) == 1
        assert r.order_by[0].column == "价格"
        assert r.order_by[0].direction == "ASC"

    def test_desc_explicit(self, builder):
        r = builder.build("按金额降序", "zh", IntentType.SELECT)
        assert len(r.order_by) == 1
        assert r.order_by[0].column == "金额"
        assert r.order_by[0].direction == "DESC"

    def test_sort_no_direction(self, builder):
        r = builder.build("按销量排序", "zh", IntentType.SELECT)
        assert len(r.order_by) == 1
        assert r.order_by[0].column == "销量"
        assert r.order_by[0].direction == "ASC"  # default

    def test_dizeng(self, builder):
        r = builder.build("按日期递增", "zh", IntentType.SELECT)
        assert r.order_by[0].direction == "ASC"

    def test_dijian(self, builder):
        r = builder.build("按分数递减", "zh", IntentType.SELECT)
        assert r.order_by[0].direction == "DESC"

    def test_cong_da_dao_xiao(self, builder):
        r = builder.build("按销售额从大到小", "zh", IntentType.SELECT)
        assert r.order_by[0].direction == "DESC"

    def test_cong_di_dao_gao(self, builder):
        r = builder.build("按成本从低到高", "zh", IntentType.SELECT)
        assert r.order_by[0].direction == "ASC"

    def test_superlative_highest(self, builder):
        r = builder.build("价格最高的商品", "zh", IntentType.SELECT)
        assert len(r.order_by) == 1
        assert r.order_by[0].direction == "DESC"

    def test_superlative_lowest(self, builder):
        r = builder.build("价格最低的商品", "zh", IntentType.SELECT)
        assert len(r.order_by) == 1
        assert r.order_by[0].direction == "ASC"

    def test_superlative_most(self, builder):
        r = builder.build("销量最多的商品", "zh", IntentType.SELECT)
        assert r.order_by[0].direction == "DESC"

    def test_no_order(self, builder):
        r = builder.build("查询所有订单", "zh", IntentType.SELECT)
        assert r.order_by == []

    def test_multiple_orders(self, builder):
        r = builder.build("按金额降序按日期升序", "zh", IntentType.SELECT)
        assert len(r.order_by) == 2


# ═══════════════════════════════════════════════════════════════════════════
# Order Extraction — English
# ═══════════════════════════════════════════════════════════════════════════


class TestOrderExtractionEn:
    def test_order_by_asc(self, builder):
        r = builder.build("order by price asc", "en", IntentType.SELECT)
        assert r.order_by[0].column == "price"
        assert r.order_by[0].direction == "ASC"

    def test_order_by_desc(self, builder):
        r = builder.build("sort by revenue desc", "en", IntentType.SELECT)
        assert r.order_by[0].column == "revenue"
        assert r.order_by[0].direction == "DESC"

    def test_order_by_no_direction(self, builder):
        r = builder.build("order by name", "en", IntentType.SELECT)
        assert r.order_by[0].column == "name"
        assert r.order_by[0].direction == "ASC"

    def test_sort_by_ascending(self, builder):
        r = builder.build("sort by date ascending", "en", IntentType.SELECT)
        assert r.order_by[0].direction == "ASC"

    def test_sort_by_descending(self, builder):
        r = builder.build("sort by score descending", "en", IntentType.SELECT)
        assert r.order_by[0].direction == "DESC"

    def test_highest(self, builder):
        r = builder.build("highest revenue", "en", IntentType.SELECT)
        assert any(o.direction == "DESC" for o in r.order_by)

    def test_lowest(self, builder):
        r = builder.build("lowest price", "en", IntentType.SELECT)
        assert any(o.direction == "ASC" for o in r.order_by)

    def test_most(self, builder):
        r = builder.build("most popular products", "en", IntentType.SELECT)
        assert any(o.direction == "DESC" for o in r.order_by)

    def test_least(self, builder):
        r = builder.build("least expensive items", "en", IntentType.SELECT)
        assert any(o.direction == "ASC" for o in r.order_by)

    def test_no_order(self, builder):
        r = builder.build("find all orders", "en", IntentType.SELECT)
        assert r.order_by == []


# ═══════════════════════════════════════════════════════════════════════════
# Order — dedup
# ═══════════════════════════════════════════════════════════════════════════


class TestOrderDedup:
    def test_same_column_dedup(self, builder):
        """Multiple patterns matching same column should deduplicate."""
        r = builder.build("按金额降序按金额排序", "zh", IntentType.SELECT)
        assert len(r.order_by) == 1
        # The explicit direction should win
        assert r.order_by[0].direction == "DESC"


# ═══════════════════════════════════════════════════════════════════════════
# Table Extraction
# ═══════════════════════════════════════════════════════════════════════════


class TestTableExtraction:
    def test_zh_table_suffix(self, builder):
        r = builder.build("查询订单表", "zh", IntentType.SELECT)
        assert "订单" in r.target_tables

    def test_zh_cong_table(self, builder):
        r = builder.build("从用户表中查询", "zh", IntentType.SELECT)
        assert "用户" in r.target_tables

    def test_zh_cong_table_li(self, builder):
        r = builder.build("从商品表里获取数据", "zh", IntentType.SELECT)
        assert "商品" in r.target_tables

    def test_en_from_table(self, builder):
        r = builder.build("select from orders", "en", IntentType.SELECT)
        assert "orders" in r.target_tables

    def test_en_the_table(self, builder):
        r = builder.build("from the users table", "en", IntentType.SELECT)
        assert "users" in r.target_tables

    def test_no_table(self, builder):
        r = builder.build("查询所有数据", "zh", IntentType.SELECT)
        assert r.target_tables == []


# ═══════════════════════════════════════════════════════════════════════════
# Entity Extraction
# ═══════════════════════════════════════════════════════════════════════════


class TestEntityExtraction:
    def test_quoted_string(self, builder):
        r = builder.build('查找名称为"苹果"的商品', "zh", IntentType.SELECT)
        assert any(e.name == "苹果" for e in r.entities)

    def test_single_quoted(self, builder):
        r = builder.build("find product 'iPhone 15'", "en", IntentType.SELECT)
        assert any(e.name == "iPhone 15" for e in r.entities)

    def test_french_quotes(self, builder):
        r = builder.build("查找产品「MacBook Pro」", "zh", IntentType.SELECT)
        assert any(e.name == "MacBook Pro" for e in r.entities)

    def test_no_entity(self, builder):
        r = builder.build("查询所有订单", "zh", IntentType.SELECT)
        assert r.entities == []


# ═══════════════════════════════════════════════════════════════════════════
# Confidence Scoring
# ═══════════════════════════════════════════════════════════════════════════


class TestConfidenceScoring:
    def test_high_confidence(self, builder):
        """Clean query with language, intent, and time range → high confidence."""
        r = builder.build(
            "上个月的订单",
            "zh",
            IntentType.SELECT,
            time_range=TimeRange(),
        )
        assert r.confidence >= 0.8

    def test_medium_confidence(self, builder):
        """Mixed language, unknown intent → lower confidence."""
        r = builder.build("some query", "mixed", IntentType.UNKNOWN)
        assert 0.4 <= r.confidence <= 0.6

    def test_confidence_reduced_by_ambiguities(self, builder):
        r1 = builder.build("查询订单", "zh", IntentType.SELECT)
        r2 = builder.build(
            "查询订单", "zh", IntentType.SELECT,
            ambiguities=[Ambiguity(aspect="temporal", description="test")],
        )
        assert r2.confidence < r1.confidence

    def test_confidence_clamped_to_zero(self, builder):
        """Many ambiguities should not drive confidence below 0."""
        many_ambiguities = [Ambiguity(aspect="range", description=f"test {i}") for i in range(30)]
        r = builder.build("test", "mixed", IntentType.UNKNOWN, ambiguities=many_ambiguities)
        assert r.confidence >= 0.0

    def test_confidence_clamped_to_one(self, builder):
        """Confidence should not exceed 1.0."""
        r = builder.build(
            "上个月的订单",
            "zh",
            IntentType.TIME_SERIES,
            time_range=TimeRange(),
        )
        assert r.confidence <= 1.0


# ═══════════════════════════════════════════════════════════════════════════
# Full SQR Assembly
# ═══════════════════════════════════════════════════════════════════════════


class TestFullSqrAssembly:
    def test_all_fields_populated(self, builder):
        r = builder.build(
            "查询前10个订单按金额降序",
            "zh",
            IntentType.SELECT,
            time_range=TimeRange(),
            ambiguities=[Ambiguity(aspect="range", description="test")],
        )
        assert r.raw_text == "查询前10个订单按金额降序"
        assert r.language == "zh"
        assert r.intent == IntentType.SELECT
        assert r.limit == 10
        assert len(r.order_by) >= 1
        assert r.time_range is not None
        assert len(r.ambiguities) == 1
        assert 0.0 <= r.confidence <= 1.0

    def test_minimal_sqr(self, builder):
        r = builder.build("hello", "en", IntentType.UNKNOWN)
        assert r.raw_text == "hello"
        assert r.language == "en"
        assert r.intent == IntentType.UNKNOWN
        assert r.limit is None
        assert r.order_by == []
        assert r.time_range is None
        assert r.ambiguities == []
        assert r.target_tables == []
        assert r.entities == []

    def test_explicit_entities_merged(self, builder):
        """Pre-extracted entities should be merged with builder-extracted ones."""
        from app.models.query import Entity
        pre = [Entity(name="pre_entity", type="term", normalized="pre_entity")]
        r = builder.build('找"测试商品"', "zh", IntentType.SELECT, entities=pre)
        names = {e.name for e in r.entities}
        assert "pre_entity" in names
        assert "测试商品" in names
