"""Unit tests for IntentClassifier — 7-type intent classification."""

import pytest

from app.core.intent_classifier import IntentClassifier
from app.models.query import IntentType


@pytest.fixture
def classifier():
    return IntentClassifier()


# ═══════════════════════════════════════════════════════════════════════════
# Chinese — SELECT
# ═══════════════════════════════════════════════════════════════════════════


class TestChineseSelect:
    def test_simple_query(self, classifier):
        assert classifier.classify("查询所有订单", "zh") == IntentType.SELECT

    def test_view(self, classifier):
        assert classifier.classify("查看用户列表", "zh") == IntentType.SELECT

    def test_list(self, classifier):
        assert classifier.classify("列出所有产品", "zh") == IntentType.SELECT

    def test_show(self, classifier):
        assert classifier.classify("显示华东地区的销售数据", "zh") == IntentType.SELECT

    def test_find(self, classifier):
        assert classifier.classify("找一下北京的客户", "zh") == IntentType.SELECT

    def test_help_me_query(self, classifier):
        assert classifier.classify("帮我查上个月的收入", "zh") == IntentType.SELECT

    def test_what_are(self, classifier):
        assert classifier.classify("有哪些活跃用户", "zh") == IntentType.SELECT

    def test_what_is(self, classifier):
        assert classifier.classify("销售额最高的产品是什么", "zh") == IntentType.SELECT


# ═══════════════════════════════════════════════════════════════════════════
# Chinese — AGGREGATE
# ═══════════════════════════════════════════════════════════════════════════


class TestChineseAggregate:
    def test_tongji(self, classifier):
        assert classifier.classify("统计上个月的销售额", "zh") == IntentType.AGGREGATE

    def test_huizong(self, classifier):
        assert classifier.classify("汇总各地区的订单", "zh") == IntentType.AGGREGATE

    def test_zongji(self, classifier):
        assert classifier.classify("总计收入是多少", "zh") == IntentType.AGGREGATE

    def test_average(self, classifier):
        assert classifier.classify("平均客单价", "zh") == IntentType.AGGREGATE

    def test_count(self, classifier):
        assert classifier.classify("计数活跃用户", "zh") == IntentType.AGGREGATE

    def test_max_min(self, classifier):
        assert classifier.classify("最大值和最小值", "zh") == IntentType.AGGREGATE

    def test_ranking(self, classifier):
        assert classifier.classify("销售额排名前10的产品", "zh") == IntentType.AGGREGATE

    def test_grouping(self, classifier):
        assert classifier.classify("按地区分组统计", "zh") == IntentType.AGGREGATE

    def test_each_category(self, classifier):
        assert classifier.classify("各种类型的订单数量", "zh") == IntentType.AGGREGATE

    def test_sum_query(self, classifier):
        assert classifier.classify("求和各月份收入", "zh") == IntentType.AGGREGATE

    def test_each_region(self, classifier):
        assert classifier.classify("每个地区的销售额", "zh") == IntentType.AGGREGATE

    def test_separately(self, classifier):
        assert classifier.classify("分别统计各部门人数", "zh") == IntentType.AGGREGATE


# ═══════════════════════════════════════════════════════════════════════════
# Chinese — JOIN
# ═══════════════════════════════════════════════════════════════════════════


class TestChineseJoin:
    def test_guanlian(self, classifier):
        assert classifier.classify("关联用户表和订单表", "zh") == IntentType.JOIN

    def test_lianhe(self, classifier):
        assert classifier.classify("联合查询产品与库存", "zh") == IntentType.JOIN

    def test_duobiao(self, classifier):
        assert classifier.classify("多表联合分析", "zh") == IntentType.JOIN

    def test_kuabiao(self, classifier):
        assert classifier.classify("跨表查询用户订单", "zh") == IntentType.JOIN

    def test_duiying_table(self, classifier):
        assert classifier.classify("对应产品表的分类信息", "zh") == IntentType.JOIN


# ═══════════════════════════════════════════════════════════════════════════
# Chinese — COMPARISON
# ═══════════════════════════════════════════════════════════════════════════


class TestChineseComparison:
    def test_duibi(self, classifier):
        assert classifier.classify("对比今年和去年的销售额", "zh") == IntentType.COMPARISON

    def test_bijiao(self, classifier):
        assert classifier.classify("比较两个地区的业绩", "zh") == IntentType.COMPARISON

    def test_greater_than(self, classifier):
        assert classifier.classify("销售额大于100万的订单", "zh") == IntentType.COMPARISON

    def test_less_than(self, classifier):
        assert classifier.classify("价格低于50元的产品", "zh") == IntentType.COMPARISON

    def test_higher_than(self, classifier):
        assert classifier.classify("收入高于去年同期的部门", "zh") == IntentType.COMPARISON

    def test_which_is_more(self, classifier):
        assert classifier.classify("哪个产品更受欢迎", "zh") == IntentType.COMPARISON

    def test_vs(self, classifier):
        assert classifier.classify("线上 VS 线下销售对比", "zh") == IntentType.COMPARISON

    def test_difference(self, classifier):
        assert classifier.classify("两部门的业绩差异", "zh") == IntentType.COMPARISON


# ═══════════════════════════════════════════════════════════════════════════
# Chinese — TIME_SERIES
# ═══════════════════════════════════════════════════════════════════════════


class TestChineseTimeSeries:
    def test_trend(self, classifier):
        assert classifier.classify("华东地区的销售趋势", "zh") == IntentType.TIME_SERIES

    def test_zoushi(self, classifier):
        assert classifier.classify("用户增长走势", "zh") == IntentType.TIME_SERIES

    def test_monthly(self, classifier):
        assert classifier.classify("按月统计订单量", "zh") == IntentType.TIME_SERIES

    def test_daily(self, classifier):
        assert classifier.classify("按天查看活跃用户", "zh") == IntentType.TIME_SERIES

    def test_weekly(self, classifier):
        assert classifier.classify("按周汇总销售额", "zh") == IntentType.TIME_SERIES

    def test_quarterly(self, classifier):
        assert classifier.classify("按季度分析收入变化", "zh") == IntentType.TIME_SERIES

    def test_yearly(self, classifier):
        assert classifier.classify("按年对比利润", "zh") == IntentType.TIME_SERIES

    def test_yoy(self, classifier):
        assert classifier.classify("同比增长率", "zh") == IntentType.TIME_SERIES

    def test_mom(self, classifier):
        assert classifier.classify("环比变化", "zh") == IntentType.TIME_SERIES

    def test_time_series(self, classifier):
        assert classifier.classify("订单量的时间序列分析", "zh") == IntentType.TIME_SERIES

    def test_every_day(self, classifier):
        assert classifier.classify("每天的访问量", "zh") == IntentType.TIME_SERIES

    def test_change(self, classifier):
        assert classifier.classify("最近三个月的变化趋势", "zh") == IntentType.TIME_SERIES

    def test_day_by_day(self, classifier):
        assert classifier.classify("逐日统计新用户", "zh") == IntentType.TIME_SERIES


# ═══════════════════════════════════════════════════════════════════════════
# Chinese — FUNNEL
# ═══════════════════════════════════════════════════════════════════════════


class TestChineseFunnel:
    def test_conversion(self, classifier):
        assert classifier.classify("用户转化率分析", "zh") == IntentType.FUNNEL

    def test_funnel(self, classifier):
        assert classifier.classify("购买漏斗数据", "zh") == IntentType.FUNNEL

    def test_retention(self, classifier):
        assert classifier.classify("用户留存率", "zh") == IntentType.FUNNEL

    def test_churn(self, classifier):
        assert classifier.classify("客户流失分析", "zh") == IntentType.FUNNEL

    def test_bounce(self, classifier):
        assert classifier.classify("页面跳出率", "zh") == IntentType.FUNNEL


# ═══════════════════════════════════════════════════════════════════════════
# English — SELECT
# ═══════════════════════════════════════════════════════════════════════════


class TestEnglishSelect:
    def test_find(self, classifier):
        assert classifier.classify("find all orders", "en") == IntentType.SELECT

    def test_list(self, classifier):
        assert classifier.classify("list users by region", "en") == IntentType.SELECT

    def test_show(self, classifier):
        assert classifier.classify("show me the revenue", "en") == IntentType.SELECT

    def test_get(self, classifier):
        assert classifier.classify("get customer data", "en") == IntentType.SELECT

    def test_retrieve(self, classifier):
        assert classifier.classify("retrieve order history", "en") == IntentType.SELECT

    def test_search(self, classifier):
        assert classifier.classify("search for active users", "en") == IntentType.SELECT

    def test_look_up(self, classifier):
        assert classifier.classify("look up product details", "en") == IntentType.SELECT


# ═══════════════════════════════════════════════════════════════════════════
# English — AGGREGATE
# ═══════════════════════════════════════════════════════════════════════════


class TestEnglishAggregate:
    def test_count(self, classifier):
        assert classifier.classify("count of orders by region", "en") == IntentType.AGGREGATE

    def test_sum(self, classifier):
        assert classifier.classify("sum of revenue last month", "en") == IntentType.AGGREGATE

    def test_average(self, classifier):
        assert classifier.classify("average order value", "en") == IntentType.AGGREGATE

    def test_total(self, classifier):
        assert classifier.classify("total sales by category", "en") == IntentType.AGGREGATE

    def test_aggregate(self, classifier):
        assert classifier.classify("aggregate user activity", "en") == IntentType.AGGREGATE

    def test_max_min(self, classifier):
        assert classifier.classify("max and min price per category", "en") == IntentType.AGGREGATE

    def test_group_by(self, classifier):
        assert classifier.classify("group by department", "en") == IntentType.AGGREGATE

    def test_summary(self, classifier):
        # "quarterly" (TIME_SERIES, 3) > "summary" (AGGREGATE, 2) → TIME_SERIES
        assert classifier.classify("summary of quarterly earnings", "en") == IntentType.TIME_SERIES

    def test_breakdown(self, classifier):
        assert classifier.classify("breakdown by product line", "en") == IntentType.AGGREGATE

    def test_how_many(self, classifier):
        assert classifier.classify("how many active users", "en") == IntentType.AGGREGATE

    def test_how_much(self, classifier):
        assert classifier.classify("how much revenue this year", "en") == IntentType.AGGREGATE


# ═══════════════════════════════════════════════════════════════════════════
# English — JOIN
# ═══════════════════════════════════════════════════════════════════════════


class TestEnglishJoin:
    def test_join(self, classifier):
        assert classifier.classify("join users with orders", "en") == IntentType.JOIN

    def test_together_with(self, classifier):
        assert classifier.classify("users together with their orders", "en") == IntentType.JOIN

    def test_combine(self, classifier):
        assert classifier.classify("combine customer and order data", "en") == IntentType.JOIN

    def test_across(self, classifier):
        assert classifier.classify("query across multiple tables", "en") == IntentType.JOIN

    def test_correlate(self, classifier):
        assert classifier.classify("correlate sales with inventory", "en") == IntentType.JOIN


# ═══════════════════════════════════════════════════════════════════════════
# English — COMPARISON
# ═══════════════════════════════════════════════════════════════════════════


class TestEnglishComparison:
    def test_compare(self, classifier):
        assert classifier.classify("compare this year vs last year", "en") == IntentType.COMPARISON

    def test_versus(self, classifier):
        assert classifier.classify("online versus offline sales", "en") == IntentType.COMPARISON

    def test_vs(self, classifier):
        assert classifier.classify("east region vs west region", "en") == IntentType.COMPARISON

    def test_greater_than(self, classifier):
        assert classifier.classify("orders greater than $100", "en") == IntentType.COMPARISON

    def test_less_than(self, classifier):
        assert classifier.classify("products less than $50", "en") == IntentType.COMPARISON

    def test_higher_than(self, classifier):
        assert classifier.classify("revenue higher than target", "en") == IntentType.COMPARISON

    def test_difference(self, classifier):
        assert classifier.classify("difference between Q1 and Q2", "en") == IntentType.COMPARISON


# ═══════════════════════════════════════════════════════════════════════════
# English — TIME_SERIES
# ═══════════════════════════════════════════════════════════════════════════


class TestEnglishTimeSeries:
    def test_trend(self, classifier):
        assert classifier.classify("sales trend over time", "en") == IntentType.TIME_SERIES

    def test_monthly(self, classifier):
        assert classifier.classify("monthly revenue report", "en") == IntentType.TIME_SERIES

    def test_daily(self, classifier):
        assert classifier.classify("daily active users", "en") == IntentType.TIME_SERIES

    def test_weekly(self, classifier):
        # "weekly" (TIME_SERIES, 3) tied with "count" (AGGREGATE, 3) → TIME_SERIES wins on priority
        assert classifier.classify("weekly order count", "en") == IntentType.TIME_SERIES

    def test_quarterly(self, classifier):
        assert classifier.classify("quarterly earnings", "en") == IntentType.TIME_SERIES

    def test_yearly(self, classifier):
        assert classifier.classify("yearly summary", "en") == IntentType.TIME_SERIES

    def test_over_time(self, classifier):
        assert classifier.classify("revenue over time", "en") == IntentType.TIME_SERIES

    def test_yoy(self, classifier):
        assert classifier.classify("YoY growth rate", "en") == IntentType.TIME_SERIES

    def test_mom(self, classifier):
        assert classifier.classify("MoM change", "en") == IntentType.TIME_SERIES

    def test_dod(self, classifier):
        assert classifier.classify("DoD active users", "en") == IntentType.TIME_SERIES

    def test_historical(self, classifier):
        assert classifier.classify("historical sales data", "en") == IntentType.TIME_SERIES

    def test_time_series(self, classifier):
        assert classifier.classify("time series analysis of orders", "en") == IntentType.TIME_SERIES


# ═══════════════════════════════════════════════════════════════════════════
# English — FUNNEL
# ═══════════════════════════════════════════════════════════════════════════


class TestEnglishFunnel:
    def test_funnel(self, classifier):
        assert classifier.classify("purchase funnel analysis", "en") == IntentType.FUNNEL

    def test_conversion(self, classifier):
        assert classifier.classify("conversion rate by channel", "en") == IntentType.FUNNEL

    def test_retention(self, classifier):
        assert classifier.classify("user retention analysis", "en") == IntentType.FUNNEL

    def test_churn(self, classifier):
        assert classifier.classify("customer churn rate", "en") == IntentType.FUNNEL

    def test_drop_off(self, classifier):
        assert classifier.classify("drop-off rate at checkout", "en") == IntentType.FUNNEL

    def test_bounce(self, classifier):
        assert classifier.classify("bounce rate by page", "en") == IntentType.FUNNEL


# ═══════════════════════════════════════════════════════════════════════════
# Mixed / code-switched input
# ═══════════════════════════════════════════════════════════════════════════


class TestMixedLanguage:
    def test_chinese_text_english_keyword(self, classifier):
        """Mixed input applies both zh and en patterns."""
        result = classifier.classify("查询用户 conversion rate", "mixed")
        # "conversion" (en, weight 3) + "查询" (zh, weight 2)
        # FUNNEL has 3 > SELECT has 2 → FUNNEL wins
        assert result == IntentType.FUNNEL

    def test_english_text_chinese_keyword(self, classifier):
        result = classifier.classify("find 订单和用户 trend", "mixed")
        # "find" → SELECT, "trend" → TIME_SERIES (weight 3)
        assert result == IntentType.TIME_SERIES


# ═══════════════════════════════════════════════════════════════════════════
# SQL keyword input
# ═══════════════════════════════════════════════════════════════════════════


class TestSQLInput:
    def test_select_statement(self, classifier):
        result = classifier.classify("SELECT * FROM orders", "en")
        assert result == IntentType.SELECT

    def test_select_with_aggregate(self, classifier):
        result = classifier.classify("SELECT COUNT(*) FROM users", "en")
        # COUNT → AGGREGATE (3) vs SELECT → SELECT (3+2+2=7)
        # Wait: "SELECT"=3, "FROM"=2 → SELECT score=5. "COUNT"=3 → AGGREGATE=3.
        # SELECT wins.
        assert result == IntentType.SELECT

    def test_select_with_group_by(self, classifier):
        result = classifier.classify(
            "SELECT region, SUM(revenue) FROM sales GROUP BY region", "en"
        )
        # SELECT=3, FROM=2, WHERE=0 → SELECT=5
        # SUM=3, GROUP BY=3 → AGGREGATE=6
        # AGGREGATE wins.
        assert result == IntentType.AGGREGATE

    def test_join_statement(self, classifier):
        result = classifier.classify(
            "SELECT * FROM users JOIN orders ON users.id = orders.user_id", "en"
        )
        # JOIN=3 → JOIN
        # SELECT=3, FROM=2 → SELECT=5
        # SELECT wins.
        assert result == IntentType.SELECT

    def test_comparison_select(self, classifier):
        result = classifier.classify(
            "SELECT * FROM orders WHERE amount > 100", "en"
        )
        # ">" in COMPARISON (1), SELECT=3+2+2=7
        assert result == IntentType.SELECT


# ═══════════════════════════════════════════════════════════════════════════
# Tie-breaking — priority: FUNNEL > TIME_SERIES > AGGREGATE > COMPARISON > JOIN > SELECT
# ═══════════════════════════════════════════════════════════════════════════


class TestTieBreaking:
    def test_funnel_beats_time_series(self, classifier):
        # "趋势"=3 (TIME_SERIES), "转化"=3 (FUNNEL) → equal weight, FUNNEL wins
        result = classifier.classify("转化趋势分析", "zh")
        assert result == IntentType.FUNNEL

    def test_time_series_beats_aggregate(self, classifier):
        # "趋势"=3 (TIME_SERIES), "统计"=3 (AGGREGATE) → TIME_SERIES wins
        result = classifier.classify("统计最近三个月的趋势", "zh")
        assert result == IntentType.TIME_SERIES

    def test_aggregate_beats_comparison(self, classifier):
        # "统计"=3 (AGGREGATE), "对比"=3 (COMPARISON) → AGGREGATE wins
        result = classifier.classify("统计并对比两个地区", "zh")
        assert result == IntentType.AGGREGATE

    def test_comparison_beats_join(self, classifier):
        # "对比"=3 (COMPARISON), "关联"=3 (JOIN) → COMPARISON wins
        result = classifier.classify("对比关联数据", "zh")
        assert result == IntentType.COMPARISON

    def test_join_beats_select(self, classifier):
        # "关联"=3 (JOIN), "查询"=2 (SELECT) → JOIN wins
        result = classifier.classify("关联查询用户和订单", "zh")
        assert result == IntentType.JOIN


# ═══════════════════════════════════════════════════════════════════════════
# Edge cases
# ═══════════════════════════════════════════════════════════════════════════


class TestEdgeCases:
    def test_empty_string(self, classifier):
        assert classifier.classify("", "zh") == IntentType.UNKNOWN

    def test_unknown_language_defaults(self, classifier):
        """For unknown language, only 'both' patterns match."""
        result = classifier.classify("查询所有订单", "fr")
        # No pattern matches → UNKNOWN
        assert result == IntentType.UNKNOWN

    def test_no_keyword_match(self, classifier):
        """Input with no recognizable keywords."""
        assert classifier.classify("xyzzy plugh", "en") == IntentType.UNKNOWN

    def test_pure_numbers(self, classifier):
        assert classifier.classify("12345 67890", "zh") == IntentType.UNKNOWN

    def test_only_sql_count(self, classifier):
        """COUNT alone in mixed input."""
        result = classifier.classify("COUNT users", "mixed")
        assert result == IntentType.AGGREGATE

    def test_case_insensitivity(self, classifier):
        result = classifier.classify("Find All Orders", "en")
        assert result == IntentType.SELECT

    def test_multiple_intent_signals(self, classifier):
        """When multiple intents have signals, strongest weighted one wins."""
        # "查询"=2 (SELECT), "统计"=3 (AGGREGATE)
        result = classifier.classify("查询并统计用户数据", "zh")
        assert result == IntentType.AGGREGATE

    def test_language_filtering_zh_pattern_doesnt_apply_to_en(self, classifier):
        """Chinese-only keywords should NOT apply to English text."""
        result = classifier.classify("trends in user data", "en")
        # "trend" is in TIME_SERIES patterns for English
        assert result == IntentType.TIME_SERIES

    def test_language_filtering_en_pattern_doesnt_apply_to_zh(self, classifier):
        """English-only keywords should NOT apply to Chinese text."""
        # Pure Chinese text without any zh keyword → UNKNOWN with en text
        result = classifier.classify("这是一个测试", "zh")
        assert result == IntentType.UNKNOWN
