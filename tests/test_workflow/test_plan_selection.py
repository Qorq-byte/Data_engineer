"""Automatic plan selection: catalog filtering, rules, history and fallback."""

from __future__ import annotations

import pytest

from app.workflow import plan_selection


@pytest.fixture(autouse=True)
def isolated_history(tmp_path, monkeypatch):
    monkeypatch.setenv("DE_PLAN_HISTORY", str(tmp_path / "plan_history.db"))
    plan_selection.reset_runtime()
    yield
    plan_selection.reset_runtime()


def test_auto_selectable_set_stays_conservative():
    assert plan_selection.selectable_plans() == ["request_query", "ez_query"]


def test_catalog_state_reports_the_blocker():
    executable, blocker = plan_selection.plan_catalog_state("metric_query")
    assert executable is False
    assert "metric" in blocker.lower()

    executable, blocker = plan_selection.plan_catalog_state("ez_query")
    assert executable is True
    assert blocker == ""


def test_unknown_plan_is_not_executable():
    executable, blocker = plan_selection.plan_catalog_state("no_such_plan")
    assert executable is False
    assert "not found" in blocker


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("统计每个地区的订单数并关联用户表", "request_query"),
        ("SELECT u.id FROM users u JOIN orders o ON o.user_id = u.id", "request_query"),
        ("列出订单", "ez_query"),
        ("show products", "ez_query"),
    ],
)
async def test_rules_route_to_expected_plans(query, expected):
    decision = await plan_selection.select_plan(query)

    assert decision.plan_id == expected
    assert decision.layer == "L1"


async def test_alias_from_selector_is_explained():
    decision = await plan_selection.select_plan("统计每个地区的订单数并关联用户表")

    assert decision.plan_id == "request_query"
    assert "selector: gensql_agentic" in decision.reason


async def test_unmatched_query_falls_back_within_the_selectable_set():
    decision = await plan_selection.select_plan("orders please")

    assert decision.plan_id in plan_selection.selectable_plans()
    assert decision.layer in {"default", "filtered"}


async def test_history_is_reused_on_the_second_request():
    query = "orders please"
    first = await plan_selection.select_plan(query)
    plan_selection.record_decision(query, first.plan_id, domain="", success=True)

    second = await plan_selection.select_plan(query)

    assert second.layer == "L2"
    assert second.plan_id == first.plan_id


async def test_failed_history_does_not_win():
    query = "orders please"
    first = await plan_selection.select_plan(query)
    plan_selection.record_decision(query, first.plan_id, domain="", success=False)

    second = await plan_selection.select_plan(query)

    assert second.layer != "L2"


async def test_selector_verdict_outside_the_set_is_reported_not_executed(monkeypatch):
    from app.workflow.plan_selector import SelectionResult

    async def fake_select(self, query_text, domain=None):
        return SelectionResult(plan_id="chat_agentic", layer="L3", reason="llm pick")

    monkeypatch.setattr(plan_selection.PlanSelector, "select", fake_select)

    decision = await plan_selection.select_plan("anything")

    assert decision.plan_id == "request_query"
    assert decision.layer == "filtered"
    assert "chat_agentic" in decision.reason
    assert "chat_agentic" in decision.alternatives
