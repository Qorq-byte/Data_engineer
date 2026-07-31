"""Tests for PlanSelector — 3-layer plan routing (rules → history → LLM).

Covers SelectionResult, L1 rule patterns, sync mode, PlanLoader integration,
plan existence filtering, and edge cases.
"""

from __future__ import annotations

import pytest

from app.harness.plan_loader import PlanLoader
from app.workflow.plan_selector import (
    DEFAULT_PLAN,
    L1_RULES,
    PlanSelector,
    SelectionResult,
)

# ── Helpers ────────────────────────────────────────────────────────────

def _make_selector(with_loader: bool = True) -> PlanSelector:
    """Create a PlanSelector, optionally with a real PlanLoader."""
    if with_loader:
        loader = PlanLoader("app/workflow/plans")
        return PlanSelector(plan_loader=loader)
    return PlanSelector()


# ── SelectionResult tests ──────────────────────────────────────────────


class TestSelectionResult:
    def test_defaults(self):
        r = SelectionResult(plan_id="ez_query")
        assert r.plan_id == "ez_query"
        assert r.layer == "default"
        assert r.reason == ""
        assert r.confidence == 1.0
        assert r.alternatives == []

    def test_full_construction(self):
        r = SelectionResult(
            plan_id="gensql_agentic",
            layer="L1",
            reason="contains JOIN",
            confidence=0.9,
            alternatives=["ez_query"],
        )
        assert r.plan_id == "gensql_agentic"
        assert r.layer == "L1"
        assert r.reason == "contains JOIN"
        assert r.confidence == 0.9
        assert r.alternatives == ["ez_query"]


# ── L1 Rule-based selection tests ─────────────────────────────────────


class TestL1RuleSelection:
    """Tests for L1 keyword/pattern matching against query text."""

    @pytest.fixture
    def selector(self) -> PlanSelector:
        return _make_selector()

    # ── JOIN patterns → gensql_agentic ─────────────────────────────

    def test_explicit_join(self, selector: PlanSelector):
        r = selector.select_sync("SELECT * FROM orders JOIN users ON orders.user_id = users.id")
        assert r.plan_id == "gensql_agentic"
        assert r.layer == "L1"

    def test_left_join(self, selector: PlanSelector):
        r = selector.select_sync("SELECT a.name, b.total FROM users a LEFT JOIN orders b")
        assert r.plan_id == "gensql_agentic"

    def test_inner_join(self, selector: PlanSelector):
        r = selector.select_sync("get all orders INNER JOIN products")
        assert r.plan_id == "gensql_agentic"

    # ── GROUP BY → gensql_agentic ──────────────────────────────────

    def test_group_by(self, selector: PlanSelector):
        r = selector.select_sync("SELECT region, SUM(amount) FROM orders GROUP BY region")
        assert r.plan_id == "gensql_agentic"

    def test_group_by_lowercase(self, selector: PlanSelector):
        r = selector.select_sync("sales by region group by region")
        assert r.plan_id == "gensql_agentic"

    # ── HAVING → gensql_agentic ────────────────────────────────────

    def test_having_clause(self, selector: PlanSelector):
        r = selector.select_sync(
            "SELECT region, SUM(amount) FROM orders GROUP BY region HAVING SUM(amount) > 1000"
        )
        assert r.plan_id == "gensql_agentic"

    # ── Set operations → gensql_agentic ────────────────────────────

    def test_union(self, selector: PlanSelector):
        r = selector.select_sync("SELECT name FROM customers UNION SELECT name FROM suppliers")
        assert r.plan_id == "gensql_agentic"

    def test_intersect(self, selector: PlanSelector):
        r = selector.select_sync("SELECT id FROM a INTERSECT SELECT id FROM b")
        assert r.plan_id == "gensql_agentic"

    def test_except(self, selector: PlanSelector):
        r = selector.select_sync("SELECT id FROM a EXCEPT SELECT id FROM b")
        assert r.plan_id == "gensql_agentic"

    # ── Subquery → gensql_agentic ──────────────────────────────────

    def test_subquery(self, selector: PlanSelector):
        r = selector.select_sync("SELECT * FROM (SELECT id FROM users) AS sub")
        assert r.plan_id == "gensql_agentic"

    def test_chinese_subquery_keyword(self, selector: PlanSelector):
        r = selector.select_sync("这个查询需要子查询来处理")
        assert r.plan_id == "gensql_agentic"

    # ── Window functions → gensql_agentic ──────────────────────────

    def test_row_number(self, selector: PlanSelector):
        r = selector.select_sync(
            "SELECT ROW_NUMBER() OVER (PARTITION BY dept ORDER BY salary DESC)"
        )
        assert r.plan_id == "gensql_agentic"

    def test_rank(self, selector: PlanSelector):
        r = selector.select_sync("RANK() OVER (ORDER BY score)")
        assert r.plan_id == "gensql_agentic"

    def test_dense_rank(self, selector: PlanSelector):
        r = selector.select_sync("DENSE_RANK() OVER (PARTITION BY region)")
        assert r.plan_id == "gensql_agentic"

    def test_case_when(self, selector: PlanSelector):
        r = selector.select_sync("SELECT CASE WHEN amount > 100 THEN 'high' ELSE 'low' END")
        assert r.plan_id == "gensql_agentic"

    # ── Chinese keywords → gensql_agentic ──────────────────────────

    def test_chinese_join_keywords(self, selector: PlanSelector):
        cases = [
            "关联查询用户和订单",
            "多表联合查询",
            "跨表分析销售数据",
            "连接两个表",
        ]
        for q in cases:
            r = selector.select_sync(q)
            assert r.plan_id == "gensql_agentic", f"Failed for: {q}"

    def test_chinese_complex_keywords(self, selector: PlanSelector):
        cases = [
            "复杂分析客户行为",
            "对比去年和今年的销售额",
            "同比分析",
            "环比增长",
            "计算占比",
        ]
        for q in cases:
            r = selector.select_sync(q)
            assert r.plan_id == "gensql_agentic", f"Failed for: {q}"

    # ── Simple patterns → ez_query ─────────────────────────────────

    def test_simple_select_star(self, selector: PlanSelector):
        r = selector.select_sync("SELECT * FROM users")
        assert r.plan_id == "ez_query"

    def test_simple_column_list(self, selector: PlanSelector):
        r = selector.select_sync("SELECT name, email FROM users")
        assert r.plan_id == "ez_query"

    def test_chinese_list_request(self, selector: PlanSelector):
        cases = [
            "查看用户表",
            "显示订单数据",
            "列出产品列表",
            "show all customers",
            "list products",
            "display sales table",
            "get user data",
        ]
        for q in cases:
            r = selector.select_sync(q)
            assert r.plan_id == "ez_query", f"Failed for: {q}"

    def test_simple_count(self, selector: PlanSelector):
        r = selector.select_sync("COUNT(*) FROM orders")
        assert r.plan_id == "ez_query"

    def test_simple_question(self, selector: PlanSelector):
        r = selector.select_sync("what is the total sales")
        assert r.plan_id == "ez_query"

    def test_chinese_simple_question(self, selector: PlanSelector):
        cases = [
            "什么是最畅销的产品",
            "哪个客户订单最多",
            "多少用户注册了",
            "怎么查询销售数据",
        ]
        for q in cases:
            r = selector.select_sync(q)
            assert r.plan_id == "ez_query", f"Failed for: {q}"

    # ── No match → default ─────────────────────────────────────────

    def test_no_match_falls_to_default(self, selector: PlanSelector):
        r = selector.select_sync("find me something interesting")
        assert r.plan_id == DEFAULT_PLAN
        assert r.layer == "default"

    def test_empty_query(self, selector: PlanSelector):
        r = selector.select_sync("")
        assert r.plan_id == DEFAULT_PLAN
        assert r.layer == "default"

    def test_whitespace_only(self, selector: PlanSelector):
        r = selector.select_sync("   \t  \n  ")
        assert r.plan_id == DEFAULT_PLAN
        assert r.layer == "default"


# ── L1 rule ordering tests ────────────────────────────────────────────


class TestL1RulePriority:
    """Complex patterns take priority over simple ones."""

    @pytest.fixture
    def selector(self) -> PlanSelector:
        return _make_selector()

    def test_join_wins_over_simple_list(self, selector: PlanSelector):
        """JOIN takes priority even if query starts with 'show'."""
        r = selector.select_sync("show all orders JOIN users")
        assert r.plan_id == "gensql_agentic"

    def test_group_by_wins_over_question(self, selector: PlanSelector):
        r = selector.select_sync("what is the total GROUP BY region")
        assert r.plan_id == "gensql_agentic"

    def test_window_wins_over_show(self, selector: PlanSelector):
        r = selector.select_sync("show ROW_NUMBER() OVER (ORDER BY x)")
        assert r.plan_id == "gensql_agentic"


# ── Plan existence filtering tests ────────────────────────────────────


class TestPlanExistenceFiltering:
    """Rules for non-existent plans are silently skipped."""

    def test_no_loader_all_plans_default(self):
        """Without a PlanLoader, only DEFAULT_PLAN is 'available'."""
        selector = _make_selector(with_loader=False)
        # All L1 rules reference ez_query or gensql_agentic.
        # gensql_agentic IS the DEFAULT_PLAN, so it should match.
        r = selector.select_sync("SELECT * FROM users JOIN orders")
        assert r.plan_id == "gensql_agentic"

    def test_no_loader_ez_query_unavailable(self):
        """Without PlanLoader, ez_query is not in available set."""
        selector = _make_selector(with_loader=False)
        # This would normally match ez_query, but ez_query is not available
        r = selector.select_sync("SELECT * FROM users")
        assert r.plan_id == DEFAULT_PLAN

    def test_with_loader_both_available(self):
        """With real PlanLoader pointing to plans/, both plans exist."""
        selector = _make_selector(with_loader=True)
        r = selector.select_sync("SELECT * FROM users")
        assert r.plan_id == "ez_query"


# ── Sync mode tests ───────────────────────────────────────────────────


class TestSelectSync:
    """Tests for the synchronous select_sync() wrapper."""

    @pytest.fixture
    def selector(self) -> PlanSelector:
        return _make_selector()

    def test_sync_uses_l1_only(self, selector: PlanSelector):
        """select_sync uses L1 rules only, skipping L2/L3."""
        r = selector.select_sync("JOIN orders and users")
        assert r.plan_id == "gensql_agentic"
        assert r.layer == "L1"

    def test_sync_fallback(self, selector: PlanSelector):
        """When no L1 rule matches, sync returns default."""
        r = selector.select_sync("some ambiguous query text")
        assert r.plan_id == DEFAULT_PLAN
        assert r.layer == "default"
        assert "Sync mode" in r.reason
        assert r.confidence == 0.3


# ── Async select tests ────────────────────────────────────────────────


class TestAsyncSelect:
    """Tests for the async select() method."""

    @pytest.fixture
    def selector(self) -> PlanSelector:
        return _make_selector()

    @pytest.mark.anyio
    async def test_async_select_l1_match(self, selector: PlanSelector):
        r = await selector.select("SELECT region, SUM(amount) FROM orders GROUP BY region")
        assert r.plan_id == "gensql_agentic"
        assert r.layer == "L1"
        assert r.confidence == 0.9

    @pytest.mark.anyio
    async def test_async_select_fallback(self, selector: PlanSelector):
        r = await selector.select("some random text without any patterns")
        assert r.plan_id == DEFAULT_PLAN
        assert r.layer == "default"

    @pytest.mark.anyio
    async def test_async_select_with_domain(self, selector: PlanSelector):
        """Domain parameter is accepted but currently unused."""
        r = await selector.select("JOIN orders and users", domain="ecommerce")
        assert r.plan_id == "gensql_agentic"


# ── SelectionResult metadata tests ────────────────────────────────────


class TestSelectionResultMetadata:
    """Verify metadata fields are correctly populated."""

    @pytest.fixture
    def selector(self) -> PlanSelector:
        return _make_selector()

    def test_alternatives_populated(self, selector: PlanSelector):
        r = selector.select_sync("SELECT * FROM orders JOIN users ON orders.uid = users.id")
        assert r.plan_id == "gensql_agentic"
        assert "ez_query" in r.alternatives
        assert r.plan_id not in r.alternatives

    def test_reason_is_human_readable(self, selector: PlanSelector):
        r = selector.select_sync("JOIN a and b")
        assert len(r.reason) > 0
        assert isinstance(r.reason, str)

    def test_confidence_range(self, selector: PlanSelector):
        r = selector.select_sync("JOIN a and b")
        assert 0.0 <= r.confidence <= 1.0


# ── L1_RULES constant tests ───────────────────────────────────────────


class TestL1RulesConstant:
    """Verify the L1_RULES constant is well-formed."""

    def test_all_rules_have_valid_structure(self):
        for rule in L1_RULES:
            assert len(rule) == 3, f"Rule should be (plan_id, pattern, reason): {rule}"
            plan_id, pattern, reason = rule
            assert isinstance(plan_id, str) and plan_id
            assert isinstance(pattern, str) and pattern
            assert isinstance(reason, str) and reason

    def test_rules_are_ordered_complex_first(self):
        """Complex patterns (JOIN, GROUP BY) come before simple ones."""
        # Find indices of key patterns
        join_idx = next(i for i, r in enumerate(L1_RULES) if "JOIN" in r[1])
        simple_idx = next(i for i, r in enumerate(L1_RULES)
                          if "ez_query" in r[0] and "FROM" in r[1])
        assert join_idx < simple_idx, "JOIN rules should precede simple FROM/ez_query rules"

    def test_all_patterns_compile(self):
        """Every L1 pattern must be a valid regex."""
        import re
        for _, pattern, _ in L1_RULES:
            try:
                re.compile(pattern)
            except re.error as e:
                pytest.fail(f"Invalid regex '{pattern}': {e}")

    def test_all_plan_ids_are_known(self):
        """All L1_RULES reference known plan IDs."""
        valid = {"ez_query", "gensql_agentic", "reflection", "chat_agentic",
                  "explore", "metric_query"}
        for plan_id, _, _ in L1_RULES:
            assert plan_id in valid, f"Unknown plan_id in L1_RULES: {plan_id}"


# ── Edge cases ────────────────────────────────────────────────────────


class TestPlanSelectorEdgeCases:
    """Edge case and boundary tests."""

    def test_very_long_query(self):
        selector = _make_selector()
        long_query = "SELECT " + ", ".join(f"col{i}" for i in range(100)) + " FROM big_table"
        r = selector.select_sync(long_query)
        # Should not crash — either matches a rule or falls back
        assert r.plan_id in {DEFAULT_PLAN, "ez_query", "gensql_agentic"}

    def test_sql_injection_attempt(self):
        """SQL injection patterns should not crash the selector."""
        selector = _make_selector()
        r = selector.select_sync("'; DROP TABLE users; --")
        assert r.plan_id == DEFAULT_PLAN
        assert r.layer == "default"

    def test_unicode_emoji_query(self):
        selector = _make_selector()
        r = selector.select_sync("📊 销售数据 📈")
        assert r.plan_id == DEFAULT_PLAN

    def test_mixed_chinese_english(self):
        selector = _make_selector()
        r = selector.select_sync("查询 user 和 order 的 JOIN 结果")
        assert r.plan_id == "gensql_agentic"

    def test_selector_without_loader(self):
        selector = _make_selector(with_loader=False)
        r = selector.select_sync("any query here")
        assert r.plan_id == DEFAULT_PLAN
        assert r.layer == "default"

    @pytest.mark.anyio
    async def test_async_select_without_loader(self):
        selector = _make_selector(with_loader=False)
        r = await selector.select("any query here")
        assert r.plan_id == DEFAULT_PLAN
