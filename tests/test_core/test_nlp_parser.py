"""Integration tests for NLParser — 5-step NL→SQR pipeline."""

import pytest

from app.core.nlp_parser import NLParser
from app.models.query import IntentType

pytestmark = pytest.mark.anyio


@pytest.fixture
def parser():
    return NLParser()


# ═══════════════════════════════════════════════════════════════════════════
# End-to-end Pipeline — Chinese
# ═══════════════════════════════════════════════════════════════════════════


class TestE2EChinese:
    async def test_simple_select(self, parser):
        sqr = await parser.parse("查询所有订单")
        assert sqr.language == "zh"
        assert sqr.intent == IntentType.SELECT
        assert sqr.raw_text == "查询所有订单"

    async def test_aggregate_with_time(self, parser):
        sqr = await parser.parse("统计上个月各地区的销售额")
        assert sqr.language == "zh"
        assert sqr.intent == IntentType.AGGREGATE
        assert sqr.time_range is not None

    async def test_with_limit_and_order(self, parser):
        sqr = await parser.parse("查询前10个订单按金额降序")
        assert sqr.language == "zh"
        assert sqr.limit == 10
        assert len(sqr.order_by) >= 1

    async def test_with_ambiguities(self, parser):
        sqr = await parser.parse("最近大额订单的平均金额按地区排名")
        assert sqr.language == "zh"
        assert sqr.intent == IntentType.AGGREGATE
        assert len(sqr.ambiguities) > 0
        aspects = {a.aspect for a in sqr.ambiguities}
        # Should have at least: range, aggregation, temporal, attribute
        assert len(aspects) >= 2

    async def test_time_series(self, parser):
        sqr = await parser.parse("按月统计销售额趋势")
        assert sqr.intent == IntentType.TIME_SERIES
        assert sqr.language == "zh"

    async def test_comparison(self, parser):
        sqr = await parser.parse("对比北京和上海的销售额")
        assert sqr.intent == IntentType.COMPARISON
        assert sqr.language == "zh"

    async def test_funnel(self, parser):
        sqr = await parser.parse("用户转化率漏斗分析")
        assert sqr.intent == IntentType.FUNNEL

    async def test_join(self, parser):
        sqr = await parser.parse("关联用户表和订单表查询")
        assert sqr.intent == IntentType.JOIN
        assert len(sqr.target_tables) >= 1


# ═══════════════════════════════════════════════════════════════════════════
# End-to-end Pipeline — English
# ═══════════════════════════════════════════════════════════════════════════


class TestE2EEnglish:
    async def test_simple_select(self, parser):
        sqr = await parser.parse("find all orders")
        assert sqr.language == "en"
        assert sqr.intent == IntentType.SELECT

    async def test_aggregate(self, parser):
        sqr = await parser.parse("count orders last month")
        assert sqr.language == "en"
        assert sqr.intent == IntentType.AGGREGATE
        assert sqr.time_range is not None

    async def test_with_limit(self, parser):
        sqr = await parser.parse("top 5 products by revenue")
        assert sqr.language == "en"
        assert sqr.limit == 5

    async def test_with_order(self, parser):
        sqr = await parser.parse("orders sorted by date desc")
        assert sqr.language == "en"
        assert len(sqr.order_by) >= 1
        assert any(o.direction == "DESC" for o in sqr.order_by)

    async def test_time_series(self, parser):
        sqr = await parser.parse("monthly revenue trend")
        assert sqr.intent == IntentType.TIME_SERIES

    async def test_comparison(self, parser):
        sqr = await parser.parse("compare sales vs last year")
        assert sqr.intent == IntentType.COMPARISON

    async def test_funnel(self, parser):
        sqr = await parser.parse("conversion funnel analysis")
        assert sqr.intent == IntentType.FUNNEL


# ═══════════════════════════════════════════════════════════════════════════
# Edge Cases
# ═══════════════════════════════════════════════════════════════════════════


class TestEdgeCases:
    async def test_empty_text(self, parser):
        sqr = await parser.parse("")
        assert sqr.language == "en"
        assert sqr.intent == IntentType.UNKNOWN
        assert sqr.confidence <= 0.6

    async def test_pure_numbers(self, parser):
        sqr = await parser.parse("12345")
        assert sqr.language == "en"
        assert sqr.intent == IntentType.UNKNOWN

    async def test_mixed_language(self, parser):
        sqr = await parser.parse("查询iPhone 15 sales数据")
        assert sqr.language == "mixed"
        # "查询" → zh SELECT, "sales" → could be en SELECT or TIME_SERIES
        assert sqr.intent != IntentType.UNKNOWN

    async def test_sql_input(self, parser):
        sqr = await parser.parse("SELECT * FROM orders WHERE amount > 100")
        assert sqr.intent == IntentType.SELECT

    async def test_context_accepted(self, parser):
        """context parameter should be accepted (reserved for Phase 3)."""
        sqr = await parser.parse("查询订单", context={"domain": "ecommerce"})
        assert sqr.raw_text == "查询订单"

    async def test_confidence_in_range(self, parser):
        sqr = await parser.parse("find all orders")
        assert 0.0 <= sqr.confidence <= 1.0

    async def test_entities_extracted(self, parser):
        sqr = await parser.parse('查找产品"iPhone 15 Pro"的销量')
        assert any("iPhone 15 Pro" in e.name for e in sqr.entities)

    async def test_return_type_is_sqr(self, parser):
        from app.models.query import SQR
        sqr = await parser.parse("test query")
        assert isinstance(sqr, SQR)


# ═══════════════════════════════════════════════════════════════════════════
# Component Isolation
# ═══════════════════════════════════════════════════════════════════════════


class TestComponentIsolation:
    async def test_custom_components_accepted(self):
        """NLParser should accept custom component instances."""
        from app.core.ambiguity import AmbiguityDetector
        from app.core.intent_classifier import IntentClassifier
        from app.core.language_detector import LanguageDetector
        from app.core.sqr_builder import SQRBuilder
        from app.core.time_parser import TimeParser

        parser = NLParser(
            language_detector=LanguageDetector(),
            time_parser=TimeParser(),
            intent_classifier=IntentClassifier(),
            ambiguity_detector=AmbiguityDetector(),
            sqr_builder=SQRBuilder(),
        )
        sqr = await parser.parse("统计上个月的订单")
        assert sqr.language == "zh"
        assert sqr.intent == IntentType.AGGREGATE

    async def test_default_components_created(self):
        """NLParser with no args should create default components."""
        parser = NLParser()
        assert parser.language_detector is not None
        assert parser.time_parser is not None
        assert parser.intent_classifier is not None
        assert parser.ambiguity_detector is not None
        assert parser.sqr_builder is not None
