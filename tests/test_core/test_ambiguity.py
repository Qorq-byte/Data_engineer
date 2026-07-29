"""Unit tests for AmbiguityDetector — 4 ambiguity types."""

import pytest

from app.core.ambiguity import AmbiguityDetector
from app.models.query import IntentType


@pytest.fixture
def detector():
    return AmbiguityDetector()


# ═══════════════════════════════════════════════════════════════════════════
# Helper
# ═══════════════════════════════════════════════════════════════════════════


def _aspects(ambiguities):
    """Return set of aspect strings from ambiguity list."""
    return {a.aspect for a in ambiguities}


def _descriptions(ambiguities):
    """Return list of description strings."""
    return [a.description for a in ambiguities]


# ═══════════════════════════════════════════════════════════════════════════
# Attribute Ambiguity — Chinese
# ═══════════════════════════════════════════════════════════════════════════


class TestAttributeAmbiguityZh:
    def test_region_ambiguous(self, detector):
        r = detector.detect("按地区统计销售额", IntentType.SELECT)
        assert any("地区" in a.description for a in r)
        assert any(a.aspect == "attribute" for a in r)
        # options should suggest city/province/region
        attr = next(a for a in r if "地区" in a.description)
        assert "city" in attr.options or "province" in attr.options

    def test_name_ambiguous(self, detector):
        r = detector.detect("按名称查找商品", IntentType.SELECT)
        assert any("名称" in a.description for a in r)

    def test_type_ambiguous(self, detector):
        r = detector.detect("按类别分组", IntentType.AGGREGATE)
        assert any(a.aspect == "attribute" for a in r)

    def test_date_ambiguous(self, detector):
        r = detector.detect("按日期排序", IntentType.SELECT)
        assert any("日期" in a.description for a in r)

    def test_status_ambiguous(self, detector):
        r = detector.detect("按状态筛选订单", IntentType.SELECT)
        assert any("状态" in a.description for a in r)

    def test_level_ambiguous(self, detector):
        r = detector.detect("按级别分类", IntentType.SELECT)
        assert any("级别" in a.description for a in r)

    def test_id_ambiguous(self, detector):
        r = detector.detect("按编号查询", IntentType.SELECT)
        assert any("编号" in a.description for a in r)

    def test_each_region(self, detector):
        r = detector.detect("各个城市的销售数据", IntentType.SELECT)
        assert any("城市" in a.description for a in r)

    def test_different_region(self, detector):
        r = detector.detect("不同地区的用户分布", IntentType.SELECT)
        assert any("地区" in a.description for a in r)


# ═══════════════════════════════════════════════════════════════════════════
# Attribute Ambiguity — English
# ═══════════════════════════════════════════════════════════════════════════


class TestAttributeAmbiguityEn:
    def test_by_region(self, detector):
        r = detector.detect("sales by region", IntentType.SELECT)
        assert any("region" in a.description for a in r)

    def test_by_name(self, detector):
        r = detector.detect("find products by name", IntentType.SELECT)
        assert any("name" in a.description for a in r)

    def test_by_type(self, detector):
        r = detector.detect("group by type", IntentType.AGGREGATE)
        assert any(a.aspect == "attribute" for a in r)

    def test_by_date(self, detector):
        r = detector.detect("sort by date", IntentType.SELECT)
        assert any("date" in a.description for a in r)

    def test_by_status(self, detector):
        r = detector.detect("filter by status", IntentType.SELECT)
        assert any("status" in a.description for a in r)

    def test_by_level(self, detector):
        r = detector.detect("group by level", IntentType.AGGREGATE)
        assert any("level" in a.description for a in r)

    def test_by_id(self, detector):
        r = detector.detect("look up by id", IntentType.SELECT)
        assert any("id" in a.description for a in r)

    def test_per_region(self, detector):
        r = detector.detect("revenue per region", IntentType.SELECT)
        assert any("region" in a.description for a in r)

    def test_different_city(self, detector):
        r = detector.detect("users in different cities", IntentType.SELECT)
        assert any("city" in a.description for a in r)


# ═══════════════════════════════════════════════════════════════════════════
# Aggregation Ambiguity — Chinese
# ═══════════════════════════════════════════════════════════════════════════


class TestAggregationAmbiguityZh:
    def test_average_ambiguous(self, detector):
        r = detector.detect("统计平均订单金额", IntentType.AGGREGATE)
        assert any("平均" in a.description for a in r)
        assert any(a.aspect == "aggregation" for a in r)

    def test_rank_ambiguous(self, detector):
        r = detector.detect("商品排名", IntentType.AGGREGATE)
        assert any("排名" in a.description for a in r)

    def test_percentage_ambiguous(self, detector):
        r = detector.detect("计算各品类占比", IntentType.AGGREGATE)
        assert any(a.aspect == "aggregation" for a in r)

    def test_ratio_ambiguous(self, detector):
        r = detector.detect("分析市场份额比例", IntentType.AGGREGATE)
        assert any(a.aspect == "aggregation" for a in r)

    def test_group_ambiguous(self, detector):
        r = detector.detect("分组统计", IntentType.AGGREGATE)
        assert any(a.aspect == "aggregation" for a in r)

    def test_top_n_rank(self, detector):
        r = detector.detect("销售额排名前10的产品", IntentType.AGGREGATE)
        assert any(a.aspect == "aggregation" for a in r)

    def test_average_value(self, detector):
        r = detector.detect("客户平均年龄", IntentType.AGGREGATE)
        assert any("平均" in a.description for a in r)

    def test_average_price(self, detector):
        r = detector.detect("平均商品价格是多少", IntentType.AGGREGATE)
        assert any("平均" in a.description for a in r)

    def test_share_ambiguous(self, detector):
        r = detector.detect("各渠道份额", IntentType.AGGREGATE)
        assert any(a.aspect == "aggregation" for a in r)


# ═══════════════════════════════════════════════════════════════════════════
# Aggregation Ambiguity — English
# ═══════════════════════════════════════════════════════════════════════════


class TestAggregationAmbiguityEn:
    def test_average_ambiguous(self, detector):
        r = detector.detect("average order value", IntentType.AGGREGATE)
        assert any(a.aspect == "aggregation" for a in r)

    def test_rank_ambiguous(self, detector):
        r = detector.detect("rank products by sales", IntentType.AGGREGATE)
        assert any(a.aspect == "aggregation" for a in r)

    def test_percentage_ambiguous(self, detector):
        r = detector.detect("percentage of total revenue", IntentType.AGGREGATE)
        assert any(a.aspect == "aggregation" for a in r)

    def test_ratio_ambiguous(self, detector):
        r = detector.detect("conversion ratio", IntentType.AGGREGATE)
        assert any(a.aspect == "aggregation" for a in r)

    def test_group_ambiguous(self, detector):
        r = detector.detect("group by something", IntentType.AGGREGATE)
        assert any(a.aspect == "aggregation" for a in r)

    def test_percentile(self, detector):
        r = detector.detect("90th percentile response time", IntentType.AGGREGATE)
        assert any(a.aspect == "aggregation" for a in r)


# ═══════════════════════════════════════════════════════════════════════════
# Aggregation Ambiguity — NOT triggered for non-AGGREGATE intent
# ═══════════════════════════════════════════════════════════════════════════


class TestAggregationOnlyForAggregateIntent:
    def test_average_not_agg_intent(self, detector):
        """'平均' should NOT trigger aggregation ambiguity for SELECT intent."""
        r = detector.detect("查看平均订单金额", IntentType.SELECT)
        assert not any(a.aspect == "aggregation" for a in r)

    def test_average_unknown_intent(self, detector):
        r = detector.detect("平均订单金额", IntentType.UNKNOWN)
        assert not any(a.aspect == "aggregation" for a in r)

    def test_rank_not_agg_intent(self, detector):
        r = detector.detect("查询商品排名", IntentType.SELECT)
        assert not any(a.aspect == "aggregation" for a in r)

    def test_average_with_comparison(self, detector):
        r = detector.detect("对比各平台平均客单价", IntentType.COMPARISON)
        assert not any(a.aspect == "aggregation" for a in r)

    def test_average_with_time_series(self, detector):
        r = detector.detect("平均订单金额的月度趋势", IntentType.TIME_SERIES)
        assert not any(a.aspect == "aggregation" for a in r)


# ═══════════════════════════════════════════════════════════════════════════
# Range Ambiguity — Chinese
# ═══════════════════════════════════════════════════════════════════════════


class TestRangeAmbiguityZh:
    def test_large_orders(self, detector):
        r = detector.detect("查询大额订单", IntentType.SELECT)
        assert any(a.aspect == "range" for a in r)
        assert any("大额" in a.description for a in r)

    def test_high_value_customers(self, detector):
        r = detector.detect("高价值客户有哪些", IntentType.SELECT)
        assert any(a.aspect == "range" for a in r)

    def test_premium_users(self, detector):
        r = detector.detect("优质用户的购买记录", IntentType.SELECT)
        assert any(a.aspect == "range" for a in r)

    def test_vip_clients(self, detector):
        r = detector.detect("VIP客户的订单", IntentType.SELECT)
        assert any(a.aspect == "range" for a in r)

    def test_key_accounts(self, detector):
        r = detector.detect("重点客户的消费趋势", IntentType.TIME_SERIES)
        assert any(a.aspect == "range" for a in r)

    def test_high_end_products(self, detector):
        r = detector.detect("高端商品的销售情况", IntentType.SELECT)
        assert any(a.aspect == "range" for a in r)

    def test_high_price_goods(self, detector):
        r = detector.detect("高价商品占比", IntentType.AGGREGATE)
        assert any(a.aspect == "range" for a in r)

    def test_low_value_customers(self, detector):
        r = detector.detect("低价值客户流失率", IntentType.FUNNEL)
        assert any(a.aspect == "range" for a in r)

    def test_high_rating(self, detector):
        r = detector.detect("高评分商品列表", IntentType.SELECT)
        assert any(a.aspect == "range" for a in r)

    def test_low_credit(self, detector):
        r = detector.detect("低信用用户统计", IntentType.AGGREGATE)
        assert any(a.aspect == "range" for a in r)

    def test_high_activity(self, detector):
        r = detector.detect("高活跃度用户", IntentType.SELECT)
        assert any(a.aspect == "range" for a in r)

    def test_big_deal(self, detector):
        r = detector.detect("大宗交易的金额分布", IntentType.AGGREGATE)
        assert any(a.aspect == "range" for a in r)


# ═══════════════════════════════════════════════════════════════════════════
# Range Ambiguity — English
# ═══════════════════════════════════════════════════════════════════════════


class TestRangeAmbiguityEn:
    def test_high_value(self, detector):
        r = detector.detect("find high-value customers", IntentType.SELECT)
        assert any(a.aspect == "range" for a in r)

    def test_premium(self, detector):
        r = detector.detect("premium users last month", IntentType.SELECT)
        assert any(a.aspect == "range" for a in r)

    def test_vip(self, detector):
        r = detector.detect("VIP client orders", IntentType.SELECT)
        assert any(a.aspect == "range" for a in r)

    def test_top_tier(self, detector):
        r = detector.detect("top-tier customers churn rate", IntentType.FUNNEL)
        assert any(a.aspect == "range" for a in r)

    def test_big_orders(self, detector):
        r = detector.detect("list big orders this week", IntentType.SELECT)
        assert any(a.aspect == "range" for a in r)

    def test_large_deals(self, detector):
        r = detector.detect("large deals by region", IntentType.SELECT)
        assert any(a.aspect == "range" for a in r)

    def test_small_orders(self, detector):
        r = detector.detect("small orders count", IntentType.AGGREGATE)
        assert any(a.aspect == "range" for a in r)

    def test_high_score(self, detector):
        r = detector.detect("high rated products", IntentType.SELECT)
        assert any(a.aspect == "range" for a in r)

    def test_low_rating(self, detector):
        r = detector.detect("low rated sellers", IntentType.SELECT)
        assert any(a.aspect == "range" for a in r)

    def test_major_customers(self, detector):
        r = detector.detect("major customers revenue", IntentType.AGGREGATE)
        assert any(a.aspect == "range" for a in r)


# ═══════════════════════════════════════════════════════════════════════════
# Temporal Ambiguity — Chinese
# ═══════════════════════════════════════════════════════════════════════════


class TestTemporalAmbiguityZh:
    def test_recent_orders(self, detector):
        r = detector.detect("最近的订单", IntentType.SELECT)
        assert any(a.aspect == "temporal" for a in r)
        assert any("最近" in a.description for a in r)

    def test_jinqi(self, detector):
        r = detector.detect("近期交易记录", IntentType.SELECT)
        assert any(a.aspect == "temporal" for a in r)

    def test_jinlai(self, detector):
        r = detector.detect("近来的用户活跃情况", IntentType.SELECT)
        assert any(a.aspect == "temporal" for a in r)

    def test_guoqu(self, detector):
        r = detector.detect("过去的销售数据", IntentType.SELECT)
        assert any(a.aspect == "temporal" for a in r)

    def test_yiwang(self, detector):
        r = detector.detect("以往的交易记录", IntentType.SELECT)
        assert any(a.aspect == "temporal" for a in r)

    def test_lishi(self, detector):
        r = detector.detect("历史订单查询", IntentType.SELECT)
        assert any(a.aspect == "temporal" for a in r)

    def test_recent_with_options(self, detector):
        """Temporal ambiguity should suggest concrete time ranges."""
        r = detector.detect("最近的订单", IntentType.SELECT)
        temporal = next(a for a in r if a.aspect == "temporal")
        assert len(temporal.options) > 0
        # Should suggest at least "最近7天" or "最近30天"
        assert any("7" in o or "30" in o for o in temporal.options)

    def test_recent_not_triggered_with_explicit_range(self, detector):
        """'最近7天' is explicit — NO temporal ambiguity."""
        r = detector.detect("最近7天的订单", IntentType.SELECT)
        assert not any(a.aspect == "temporal" for a in r)

    def test_recent_not_triggered_with_month(self, detector):
        """'最近一个月' is explicit — NO temporal ambiguity."""
        r = detector.detect("最近一个月的销售", IntentType.SELECT)
        assert not any(a.aspect == "temporal" for a in r)

    def test_recent_not_triggered_with_days(self, detector):
        """'最近30天' is explicit — NO temporal ambiguity."""
        r = detector.detect("查看最近30天的活跃用户", IntentType.SELECT)
        assert not any(a.aspect == "temporal" for a in r)

    def test_guoqu_not_triggered_with_explicit(self, detector):
        """'过去三个月' is explicit — NO temporal ambiguity."""
        r = detector.detect("过去三个月的趋势", IntentType.TIME_SERIES)
        assert not any(a.aspect == "temporal" for a in r)

    def test_lishishang_always_ambiguous(self, detector):
        """'历史上' is inherently vague — always flagged even with a year."""
        r = detector.detect("历史上2025年的数据", IntentType.SELECT)
        assert any(a.aspect == "temporal" for a in r)


# ═══════════════════════════════════════════════════════════════════════════
# Temporal Ambiguity — English
# ═══════════════════════════════════════════════════════════════════════════


class TestTemporalAmbiguityEn:
    def test_recent_orders(self, detector):
        r = detector.detect("recent orders", IntentType.SELECT)
        assert any(a.aspect == "temporal" for a in r)

    def test_recently(self, detector):
        r = detector.detect("recently active users", IntentType.SELECT)
        assert any(a.aspect == "temporal" for a in r)

    def test_lately(self, detector):
        r = detector.detect("lately transactions", IntentType.SELECT)
        assert any(a.aspect == "temporal" for a in r)

    def test_of_late(self, detector):
        r = detector.detect("orders of late", IntentType.SELECT)
        assert any(a.aspect == "temporal" for a in r)

    def test_past(self, detector):
        r = detector.detect("past orders", IntentType.SELECT)
        assert any(a.aspect == "temporal" for a in r)

    def test_historical(self, detector):
        r = detector.detect("historical data", IntentType.SELECT)
        assert any(a.aspect == "temporal" for a in r)

    def test_recent_with_options(self, detector):
        r = detector.detect("recent orders", IntentType.SELECT)
        temporal = next(a for a in r if a.aspect == "temporal")
        assert len(temporal.options) > 0

    def test_recent_not_triggered_with_explicit(self, detector):
        """'recent 7 days' is explicit — NO temporal ambiguity."""
        r = detector.detect("recent 7 days orders", IntentType.SELECT)
        assert not any(a.aspect == "temporal" for a in r)

    def test_past_not_triggered_with_explicit(self, detector):
        """'past 30 days' is explicit — NO temporal ambiguity."""
        r = detector.detect("past 30 days activity", IntentType.SELECT)
        assert not any(a.aspect == "temporal" for a in r)

    def test_historical_not_triggered_with_explicit(self, detector):
        """'historical 1 year' is explicit — NO temporal ambiguity."""
        r = detector.detect("historical 1 year trends", IntentType.TIME_SERIES)
        assert not any(a.aspect == "temporal" for a in r)


# ═══════════════════════════════════════════════════════════════════════════
# Combined / Multiple Ambiguities
# ═══════════════════════════════════════════════════════════════════════════


class TestMultipleAmbiguities:
    def test_range_plus_attribute(self, detector):
        """'大额订单按地区统计' has both range and attribute ambiguity."""
        r = detector.detect("大额订单按地区统计", IntentType.AGGREGATE)
        aspects = _aspects(r)
        assert "range" in aspects
        assert "attribute" in aspects

    def test_range_plus_aggregation(self, detector):
        """'高价值客户的平均订单金额' has range + aggregation ambiguity."""
        r = detector.detect("高价值客户的平均订单金额", IntentType.AGGREGATE)
        aspects = _aspects(r)
        assert "range" in aspects
        assert "aggregation" in aspects

    def test_temporal_plus_range(self, detector):
        """'最近的大额订单' has temporal + range ambiguity."""
        r = detector.detect("最近的大额订单", IntentType.SELECT)
        aspects = _aspects(r)
        assert "temporal" in aspects
        assert "range" in aspects

    def test_attribute_plus_temporal(self, detector):
        """'最近按地区统计' has temporal + attribute ambiguity."""
        r = detector.detect("最近按地区统计的销售数据", IntentType.AGGREGATE)
        aspects = _aspects(r)
        assert "temporal" in aspects
        assert "attribute" in aspects

    def test_three_types(self, detector):
        """'大额订单按日期统计最近趋势' has range + attribute + temporal."""
        r = detector.detect("大额订单按日期统计最近趋势", IntentType.AGGREGATE)
        aspects = _aspects(r)
        assert "range" in aspects
        assert "attribute" in aspects
        assert "temporal" in aspects

    def test_all_four_types(self, detector):
        """Full combo: attribute + aggregation + range + temporal."""
        r = detector.detect(
            "最近高价值客户的平均订单金额按地区排名", IntentType.AGGREGATE
        )
        aspects = _aspects(r)
        assert "attribute" in aspects  # 按地区
        assert "aggregation" in aspects  # 平均, 排名
        assert "range" in aspects  # 高价值
        assert "temporal" in aspects  # 最近


# ═══════════════════════════════════════════════════════════════════════════
# Edge Cases
# ═══════════════════════════════════════════════════════════════════════════


class TestEdgeCases:
    def test_empty_text(self, detector):
        r = detector.detect("", IntentType.SELECT)
        assert r == []

    def test_no_ambiguity_simple(self, detector):
        """A simple, unambiguous query."""
        r = detector.detect("查询用户表的所有记录", IntentType.SELECT)
        assert r == []

    def test_no_ambiguity_specific_table(self, detector):
        r = detector.detect("find all records from the users table", IntentType.SELECT)
        assert r == []

    def test_no_ambiguity_explicit_columns(self, detector):
        r = detector.detect("查询订单编号为1001的详情", IntentType.SELECT)
        assert r == []

    def test_english_no_ambiguity(self, detector):
        r = detector.detect("find order 12345 details", IntentType.SELECT)
        assert r == []

    def test_duplicate_patterns_not_duplicated(self, detector):
        """A query matching multiple attr patterns should not duplicate by exact same match."""
        # "按类别名称" could match both "类别" and "名称" patterns
        r = detector.detect("按类别名称分组", IntentType.AGGREGATE)
        # Should have distinct matches, not duplicates
        assert len(r) == len({a.description for a in r})

    def test_ambiguity_has_correct_aspect_field(self, detector):
        r = detector.detect("查询大额订单", IntentType.SELECT)
        for a in r:
            assert a.aspect in ("attribute", "aggregation", "range", "temporal")

    def test_ambiguity_options_is_list(self, detector):
        r = detector.detect("最近的订单", IntentType.SELECT)
        for a in r:
            assert isinstance(a.options, list)

    def test_ambiguity_default_is_none(self, detector):
        r = detector.detect("最近的订单", IntentType.SELECT)
        for a in r:
            assert a.default is None

    def test_context_ignored_for_now(self, detector):
        """context parameter is reserved and should be accepted but ignored."""
        r = detector.detect("最近的订单", IntentType.SELECT, context={"schema": "test"})
        assert any(a.aspect == "temporal" for a in r)

    def test_all_intent_types_work(self, detector):
        """detect() should work with all 7 intent types."""
        for intent in IntentType:
            r = detector.detect("test query", intent)
            assert isinstance(r, list)


# ═══════════════════════════════════════════════════════════════════════════
# Description + Options quality
# ═══════════════════════════════════════════════════════════════════════════


class TestDescriptionQuality:
    def test_attribute_description_not_empty(self, detector):
        r = detector.detect("按地区统计", IntentType.SELECT)
        for a in r:
            assert len(a.description) > 0

    def test_range_description_mentions_threshold(self, detector):
        r = detector.detect("大额订单", IntentType.SELECT)
        for a in r:
            if a.aspect == "range":
                assert len(a.description) > 0

    def test_temporal_description_mentions_range(self, detector):
        r = detector.detect("最近的订单", IntentType.SELECT)
        for a in r:
            if a.aspect == "temporal":
                assert len(a.description) > 0

    def test_aggregation_description_mentions_method(self, detector):
        r = detector.detect("平均订单金额", IntentType.AGGREGATE)
        for a in r:
            if a.aspect == "aggregation":
                assert len(a.description) > 0
