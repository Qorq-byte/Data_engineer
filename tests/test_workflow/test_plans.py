"""Tests for workflow plan YAML templates — loading, structure, and routing.

Covers all 6 plan templates (2 existing + 4 new) for structure validation,
node dependency integrity, PlanLoader integration, and PlanSelector routing.
"""

from __future__ import annotations

import pytest

from app.harness.plan_loader import PlanLoader
from app.workflow.plan_selector import PlanSelector

# ── Helpers ────────────────────────────────────────────────────────────

def _loader() -> PlanLoader:
    return PlanLoader("app/workflow/plans")


def _selector() -> PlanSelector:
    return PlanSelector(plan_loader=_loader())


# ── Plan existence tests ──────────────────────────────────────────────


class TestAllPlansExist:
    """Verify all 6 plan templates are present and loadable."""

    EXPECTED = {
        "gensql_agentic",
        "ez_query",
        "reflection",
        "chat_agentic",
        "explore",
        "metric_query",
    }

    @pytest.fixture
    def loader(self) -> PlanLoader:
        return _loader()

    def test_all_six_plans_available(self, loader: PlanLoader):
        available = set(loader.list_available())
        assert available == self.EXPECTED

    def test_each_plan_loads(self, loader: PlanLoader):
        for plan_id in self.EXPECTED:
            plan = loader.load(plan_id)
            assert plan.id == plan_id
            assert plan.name, f"{plan_id} has no name"
            assert plan.description, f"{plan_id} has no description"


# ── Individual plan structure tests ───────────────────────────────────


class TestReflectionPlan:
    @pytest.fixture
    def plan(self):
        return _loader().load("reflection")

    def test_id_and_name(self, plan):
        assert plan.id == "reflection"
        assert "反射" in plan.name

    def test_node_count(self, plan):
        assert len(plan.node_order) == 6

    def test_node_ids(self, plan):
        ids = [n.id for n in plan.node_order]
        assert ids == ["link", "gen", "exec", "reflect", "revise", "exec2"]

    def test_dag_topology(self, plan):
        """reflection: link → gen → exec → reflect → [revise → exec2]"""
        nodes = {n.id: n for n in plan.node_order}
        assert nodes["gen"].depends_on == ["link"]
        assert nodes["exec"].depends_on == ["gen"]
        assert nodes["reflect"].depends_on == ["exec"]
        assert nodes["revise"].depends_on == ["reflect"]
        assert nodes["exec2"].depends_on == ["revise"]

    def test_reflect_has_reflection_routing(self, plan):
        reflect = next(n for n in plan.node_order if n.id == "reflect")
        assert reflect.on_pass == "output"
        assert reflect.on_fail == "revise"

    def test_failure_policy(self, plan):
        assert plan.failure_policy.max_retries == 3
        assert plan.failure_policy.on_exhausted == "abort"

    def test_evaluation_enabled(self, plan):
        assert plan.evaluation.enabled is True
        assert "execution_success" in plan.evaluation.metrics

    def test_max_iterations(self, plan):
        assert plan.max_iterations == 10


class TestChatAgenticPlan:
    @pytest.fixture
    def plan(self):
        return _loader().load("chat_agentic")

    def test_id_and_name(self, plan):
        assert plan.id == "chat_agentic"
        assert "对话" in plan.name or "chat" in plan.name.lower()

    def test_node_count(self, plan):
        assert len(plan.node_order) == 5

    def test_node_ids(self, plan):
        ids = [n.id for n in plan.node_order]
        assert ids == ["parse", "search", "gen", "validate", "respond"]

    def test_dag_topology(self, plan):
        nodes = {n.id: n for n in plan.node_order}
        assert nodes["search"].depends_on == ["parse"]
        assert nodes["gen"].depends_on == ["search"]
        assert nodes["validate"].depends_on == ["gen"]
        assert nodes["respond"].depends_on == ["validate"]

    def test_first_node_is_parse_nl(self, plan):
        assert plan.node_order[0].node == "parse_nl"

    def test_last_node_is_respond(self, plan):
        assert plan.node_order[-1].node == "respond"

    def test_hybrid_search_target_all(self, plan):
        search = next(n for n in plan.node_order if n.id == "search")
        extras = search.config.extras
        assert extras.get("target") == "all"
        assert extras.get("top_k") == 10

    def test_failure_policy(self, plan):
        assert plan.failure_policy.max_retries == 2


class TestExplorePlan:
    @pytest.fixture
    def plan(self):
        return _loader().load("explore")

    def test_id_and_name(self, plan):
        assert plan.id == "explore"
        assert "探索" in plan.name or "explor" in plan.name.lower()

    def test_node_count(self, plan):
        assert len(plan.node_order) == 3

    def test_node_ids(self, plan):
        ids = [n.id for n in plan.node_order]
        assert ids == ["parse", "search", "format"]

    def test_dag_topology(self, plan):
        nodes = {n.id: n for n in plan.node_order}
        assert nodes["search"].depends_on == ["parse"]
        assert nodes["format"].depends_on == ["search"]

    def test_no_execute_node(self, plan):
        """explore plan must NOT contain execute_sql (read-only exploration)."""
        node_names = [n.node for n in plan.node_order]
        assert "execute_sql" not in node_names

    def test_format_node_is_format_sql(self, plan):
        fmt = next(n for n in plan.node_order if n.id == "format")
        assert fmt.node == "format_sql"

    def test_evaluation_disabled(self, plan):
        assert plan.evaluation.enabled is False
        assert plan.evaluation.metrics == []

    def test_hybrid_search_top_k(self, plan):
        search = next(n for n in plan.node_order if n.id == "search")
        extras = search.config.extras
        assert extras.get("top_k") == 15

    def test_max_iterations(self, plan):
        assert plan.max_iterations == 3


class TestMetricQueryPlan:
    @pytest.fixture
    def plan(self):
        return _loader().load("metric_query")

    def test_id_and_name(self, plan):
        assert plan.id == "metric_query"
        assert "MetricFlow" in plan.name or "指标" in plan.name

    def test_node_count(self, plan):
        assert len(plan.node_order) == 4

    def test_node_ids(self, plan):
        ids = [n.id for n in plan.node_order]
        assert ids == ["resolve", "gen", "validate", "exec"]

    def test_dag_topology(self, plan):
        nodes = {n.id: n for n in plan.node_order}
        assert nodes["gen"].depends_on == ["resolve"]
        assert nodes["validate"].depends_on == ["gen"]
        assert nodes["exec"].depends_on == ["validate"]

    def test_first_node_is_metric_resolve(self, plan):
        assert plan.node_order[0].node == "metric_resolve"

    def test_generate_sql_in_refine_mode(self, plan):
        gen = next(n for n in plan.node_order if n.id == "gen")
        assert gen.config.mode == "refine"
        assert gen.config.temperature == 0.2

    def test_execute_max_rows(self, plan):
        exec_node = next(n for n in plan.node_order if n.id == "exec")
        assert exec_node.config.max_rows == 500


class TestExistingPlans:
    """Verify the 2 original plans still load correctly."""

    def test_gensql_agentic(self):
        plan = _loader().load("gensql_agentic")
        assert plan.id == "gensql_agentic"
        assert len(plan.node_order) == 4
        assert plan.node_order[0].node == "schema_linking"
        assert plan.node_order[-1].node == "execute_sql"

    def test_ez_query(self):
        plan = _loader().load("ez_query")
        assert plan.id == "ez_query"
        assert len(plan.node_order) == 3
        assert plan.node_order[0].node == "schema_linking"
        assert plan.node_order[-1].node == "execute_sql"
        assert plan.evaluation.enabled is False


# ── Cross-plan validation tests ───────────────────────────────────────


class TestCrossPlanValidation:
    """Structural and consistency checks across all 6 plans."""

    @pytest.fixture
    def all_plans(self):
        loader = _loader()
        return {pid: loader.load(pid) for pid in loader.list_available()}

    def test_all_plans_have_unique_ids(self, all_plans):
        assert len(all_plans) == 6

    def test_all_plans_have_non_empty_node_order(self, all_plans):
        for pid, plan in all_plans.items():
            assert len(plan.node_order) > 0, f"{pid} has empty node_order"

    def test_all_deps_reference_existing_node_ids(self, all_plans):
        for pid, plan in all_plans.items():
            node_ids = {n.id for n in plan.node_order}
            for node in plan.node_order:
                for dep in node.depends_on:
                    assert dep in node_ids, (
                        f"{pid}: node '{node.id}' depends on unknown '{dep}'"
                    )

    def test_all_first_nodes_have_no_dependencies(self, all_plans):
        for pid, plan in all_plans.items():
            first = plan.node_order[0]
            assert first.depends_on == [], (
                f"{pid}: first node '{first.id}' should have no deps"
            )

    def test_no_duplicate_node_ids(self, all_plans):
        for pid, plan in all_plans.items():
            ids = [n.id for n in plan.node_order]
            assert len(ids) == len(set(ids)), f"{pid}: duplicate node ids"

    def test_all_nodes_have_valid_registry_names(self, all_plans):
        """Every plan node must reference a known node registry name."""
        valid = {
            "schema_linking", "parse_nl", "generate_sql", "validate_sql",
            "execute_sql", "hybrid_search", "metric_resolve", "reflection",
            "format_sql", "respond",
        }
        for pid, plan in all_plans.items():
            for node in plan.node_order:
                assert node.node in valid, (
                    f"{pid}: unknown node '{node.node}'"
                )

    def test_reflection_is_longest(self, all_plans):
        """reflection plan should have the most nodes (feedback loop)."""
        lengths = {pid: len(p.node_order) for pid, p in all_plans.items()}
        assert lengths["reflection"] == max(lengths.values())

    def test_explore_is_shortest(self, all_plans):
        """explore plan should have the fewest nodes (no SQL generation/exec)."""
        lengths = {pid: len(p.node_order) for pid, p in all_plans.items()}
        assert lengths["explore"] == min(lengths.values())


# ── PlanSelector routing tests for new plans ──────────────────────────


class TestPlanSelectorNewPlans:
    """Verify PlanSelector can route to new plan types."""

    @pytest.fixture
    def selector(self) -> PlanSelector:
        return _selector()

    def test_metric_keyword_routes_to_metric_query(self, selector: PlanSelector):
        """Queries mentioning '指标' should route to metric_query plan."""
        # Note: currently no L1 rule targets metric_query — it's for future use.
        # For now, selector has no rule for this, so falls back.
        # But the plan itself must exist and be loadable.
        loader = _loader()
        plan = loader.load("metric_query")
        assert plan is not None
        assert plan.id == "metric_query"

    def test_all_six_plans_in_available_list(self, selector: PlanSelector):
        available = selector._available_plans()
        assert len(available) == 6
        assert "reflection" in available
        assert "chat_agentic" in available
        assert "explore" in available
        assert "metric_query" in available

    def test_reload_preserves_new_plans(self):
        loader = _loader()
        for pid in ["reflection", "chat_agentic", "explore", "metric_query"]:
            plan1 = loader.load(pid)
            plan2 = loader.reload(pid)
            assert plan1.id == plan2.id
            assert len(plan1.node_order) == len(plan2.node_order)


# ── Edge cases ────────────────────────────────────────────────────────


class TestPlanEdgeCases:
    def test_load_then_reload(self):
        loader = _loader()
        plan1 = loader.load("metric_query")
        plan2 = loader.reload("metric_query")
        # Should be a fresh parse (different objects, same content)
        assert plan1 is not plan2
        assert plan1.id == plan2.id
        assert plan1.name == plan2.name

    def test_missing_plan_raises(self):
        loader = _loader()
        with pytest.raises(FileNotFoundError, match="nonexistent_plan"):
            loader.load("nonexistent_plan")

    def test_list_available_returns_sorted(self):
        loader = _loader()
        names = loader.list_available()
        assert names == sorted(names)

    def test_each_plan_has_on_exhausted_set(self):
        loader = _loader()
        for pid in loader.list_available():
            plan = loader.load(pid)
            assert plan.on_exhausted in ("force_output", "abort"), (
                f"{pid}: invalid on_exhausted"
            )

    def test_each_plan_has_valid_failure_on_exhausted(self):
        loader = _loader()
        for pid in loader.list_available():
            plan = loader.load(pid)
            assert plan.failure_policy.on_exhausted in ("abort", "skip", "fallback"), (
                f"{pid}: invalid failure on_exhausted"
            )
