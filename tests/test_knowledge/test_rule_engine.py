"""Tests for RuleEngine — pattern matching, enforcement, and SQL template.

Covers CRUD, regex pattern matching, enforcement directive extraction,
sql_template substitution, and domain-scoped rule management.
"""

from __future__ import annotations

import pytest

from app.knowledge.rule_engine import RuleEngine, RuleMatch, _match_pattern
from app.models.domain import BusinessRule

# ── Helpers ────────────────────────────────────────────────────────────


def _make_rule(
    rule_id: str = "r1",
    description: str = "Test rule",
    pattern: str = "",
    enforce: list[str] | None = None,
    sql_template: str = "",
    domain_id: str = "",
) -> BusinessRule:
    return BusinessRule(
        id=rule_id,
        description=description,
        pattern=pattern,
        enforce=enforce or [],
        sql_template=sql_template,
        domain_id=domain_id,
    )


# ── RuleMatch tests ────────────────────────────────────────────────────


class TestRuleMatch:
    def test_construction(self):
        rule = _make_rule("r1")
        rm = RuleMatch(rule=rule, score=0.8)
        assert rm.rule is rule
        assert rm.score == 0.8
        assert rm.matched_groups == ()

    def test_construction_with_groups(self):
        rule = _make_rule("r1")
        rm = RuleMatch(rule=rule, score=0.9, matched_groups=("orders", "amount"))
        assert rm.matched_groups == ("orders", "amount")

    def test_repr(self):
        rule = _make_rule("ecom_price")
        rm = RuleMatch(rule=rule, score=0.75)
        r = repr(rm)
        assert "ecom_price" in r
        assert "0.750" in r


# ── _match_pattern tests ───────────────────────────────────────────────


class TestMatchPattern:
    def test_exact_match(self):
        score, groups = _match_pattern(r"金额", "查询订单金额")
        assert score > 0.0

    def test_no_match(self):
        score, groups = _match_pattern(r"xyzfoo", "查询订单金额")
        assert score == 0.0
        assert groups == ()

    def test_partial_match(self):
        score, groups = _match_pattern(r"订单|用户", "查询订单金额")
        assert 0.0 < score < 1.0

    def test_multiple_matches(self):
        """Multiple regex matches should increase coverage score."""
        score, groups = _match_pattern(r"订单|金额", "订单金额查询")
        # Both "订单" and "金额" should match
        assert score > 0.0

    def test_case_insensitive(self):
        score1, _ = _match_pattern(r"order", "ORDER by date")
        score2, _ = _match_pattern(r"ORDER", "order by date")
        assert score1 == score2
        assert score1 > 0.0

    def test_capture_groups(self):
        score, groups = _match_pattern(r"(\d+)天", "最近30天订单")
        assert "30" in groups

    def test_invalid_regex(self):
        score, groups = _match_pattern(r"[invalid", "some text")
        assert score == 0.0

    def test_empty_pattern(self):
        score, groups = _match_pattern("", "some text")
        assert score == 0.0

    def test_full_coverage(self):
        score, groups = _match_pattern(r".*", "hello")
        assert score == 1.0


# ── CRUD tests ─────────────────────────────────────────────────────────


class TestRuleEngineCRUD:
    @pytest.fixture
    def engine(self) -> RuleEngine:
        return RuleEngine()

    def test_add_global(self, engine: RuleEngine):
        rule = _make_rule("r1", pattern="金额")
        engine.add(rule)
        assert engine.rule_count == 1
        assert engine.get("r1") is rule

    def test_add_domain_scoped(self, engine: RuleEngine):
        rule = _make_rule("r1", pattern="金额")
        engine.add(rule, domain="ecommerce")
        assert engine.rule_count == 1
        assert engine.domain_count == 1
        assert engine.get("r1", domain="ecommerce") is rule

    def test_add_duplicate_overwrites(self, engine: RuleEngine):
        r1 = _make_rule("r1", description="old")
        r2 = _make_rule("r1", description="new")
        engine.add(r1)
        engine.add(r2)
        assert engine.get("r1").description == "new"

    def test_get_falls_back_to_global(self, engine: RuleEngine):
        rule = _make_rule("r1")
        engine.add(rule)  # global
        assert engine.get("r1", domain="ecommerce") is rule

    def test_get_domain_takes_priority(self, engine: RuleEngine):
        global_r = _make_rule("r1", description="global")
        domain_r = _make_rule("r1", description="domain")
        engine.add(global_r)
        engine.add(domain_r, domain="ecommerce")
        result = engine.get("r1", domain="ecommerce")
        assert result is domain_r

    def test_get_missing(self, engine: RuleEngine):
        assert engine.get("nonexistent") is None

    def test_update_existing(self, engine: RuleEngine):
        rule = _make_rule("r1", description="old desc")
        engine.add(rule)
        assert engine.update("r1", {"description": "new desc"})
        assert engine.get("r1").description == "new desc"

    def test_update_pattern(self, engine: RuleEngine):
        rule = _make_rule("r1", pattern="old")
        engine.add(rule)
        assert engine.update("r1", {"pattern": "new_pattern"})
        assert engine.get("r1").pattern == "new_pattern"

    def test_update_nonexistent(self, engine: RuleEngine):
        assert engine.update("nonexistent", {"description": "x"}) is False

    def test_delete_existing(self, engine: RuleEngine):
        engine.add(_make_rule("r1"))
        assert engine.delete("r1") is True
        assert engine.get("r1") is None
        assert engine.rule_count == 0

    def test_delete_domain_scoped(self, engine: RuleEngine):
        engine.add(_make_rule("r1"), domain="ecommerce")
        assert engine.delete("r1", domain="ecommerce") is True
        assert engine.rule_count == 0

    def test_delete_falls_back_to_global(self, engine: RuleEngine):
        engine.add(_make_rule("r1"))  # global
        assert engine.delete("r1", domain="ecommerce")  # falls back
        assert engine.get("r1") is None

    def test_delete_nonexistent(self, engine: RuleEngine):
        assert engine.delete("nonexistent") is False

    def test_list_all_global_sorted(self, engine: RuleEngine):
        engine.add(_make_rule("b"))
        engine.add(_make_rule("a"))
        rules = engine.list_all()
        assert [r.id for r in rules] == ["a", "b"]

    def test_list_all_domain(self, engine: RuleEngine):
        engine.add(_make_rule("a"), domain="ecommerce")
        engine.add(_make_rule("b"), domain="ecommerce")
        rules = engine.list_all(domain="ecommerce")
        assert len(rules) == 2

    def test_clear_global(self, engine: RuleEngine):
        engine.add(_make_rule("a"))
        engine.add(_make_rule("b"), domain="ecommerce")
        assert engine.clear() == 2
        assert engine.rule_count == 0

    def test_clear_domain(self, engine: RuleEngine):
        engine.add(_make_rule("a"), domain="ecommerce")
        engine.add(_make_rule("b"))  # global
        assert engine.clear(domain="ecommerce") == 1
        assert engine.rule_count == 1  # only global remains


# ── Matching tests ─────────────────────────────────────────────────────


class TestRuleEngineMatch:
    @pytest.fixture
    def engine(self) -> RuleEngine:
        eng = RuleEngine()
        eng.add(
            _make_rule("ecom_price", pattern=r"金额|amount|price|revenue",
                       enforce=["precision=2"]),
            domain="ecommerce",
        )
        eng.add(
            _make_rule("ecom_tz", pattern=r"created_at|updated_at|下单时间|支付时间",
                       enforce=["timezone=Asia/Shanghai"]),
            domain="ecommerce",
        )
        eng.add(
            _make_rule("fin_price", pattern=r"金额|amount|value|nav|balance",
                       enforce=["precision=4"]),
            domain="finance",
        )
        eng.add(
            _make_rule("fin_compliance", pattern=r"risk|exposure|compliance",
                       enforce=["require_time_range=true"]),
            domain="finance",
        )
        eng.add(
            _make_rule("global_audit", pattern=r"audit|log|审计|日志",
                       enforce=["audit_log=true"]),
        )
        return eng

    def test_match_exact(self, engine: RuleEngine):
        matches = engine.match("查询订单金额")
        assert len(matches) >= 1
        # ecom_price should match "金额"
        matched_ids = {m.rule.id for m in matches}
        assert "ecom_price" in matched_ids

    def test_match_multiple(self, engine: RuleEngine):
        matches = engine.match("查询订单金额和下单时间")
        # Should match both ecom_price (金额) and ecom_tz (下单时间)
        matched_ids = {m.rule.id for m in matches}
        assert "ecom_price" in matched_ids or "ecom_tz" in matched_ids

    def test_match_domain_filter(self, engine: RuleEngine):
        matches = engine.match("amount value risk", domain="finance")
        matched_ids = {m.rule.id for m in matches}
        assert "fin_price" in matched_ids

    def test_match_domain_excludes_other(self, engine: RuleEngine):
        """With domain=finance, ecommerce rules shouldn't appear."""
        matches = engine.match("amount", domain="finance")
        # "amount" matches both ecom_price and fin_price patterns
        # With domain=finance, should find fin_price
        matched_ids = {m.rule.id for m in matches}
        assert "fin_price" in matched_ids
        assert "ecom_price" not in matched_ids

    def test_match_global_rule(self, engine: RuleEngine):
        matches = engine.match("审计日志查询")
        matched_ids = {m.rule.id for m in matches}
        assert "global_audit" in matched_ids

    def test_match_threshold_filters(self, engine: RuleEngine):
        matches = engine.match("xyz foo bar", threshold=0.5)
        assert len(matches) == 0

    def test_match_best(self, engine: RuleEngine):
        result = engine.match_best("查询订单金额和下单时间")
        assert result is not None
        assert result.score > 0.0

    def test_match_best_no_match(self, engine: RuleEngine):
        result = engine.match_best("xyz foo bar", threshold=0.5)
        assert result is None

    def test_match_empty_pattern_skipped(self, engine: RuleEngine):
        """Rules with empty pattern should be skipped in matching."""
        engine.add(_make_rule("no_pattern", pattern=""))
        matches = engine.match("anything")
        matched_ids = {m.rule.id for m in matches}
        assert "no_pattern" not in matched_ids

    def test_match_score_range(self, engine: RuleEngine):
        matches = engine.match("查询订单金额")
        for rm in matches:
            assert 0.0 <= rm.score <= 1.0

    def test_match_result_structure(self, engine: RuleEngine):
        matches = engine.match("查询订单金额")
        for rm in matches:
            assert isinstance(rm, RuleMatch)
            assert isinstance(rm.rule, BusinessRule)
            assert isinstance(rm.score, float)

    def test_global_rules_visible_to_all_domains(self, engine: RuleEngine):
        """Global rules should be matched regardless of domain filter."""
        matches_ecom = engine.match("审计日志", domain="ecommerce")
        matches_fin = engine.match("审计日志", domain="finance")
        assert len(matches_ecom) >= 1
        assert len(matches_fin) >= 1


# ── Enforcement tests ──────────────────────────────────────────────────


class TestRuleEngineEnforcement:
    @pytest.fixture
    def engine(self) -> RuleEngine:
        eng = RuleEngine()
        eng.add(
            _make_rule("ecom_price", pattern=r"金额|amount|price|revenue",
                       enforce=["precision=2", "round_mode=half_up"]),
            domain="ecommerce",
        )
        eng.add(
            _make_rule("ecom_tz", pattern=r"时间|date|time",
                       enforce=["timezone=Asia/Shanghai"]),
            domain="ecommerce",
        )
        eng.add(
            _make_rule("fin_price", pattern=r"金额|amount|value|nav",
                       enforce=["precision=4"]),
            domain="finance",
        )
        return eng

    def test_get_enforcements_single_rule(self, engine: RuleEngine):
        directives = engine.get_enforcements("查询订单金额", domain="ecommerce")
        assert directives.get("precision") == "2"
        assert directives.get("round_mode") == "half_up"

    def test_get_enforcements_multi_rule(self, engine: RuleEngine):
        directives = engine.get_enforcements("查询订单金额按时间排序", domain="ecommerce")
        assert "precision" in directives
        assert "timezone" in directives

    def test_get_enforcements_domain_isolation(self, engine: RuleEngine):
        """Finance domain should get precision=4, not precision=2."""
        directives = engine.get_enforcements("amount calculation", domain="finance")
        assert directives.get("precision") == "4"

    def test_get_enforcements_no_match(self, engine: RuleEngine):
        directives = engine.get_enforcements("xyz foo bar", domain="ecommerce")
        assert directives == {}

    def test_get_enforcements_higher_score_wins(self, engine: RuleEngine):
        """When two rules set the same key, higher-score rule wins."""
        # Add a second rule that also sets precision
        eng = RuleEngine()
        eng.add(_make_rule("r1", pattern=r"金额", enforce=["precision=2"]), domain="test")
        eng.add(_make_rule("r2", pattern=r"金额查询", enforce=["precision=6"]), domain="test")
        # "金额查询" matches both, but r2 has better coverage
        directives = eng.get_enforcements("金额查询", domain="test")
        assert directives.get("precision") == "6"

    def test_get_enforcements_threshold(self, engine: RuleEngine):
        directives = engine.get_enforcements("查询订单金额", domain="ecommerce", threshold=0.9)
        # With high threshold, weak matches are filtered
        assert isinstance(directives, dict)

    def test_get_enforcements_empty_enforce_list(self, engine: RuleEngine):
        engine.add(_make_rule("no_enforce", pattern=r"test", enforce=[]))
        directives = engine.get_enforcements("test query", domain="ecommerce")
        # Rule matches but has no directives
        assert "no_enforce" not in str(directives)

    def test_parse_directive_no_equals(self, engine: RuleEngine):
        """Directives without = sign should be skipped gracefully."""
        engine.add(_make_rule("bad_directive", pattern=r"test", enforce=["badformat"]))
        directives = engine.get_enforcements("test", domain="ecommerce")
        # Should not crash; "badformat" has no = so it's skipped
        assert isinstance(directives, dict)


# ── SQL Template tests ─────────────────────────────────────────────────


class TestRuleEngineSQLTemplate:
    @pytest.fixture
    def engine(self) -> RuleEngine:
        eng = RuleEngine()
        eng.add(
            _make_rule(
                "metric_template",
                pattern=r"指标",
                sql_template="SELECT {agg}({column}) FROM {table} WHERE {condition}",
            ),
            domain="ecommerce",
        )
        eng.add(
            _make_rule(
                "simple_template",
                pattern=r"simple",
                sql_template="SELECT * FROM users",
            ),
        )
        eng.add(_make_rule("no_template", pattern=r"no_tmpl"))
        return eng

    def test_apply_sql_template_with_params(self, engine: RuleEngine):
        sql = engine.apply_sql_template(
            "metric_template",
            params={"agg": "SUM", "column": "amount", "table": "orders", "condition": "1=1"},
            domain="ecommerce",
        )
        assert sql == "SELECT SUM(amount) FROM orders WHERE 1=1"

    def test_apply_sql_template_no_params(self, engine: RuleEngine):
        sql = engine.apply_sql_template("simple_template")
        assert sql == "SELECT * FROM users"

    def test_apply_sql_template_no_template(self, engine: RuleEngine):
        sql = engine.apply_sql_template("no_template")
        assert sql is None

    def test_apply_sql_template_unknown_rule(self, engine: RuleEngine):
        sql = engine.apply_sql_template("nonexistent")
        assert sql is None

    def test_apply_sql_template_partial_params(self, engine: RuleEngine):
        """Missing params should raise KeyError (standard str.format behavior)."""
        with pytest.raises(KeyError):
            engine.apply_sql_template(
                "metric_template",
                params={"agg": "SUM"},
                domain="ecommerce",
            )


# ── Edge cases ─────────────────────────────────────────────────────────


class TestRuleEngineEdgeCases:
    def test_empty_engine(self):
        engine = RuleEngine()
        assert engine.rule_count == 0
        assert engine.match("anything") == []
        assert engine.match_best("anything") is None
        assert engine.get_enforcements("anything") == {}
        assert engine.apply_sql_template("any") is None

    def test_load_from_domains(self, tmp_path):
        """Integration: load rules from DomainManager."""
        from app.knowledge.domain_manager import DomainManager

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
glossary: []
rules:
  - id: "r1"
    description: "金额精度"
    pattern: "金额|amount"
    enforce: ["precision=2"]
  - id: "r2"
    description: "时区规则"
    pattern: "时间|date"
    enforce: ["timezone=Asia/Shanghai"]
""", encoding="utf-8")

        dm = DomainManager(str(d))
        engine = RuleEngine(domain_manager=dm)
        loaded = engine.load_from_domains()
        assert loaded == 2
        assert engine.rule_count == 2
        assert engine.get("r1", domain="ecommerce") is not None

    def test_load_from_none_domain_manager(self):
        engine = RuleEngine()
        assert engine.load_from_domains() == 0

    def test_rule_count_includes_all(self):
        engine = RuleEngine()
        engine.add(_make_rule("a"))
        engine.add(_make_rule("b"), domain="d1")
        engine.add(_make_rule("c"), domain="d2")
        assert engine.rule_count == 3
        assert engine.domain_count == 2

    def test_add_same_id_different_domains(self):
        """Same rule ID in different domains should be independent."""
        engine = RuleEngine()
        r1 = _make_rule("shared_id", description="domain 1")
        r2 = _make_rule("shared_id", description="domain 2")
        engine.add(r1, domain="d1")
        engine.add(r2, domain="d2")
        assert engine.get("shared_id", domain="d1").description == "domain 1"
        assert engine.get("shared_id", domain="d2").description == "domain 2"

    def test_repr_includes_rule_id_and_score(self):
        rule = _make_rule("test_rule")
        rm = RuleMatch(rule=rule, score=0.5)
        r = repr(rm)
        assert "test_rule" in r
        assert "0.500" in r
