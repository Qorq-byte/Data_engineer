"""Learned rule store: review persistence, domain scoping and matching."""

from __future__ import annotations

import pytest

from app.learning.rule_registry import LearnedRuleRegistry, LearnedRuleStore, RuleRow


@pytest.fixture
def registry(tmp_path) -> LearnedRuleRegistry:
    return LearnedRuleRegistry(LearnedRuleStore(tmp_path / "rules.db"))


def _approve(registry: LearnedRuleRegistry, rule_id: str = "cr_1", **payload):
    values = {
        "description": "订单查询需要 WHERE",
        "pattern": "订单",
        "suggested_enforcement": {"require_where": "true"},
        "domain": "",
        "confidence": 0.8,
        "source_count": 3,
    }
    values.update(payload)
    assert registry.record_review(rule_id, "approved", values) is True


def test_approved_rule_becomes_a_business_rule(registry):
    _approve(registry)

    rules = registry.business_rules()

    assert [rule.id for rule in rules] == ["cr_1"]
    assert rules[0].description == "订单查询需要 WHERE"
    assert rules[0].enforce == ["require_where=true"]


def test_rejected_rule_is_not_returned(registry):
    _approve(registry)
    registry.record_review("cr_1", "rejected", {"description": "订单查询需要 WHERE"})

    assert registry.business_rules() == []


def test_domain_rules_do_not_leak_across_domains(registry):
    _approve(registry, "cr_global", domain="")
    _approve(registry, "cr_finance", domain="finance")

    finance = {rule.id for rule in registry.business_rules("finance")}
    sales = {rule.id for rule in registry.business_rules("sales")}

    assert finance == {"cr_global", "cr_finance"}
    assert sales == {"cr_global"}


def test_partial_review_preserves_existing_fields(registry):
    _approve(registry)
    registry.record_review("cr_1", "approved", {"description": "只改描述"})

    rule = registry.store.get("cr_1")

    assert rule is not None
    assert rule.description == "只改描述"
    assert rule.pattern == "订单"
    assert rule.enforce == ["require_where=true"]


def test_unknown_rule_without_details_is_still_persisted(registry):
    assert registry.record_review("cr_new", "approved", None) is True

    rule = registry.store.get("cr_new")
    assert rule is not None
    assert rule.pattern == ""


def test_match_respects_patterns(registry):
    _approve(registry, "cr_orders", pattern="订单")
    _approve(registry, "cr_refund", pattern="退款")
    _approve(registry, "cr_always", pattern="")

    rules = registry.business_rules("")
    matched = {rule.id for rule in registry.match("列出订单", rules)}

    assert matched == {"cr_orders", "cr_always"}


def test_invalid_regex_falls_back_to_substring():
    from app.models.domain import BusinessRule

    rule = BusinessRule(id="cr_bad", description="bad", pattern="([")

    assert LearnedRuleRegistry.match("含有 ([ 的查询", [rule]) == [rule]


def test_cache_is_invalidated_on_review(registry):
    _approve(registry)
    assert registry.business_rules()

    registry.record_review("cr_1", "rejected", {"description": "订单查询需要 WHERE"})

    assert registry.business_rules() == []


def test_store_survives_reopen(tmp_path):
    path = tmp_path / "rules.db"
    first = LearnedRuleRegistry(LearnedRuleStore(path))
    _approve(first)
    first.store.close()

    reopened = LearnedRuleRegistry(LearnedRuleStore(path))

    assert [rule.rule_id for rule in reopened.approved_rules()] == ["cr_1"]


def test_upsert_is_idempotent(tmp_path):
    store = LearnedRuleStore(tmp_path / "rules.db")
    rule = RuleRow(rule_id="cr_1", status="approved", description="d", pattern="p")

    store.upsert(rule)
    store.upsert(rule)

    assert len(store.list_rules()) == 1
