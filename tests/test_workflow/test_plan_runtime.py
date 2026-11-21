"""Plan runtime: capability table, node assembly and request-scoped overrides."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.harness.constraint import ConstraintEnforcer, ConstraintLimits
from app.harness.plan_loader import PlanLoader
from app.workflow.plan_runtime import (
    STAGE_BY_NODE,
    NodeDependencies,
    build_registry,
    executable_plan_ids,
    plan_blocker,
    plan_has_node,
    plan_stages,
    request_overrides,
    stage_for,
)

EXECUTABLE = (
    "chat_agentic",
    "explore",
    "ez_query",
    "gensql_agentic",
    "reflection",
    "request_query",
)


@pytest.fixture
def loader() -> PlanLoader:
    return PlanLoader("app/workflow/plans")


def _body(**overrides):
    values = {
        "link_schema": True,
        "num_candidates": 2,
        "model": "test-model",
        "run_validation": True,
        "execute": True,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _limits(**overrides) -> ConstraintEnforcer:
    values = {
        "max_llm_calls_per_query": 4,
        "max_result_rows": 50,
        "statement_timeout_ms": 1000,
    }
    values.update(overrides)
    return ConstraintEnforcer(ConstraintLimits(**values))


def test_unconfigured_catalog_and_review_preview_are_blocked(loader, monkeypatch):
    from app.config.settings import settings

    monkeypatch.setattr(settings, "candidate_review_enabled", False)
    available = loader.list_available()
    blocked = {plan_id: plan_blocker(plan_id) for plan_id in available if plan_blocker(plan_id)}

    assert set(blocked) == {"metric_query", "candidate_review"}
    assert "metric" in blocked["metric_query"].lower()
    assert set(executable_plan_ids(available)) == set(available) - set(blocked)
    monkeypatch.setattr(settings, "candidate_review_enabled", True)
    assert "candidate_review" in executable_plan_ids(available)


@pytest.mark.parametrize("plan_id", EXECUTABLE)
def test_registry_covers_every_node_in_executable_plans(loader, plan_id):
    plan = loader.load(plan_id)
    registry = build_registry(plan, NodeDependencies())

    for definition in plan.node_order:
        assert definition.node in registry
    # Repeated node types (reflection's generate/execute) share one instance.
    assert len(registry) == len({definition.node for definition in plan.node_order})


def test_unknown_node_is_rejected(loader):
    plan = loader.load("ez_query")
    plan.node_order[0].node = "does_not_exist"

    with pytest.raises(ValueError, match="not executable"):
        build_registry(plan, NodeDependencies())


def test_stage_mapping_covers_every_executable_node(loader):
    for plan_id in executable_plan_ids(loader.list_available()):
        for definition in loader.load(plan_id).node_order:
            assert definition.node in STAGE_BY_NODE
            assert stage_for(definition.node) == STAGE_BY_NODE[definition.node]


def test_reflection_stages_stay_in_plan_order(loader):
    assert plan_stages(loader.load("reflection")) == [
        "retrieve",
        "generate",
        "execute",
        "reflect",
    ]


def test_reflection_plan_has_both_execution_and_reflection(loader):
    plan = loader.load("reflection")

    assert plan_has_node(plan, "reflection")
    assert plan_has_node(plan, "generate_sql")
    execution = [d for d in plan.node_order if d.node == "execute_sql"]
    assert len(execution) == 2
    assert execution[0].on_failure == "skip"


def test_only_first_generate_receives_prepared_candidates(loader):
    plan = loader.load("reflection")
    overrides = request_overrides(
        plan,
        body=_body(),
        limits=_limits(),
        db_id="main",
        dialect="sqlite",
        db_name="shop",
        prepared=["prepared"],
        notes="note",
    )

    assert overrides["gen"]["prepared_candidates"] == ["prepared"]
    assert overrides["gen"]["num_candidates"] == 2
    assert overrides["gen"]["model"] == "test-model"
    assert "prepared_candidates" not in overrides["revise"]
    assert overrides["revise"]["dialect"] == "sqlite"


def test_execution_limits_come_from_the_enforcer(loader):
    plan = loader.load("gensql_agentic")
    overrides = request_overrides(
        plan,
        body=_body(),
        limits=_limits(max_result_rows=7, statement_timeout_ms=250),
        db_id="main",
        dialect="sqlite",
        db_name="shop",
    )

    assert overrides["exec"]["max_rows"] == 7
    assert overrides["exec"]["timeout_ms"] == 250
    assert overrides["exec"]["enabled"] is True
    assert overrides["validate"]["enabled"] is True


def test_no_database_disables_every_execution(loader):
    plan = loader.load("reflection")
    overrides = request_overrides(
        plan,
        body=_body(),
        limits=_limits(),
        db_id="",
        dialect="ansi",
        db_name="",
    )

    assert overrides["exec"]["enabled"] is False
    assert overrides["exec2"]["enabled"] is False


def test_validation_override_follows_request_flags(loader):
    plan = loader.load("gensql_agentic")
    overrides = request_overrides(
        plan,
        body=_body(run_validation=False, execute=False),
        limits=_limits(),
        db_id="",
        dialect="ansi",
        db_name="",
    )

    assert overrides["validate"]["enabled"] is False
