"""Behavioral regressions for fixed execution, permissions and request budgets."""

import asyncio

import pytest

from app.harness.constraint import ConstraintEnforcer, ConstraintLimits
from app.harness.runner import WorkflowRunner
from app.llm.budget import BudgetedRouter, active_budget
from app.models.workflow import EvaluationConfig, NodeDef, WorkflowPlan
from app.nodes.base import BaseNode, NodeOutput
from app.nodes.registry import NodeRegistry


class CountingNode(BaseNode):
    name = "counter"

    def __init__(self, errors=None):
        self.calls = 0
        self.errors = errors or []

    async def execute(self, input):
        self.calls += 1
        return NodeOutput(result={}, errors=self.errors)


def test_registry_instances_do_not_share_nodes():
    first, second = NodeRegistry(), NodeRegistry()
    first.register(CountingNode())
    assert "counter" not in second
    second.register(CountingNode())
    first.reset()
    assert "counter" in second


@pytest.mark.parametrize("permission", ["deny", "ask"])
async def test_permission_blocks_before_execute(permission):
    node = CountingNode()
    registry = NodeRegistry()
    registry.register(node)
    plan = WorkflowPlan(id="permission", name="permission", node_order=[NodeDef("n", node.name)])
    result = await WorkflowRunner(plan, registry).run(
        "hello",
        node_configs={"n": {"tools": ["call_llm"], "permissions": {"call_llm": permission}}},
    )
    assert result.status == "failed"
    assert node.calls == 0
    assert "permission" in result.error.lower()


async def test_omitting_tool_declaration_cannot_bypass_default_deny():
    node = CountingNode()
    node.required_tools = ("call_llm",)
    registry = NodeRegistry()
    registry.register(node)
    plan = WorkflowPlan(
        id="missing-permission", name="missing", node_order=[NodeDef("n", node.name)]
    )
    result = await WorkflowRunner(plan, registry).run("hello", node_configs={"n": {"tools": []}})
    assert result.status == "failed"
    assert node.calls == 0


async def test_node_errors_fail_even_without_evaluation():
    node = CountingNode(errors=["provider unavailable"])
    registry = NodeRegistry()
    registry.register(node)
    plan = WorkflowPlan(
        id="errors",
        name="errors",
        node_order=[NodeDef("n", node.name)],
        evaluation=EvaluationConfig(enabled=False),
    )
    result = await WorkflowRunner(plan, registry).run("hello")
    assert result.status == "failed"
    assert "provider unavailable" in result.error


@pytest.mark.parametrize(
    "nodes", [[], [NodeDef("n", "missing")], [NodeDef("n", "counter", depends_on=["missing"])]]
)
async def test_invalid_plan_cannot_complete(nodes):
    registry = NodeRegistry()
    node = CountingNode()
    registry.register(node)
    result = await WorkflowRunner(
        WorkflowPlan(id="invalid", name="invalid", node_order=nodes), registry
    ).run("hello")
    assert result.status == "failed"
    assert node.calls == 0


async def test_injected_router_budget_blocks_call_and_resets_context():
    class Router:
        calls = 0

        async def complete(self, *args, **kwargs):
            self.calls += 1
            return {"choices": []}

    router = Router()
    budget = ConstraintEnforcer(ConstraintLimits(max_llm_calls_per_query=1))
    token = active_budget.set(budget)
    try:
        wrapped = BudgetedRouter(router)
        await wrapped.complete([])
        with pytest.raises(RuntimeError, match="budget exhausted"):
            await wrapped.complete([])
        assert router.calls == budget.llm_calls_used == 1
    finally:
        active_budget.reset(token)
    await BudgetedRouter(router).complete([])
    assert router.calls == 2


async def test_physical_fallback_cannot_exceed_budget(monkeypatch):
    from app.llm.router import LiteLLMRouter, ProviderConfig, ProviderTimeoutError, RouterConfig

    config = RouterConfig(
        providers={
            "first": ProviderConfig(provider="first", api_key_env="", models=["a"]),
            "second": ProviderConfig(provider="second", api_key_env="", models=["b"]),
        },
        default_model="a",
        fallback_chain=["b"],
    )
    router = LiteLLMRouter(config, mock_mode=False)
    attempts = []

    async def fail(provider, *args, **kwargs):
        attempts.append(provider.provider)
        raise ProviderTimeoutError("temporary outage")

    monkeypatch.setattr(router, "_call_provider", fail)
    budget = ConstraintEnforcer(ConstraintLimits(max_llm_calls_per_query=1))
    token = active_budget.set(budget)
    try:
        with pytest.raises(RuntimeError, match="budget exhausted"):
            await BudgetedRouter(router).complete([])
        assert attempts == ["first"]
        assert budget.llm_calls_used == 1
    finally:
        active_budget.reset(token)


async def test_cancelled_run_persists_abort_and_does_not_advance(monkeypatch):
    from app.models.workflow import WorkflowTrace

    entered = asyncio.Event()
    persisted = []

    class WaitingNode(CountingNode):
        name = "waiting"

        async def execute(self, input):
            entered.set()
            await asyncio.Event().wait()

    async def capture(trace):
        persisted.append(trace)

    monkeypatch.setattr(WorkflowTrace, "persist", capture)
    registry = NodeRegistry()
    registry.register(WaitingNode())
    after = CountingNode()
    registry.register(after)
    plan = WorkflowPlan(
        id="cancel",
        name="cancel",
        node_order=[
            NodeDef("wait", "waiting"),
            NodeDef("after", "counter"),
        ],
    )
    task = asyncio.create_task(WorkflowRunner(plan, registry).run("hello"))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert after.calls == 0
    assert len(persisted) == 1
    assert persisted[0].completed_at is not None
    assert persisted[0].status == "aborted"
    assert persisted[0].nodes[0].error == "Request cancelled"
