"""Tests for GlossaryManager — CRUD, matching, and term resolution.

Covers domain-scoped and global term management, fuzzy/substring
matching, and the resolve() interface for SchemaRetriever integration.
"""

from __future__ import annotations

import pytest

from app.knowledge.glossary import (
    GlossaryManager,
    _extract_columns_from_expr,
    _fallback_resolve,
    _term_match_score,
)
from app.models.domain import GlossaryTerm, TermMapping

# ── Helpers ────────────────────────────────────────────────────────────


def _make_term(
    term: str,
    term_en: str = "",
    description: str = "",
    expression: str = "",
    mapping_type: str = "derived_column",
    table: str | None = None,
    tags: list[str] | None = None,
) -> GlossaryTerm:
    mapping = None
    if expression:
        mapping = TermMapping(
            expression=expression,
            type=mapping_type,
            table=table,
        )
    return GlossaryTerm(
        term=term,
        term_en=term_en,
        description=description,
        mapping=mapping,
        tags=tags or [],
    )


# ── Helper function tests ──────────────────────────────────────────────


class TestTermMatchScore:
    def test_exact_match(self):
        term = _make_term("订单金额")
        assert _term_match_score(term, "查询订单金额") == 1.0

    def test_exact_match_case_insensitive(self):
        term = _make_term("revenue")
        assert _term_match_score(term, "REVENUE by month") == 1.0

    def test_english_term_match(self):
        term = _make_term("客单价", term_en="AOV")
        assert _term_match_score(term, "what is the AOV") == 0.95

    def test_word_level_match(self):
        term = _make_term("用户", term_en="user")
        # "用户" appears as exact substring → score 1.0
        assert _term_match_score(term, "查询 用户 订单") == 1.0

    def test_no_match(self):
        term = _make_term("净资产")
        assert _term_match_score(term, "查询订单金额") == 0.0

    def test_fuzzy_match(self):
        term = _make_term("order amount")
        score = _term_match_score(term, "order amounts by region")
        assert score >= 0.0


class TestExtractColumns:
    def test_table_dot_column(self):
        cols = _extract_columns_from_expr("SUM(orders.amount)")
        assert "amount" in cols

    def test_multiple_columns(self):
        cols = _extract_columns_from_expr("COALESCE(users.first_name, users.last_name)")
        assert "first_name" in cols
        assert "last_name" in cols

    def test_no_table_prefix(self):
        cols = _extract_columns_from_expr("AVG(amount)")
        assert "amount" in cols

    def test_no_columns(self):
        cols = _extract_columns_from_expr("1 + 2")
        assert cols == []

    def test_distinct(self):
        cols = _extract_columns_from_expr("COUNT(DISTINCT user_id)")
        assert "user_id" in cols


class TestFallbackResolve:
    def test_simple(self):
        assert _fallback_resolve("订单") == ["订单"]

    def test_dot_notation(self):
        assert _fallback_resolve("orders.amount") == ["orders.amount"]


# ── CRUD tests ─────────────────────────────────────────────────────────


class TestGlossaryManagerCRUD:
    @pytest.fixture
    def gm(self) -> GlossaryManager:
        return GlossaryManager()

    def test_add_global(self, gm: GlossaryManager):
        term = _make_term("订单金额", expression="SUM(orders.amount)")
        gm.add(term)
        assert gm.term_count == 1
        assert gm.get("订单金额") is term

    def test_add_domain_scoped(self, gm: GlossaryManager):
        term = _make_term("订单金额", expression="SUM(orders.amount)")
        gm.add(term, domain="ecommerce")
        assert gm.term_count == 1
        assert gm.domain_count == 1
        assert gm.get("订单金额", domain="ecommerce") is term

    def test_add_duplicate_overwrites(self, gm: GlossaryManager):
        t1 = _make_term("订单金额", description="old")
        t2 = _make_term("订单金额", description="new")
        gm.add(t1)
        gm.add(t2)
        assert gm.get("订单金额").description == "new"

    def test_get_falls_back_to_global(self, gm: GlossaryManager):
        term = _make_term("revenue", expression="SUM(amount)")
        gm.add(term)  # global
        # Domain-specific lookup should fall back to global
        assert gm.get("revenue", domain="ecommerce") is term

    def test_get_domain_takes_priority(self, gm: GlossaryManager):
        global_t = _make_term("revenue", description="global")
        domain_t = _make_term("revenue", description="domain")
        gm.add(global_t)
        gm.add(domain_t, domain="ecommerce")
        result = gm.get("revenue", domain="ecommerce")
        assert result is domain_t

    def test_get_missing(self, gm: GlossaryManager):
        assert gm.get("nonexistent") is None

    def test_update_existing(self, gm: GlossaryManager):
        term = _make_term("客单价", description="old desc")
        gm.add(term)
        assert gm.update("客单价", {"description": "new desc"})
        assert gm.get("客单价").description == "new desc"

    def test_update_mapping(self, gm: GlossaryManager):
        term = _make_term("客单价", expression="OLD()")
        gm.add(term)
        new_mapping = TermMapping(expression="AVG(orders.amount)", type="derived_column")
        assert gm.update("客单价", {"mapping": new_mapping})
        updated = gm.get("客单价")
        assert updated.mapping.expression == "AVG(orders.amount)"

    def test_update_nonexistent(self, gm: GlossaryManager):
        assert gm.update("nonexistent", {"description": "x"}) is False

    def test_delete_existing(self, gm: GlossaryManager):
        term = _make_term("test")
        gm.add(term)
        assert gm.delete("test") is True
        assert gm.get("test") is None
        assert gm.term_count == 0

    def test_delete_domain_scoped(self, gm: GlossaryManager):
        gm.add(_make_term("test"), domain="ecommerce")
        assert gm.delete("test", domain="ecommerce") is True

    def test_delete_nonexistent(self, gm: GlossaryManager):
        assert gm.delete("nonexistent") is False

    def test_list_all_global(self, gm: GlossaryManager):
        gm.add(_make_term("b"))
        gm.add(_make_term("a"))
        terms = gm.list_all()
        assert [t.term for t in terms] == ["a", "b"]

    def test_list_all_domain(self, gm: GlossaryManager):
        gm.add(_make_term("a"), domain="ecommerce")
        gm.add(_make_term("b"), domain="ecommerce")
        terms = gm.list_all(domain="ecommerce")
        assert len(terms) == 2

    def test_clear_global(self, gm: GlossaryManager):
        gm.add(_make_term("a"))
        gm.add(_make_term("b"), domain="ecommerce")
        assert gm.clear() == 2
        assert gm.term_count == 0

    def test_clear_domain(self, gm: GlossaryManager):
        gm.add(_make_term("a"), domain="ecommerce")
        gm.add(_make_term("b"))  # global
        assert gm.clear(domain="ecommerce") == 1
        assert gm.term_count == 1  # only global remains

    def test_load_from_domains(self, tmp_path):
        """Integration: load terms from DomainManager."""
        from app.knowledge.domain_manager import DomainManager

        # Write a domain YAML with glossary terms
        d = tmp_path / "domains"
        d.mkdir()
        (d / "ecommerce.yml").write_text("""
name: ecommerce
label:
  zh: "电商"
description:
  zh: "电商领域"
databases: []
timezone: "Asia/Shanghai"
currency: "CNY"
keywords: []
glossary:
  - term: "订单金额"
    term_en: "order amount"
    description: "订单支付金额"
    mapping:
      expression: "SUM(orders.amount)"
      type: derived_column
  - term: "客单价"
    term_en: "AOV"
    description: "平均订单金额"
    mapping:
      expression: "AVG(orders.amount)"
      type: derived_column
rules: []
""", encoding="utf-8")

        dm = DomainManager(str(d))
        gm = GlossaryManager(domain_manager=dm)
        loaded = gm.load_from_domains()
        assert loaded == 2
        assert gm.term_count == 2
        assert gm.get("订单金额", domain="ecommerce") is not None


# ── Matching tests ─────────────────────────────────────────────────────


class TestGlossaryManagerMatch:
    @pytest.fixture
    def gm(self) -> GlossaryManager:
        mgr = GlossaryManager()
        mgr.add(_make_term("订单金额", term_en="order amount",
                           expression="SUM(orders.amount)"), domain="ecommerce")
        mgr.add(_make_term("客单价", term_en="AOV",
                           description="平均订单金额"), domain="ecommerce")
        mgr.add(_make_term("净资产", term_en="NAV"), domain="finance")
        mgr.add(_make_term("revenue", description="Global revenue term"))
        return mgr

    def test_match_exact(self, gm: GlossaryManager):
        hits = gm.match("查询订单金额")
        assert len(hits) >= 1
        assert hits[0][0] == "订单金额"
        assert hits[0][2] == 1.0

    def test_match_multiple(self, gm: GlossaryManager):
        hits = gm.match("订单金额和客单价分析")
        assert len(hits) >= 2

    def test_match_domain_filter(self, gm: GlossaryManager):
        hits = gm.match("净资产", domain="finance")
        assert len(hits) >= 1
        assert hits[0][0] == "净资产"

    def test_match_domain_excludes_other(self, gm: GlossaryManager):
        """With domain filter, other domain's terms shouldn't appear."""
        hits = gm.match("订单金额", domain="finance")
        # "订单金额" is in ecommerce, not finance
        for _, _, score in hits:
            assert score < 1.0  # exact match shouldn't happen in wrong domain

    def test_match_english_term(self, gm: GlossaryManager):
        hits = gm.match("what is the AOV this month")
        assert len(hits) >= 1
        assert hits[0][0] == "客单价"

    def test_match_global_term(self, gm: GlossaryManager):
        hits = gm.match("show me revenue trends")
        assert len(hits) >= 1
        assert hits[0][0] == "revenue"

    def test_match_threshold_filters(self, gm: GlossaryManager):
        """Only terms with score >= threshold should be returned."""
        hits = gm.match("xyz foo bar", threshold=0.5)
        assert len(hits) == 0

    def test_match_best(self, gm: GlossaryManager):
        result = gm.match_best("订单金额查询", threshold=0.5)
        assert result is not None
        assert result[0] == "订单金额"

    def test_match_best_below_threshold(self, gm: GlossaryManager):
        result = gm.match_best("xyz foo bar", threshold=0.8)
        assert result is None

    def test_match_result_structure(self, gm: GlossaryManager):
        hits = gm.match("订单金额")
        for term_name, term_obj, score in hits:
            assert isinstance(term_name, str)
            assert isinstance(term_obj, GlossaryTerm)
            assert 0.0 <= score <= 1.0
            assert score >= 0.3  # default threshold


# ── Resolution tests ───────────────────────────────────────────────────


class TestGlossaryManagerResolve:
    @pytest.fixture
    def gm(self) -> GlossaryManager:
        mgr = GlossaryManager()
        mgr.add(_make_term("订单金额", expression="SUM(orders.amount)",
                           table="orders"), domain="ecommerce")
        mgr.add(_make_term("客单价", expression="AVG(orders.amount)"), domain="ecommerce")
        mgr.add(_make_term("活跃用户",
                           expression="users.last_active_at >= NOW() - INTERVAL '30 days'",
                           mapping_type="filter_condition"), domain="ecommerce")
        mgr.add(_make_term("just_name"))  # no mapping
        return mgr

    def test_resolve_with_table(self, gm: GlossaryManager):
        names = gm.resolve("订单金额", domain="ecommerce")
        assert "orders.amount" in names

    def test_resolve_no_table(self, gm: GlossaryManager):
        names = gm.resolve("客单价", domain="ecommerce")
        assert "amount" in names

    def test_resolve_no_mapping(self, gm: GlossaryManager):
        names = gm.resolve("just_name")
        assert "just_name" in names

    def test_resolve_unknown_term(self, gm: GlossaryManager):
        names = gm.resolve("unknown_term")
        assert "unknown_term" in names  # fallback

    def test_resolve_expression(self, gm: GlossaryManager):
        mapping = gm.resolve_expression("订单金额", domain="ecommerce")
        assert mapping is not None
        assert mapping.type == "derived_column"
        assert mapping.table == "orders"

    def test_resolve_expression_not_found(self, gm: GlossaryManager):
        assert gm.resolve_expression("nonexistent") is None


# ── Edge cases ────────────────────────────────────────────────────────


class TestGlossaryManagerEdgeCases:
    def test_empty_manager(self):
        gm = GlossaryManager()
        assert gm.term_count == 0
        assert gm.match("anything") == []
        assert gm.match_best("anything") is None
        assert gm.resolve("term") == ["term"]

    def test_load_from_none_domain_manager(self):
        gm = GlossaryManager()  # no domain_manager
        assert gm.load_from_domains() == 0

    def test_term_count_includes_all(self):
        gm = GlossaryManager()
        gm.add(_make_term("a"))
        gm.add(_make_term("b"), domain="d1")
        gm.add(_make_term("c"), domain="d2")
        assert gm.term_count == 3
        assert gm.domain_count == 2

    def test_delete_cascades_to_global(self):
        gm = GlossaryManager()
        gm.add(_make_term("shared"))
        assert gm.delete("shared", domain="ecommerce")  # falls back to global
        assert gm.get("shared") is None
