"""Tests for WorkflowRunner — real metric checkers and reflection branching.

Covers:
  - evaluate_result metric checkers (syntax_valid, schema_compliant,
    execution_success, unknown metrics) and their leniency on missing info.
  - Reflection branching: needs_revision jumps to the 'revise' node and
    actually executes it (regression test for the fall-through bug).
  - max_iterations bounding the reflect/revise feedback loop.
  - Trace persistence triggered by runner.run().
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from app.harness.runner import WorkflowRunner
from app.models.workflow import EvaluationConfig, NodeDef, WorkflowPlan
from app.nodes.base import BaseNode, NodeInput, NodeOutput
from app.nodes.registry import NodeRegistry

# ── Helpers ────────────────────────────────────────────────────────────


class FakeNode(BaseNode):
    """Records executions and returns canned NodeOutput objects."""

    def __init__(self, name: str, outputs: list[NodeOutput] | None = None):
        self.name = name
        self.calls = 0
        self._outputs = outputs or [NodeOutput(result={"status": "success"})]

    async def execute(self, input: NodeInput) -> NodeOutput:
        self.calls += 1
        idx = min(self.calls - 1, len(self._outputs) - 1)
        return self._outputs[idx]


def _plan(
    node_order: list[NodeDef] | None = None,
    metrics: list[str] | None = None,
    enabled: bool = True,
    max_iterations: int = 10,
) -> WorkflowPlan:
    return WorkflowPlan(
        id="test_plan",
        name="Test Plan",
        node_order=node_order or [],
        evaluation=EvaluationConfig(enabled=enabled, metrics=metrics or []),
        max_iterations=max_iterations,
    )


def _runner(metrics: list[str] | None = None, enabled: bool = True) -> WorkflowRunner:
    """Runner with an empty node_order — for direct evaluate_result tests."""
    return WorkflowRunner(plan=_plan(metrics=metrics, enabled=enabled), registry=NodeRegistry())


NODE_DEF = NodeDef(id="test", node="test_node")


@pytest.fixture
def registry():
    """Fresh registrations with guaranteed cleanup (registry state is class-level)."""
    reg = NodeRegistry()
    registered: list[str] = []

    def _register(node: BaseNode) -> BaseNode:
        reg.register(node)
        registered.append(node.name)
        return node

    yield reg, _register
    for name in registered:
        reg.unregister(name)


# ── syntax_valid checker ───────────────────────────────────────────────


class TestSyntaxValidMetric:
    async def test_bad_sql_in_context_fails(self):
        runner = _runner(metrics=["syntax_valid"])
        output = NodeOutput(result=None, context={"sql": "SELECT * FROM"})
        evaluation = await runner.evaluate_result(NODE_DEF, output)

        assert evaluation.metrics["syntax_valid"].passed is False
        assert evaluation.passed is False

    async def test_good_sql_in_context_passes(self):
        runner = _runner(metrics=["syntax_valid"])
        output = NodeOutput(result=None, context={"sql": "SELECT id, name FROM users"})
        evaluation = await runner.evaluate_result(NODE_DEF, output)

        assert evaluation.metrics["syntax_valid"].passed is True
        assert evaluation.passed is True

    async def test_bad_sql_in_result_dict_fails(self):
        runner = _runner(metrics=["syntax_valid"])
        output = NodeOutput(result={"primary_sql": "SELEC * FROM t"})
        evaluation = await runner.evaluate_result(NODE_DEF, output)

        assert evaluation.metrics["syntax_valid"].passed is False

    async def test_sql_text_key_in_metadata_recognized(self):
        runner = _runner(metrics=["syntax_valid"])
        output = NodeOutput(result=None, metadata={"sql_text": "SELECT 1"})
        evaluation = await runner.evaluate_result(NODE_DEF, output)

        assert evaluation.metrics["syntax_valid"].passed is True

    async def test_missing_sql_is_not_applicable_pass(self):
        runner = _runner(metrics=["syntax_valid"])
        output = NodeOutput(result={"tables": ["users"]})
        evaluation = await runner.evaluate_result(NODE_DEF, output)

        metric = evaluation.metrics["syntax_valid"]
        assert metric.passed is True
        assert "not applicable" in metric.details


# ── schema_compliant checker ───────────────────────────────────────────


class TestSchemaCompliantMetric:
    async def test_failing_report_object_fails(self):
        runner = _runner(metrics=["schema_compliant"])
        report = SimpleNamespace(passed=False, schema_errors=["no such table"])
        output = NodeOutput(result=None, context={"validation_report": report})
        evaluation = await runner.evaluate_result(NODE_DEF, output)

        assert evaluation.metrics["schema_compliant"].passed is False
        assert evaluation.passed is False

    async def test_passing_report_object_passes(self):
        runner = _runner(metrics=["schema_compliant"])
        report = SimpleNamespace(passed=True)
        output = NodeOutput(result=None, context={"validation_report": report})
        evaluation = await runner.evaluate_result(NODE_DEF, output)

        assert evaluation.metrics["schema_compliant"].passed is True

    async def test_report_as_dict_is_supported(self):
        runner = _runner(metrics=["schema_compliant"])
        output = NodeOutput(result=None, context={"validation_report": {"passed": False}})
        evaluation = await runner.evaluate_result(NODE_DEF, output)

        assert evaluation.metrics["schema_compliant"].passed is False

    async def test_validation_passed_flag_fallback(self):
        runner = _runner(metrics=["schema_compliant"])
        output = NodeOutput(result=None, context={"validation_passed": False})
        evaluation = await runner.evaluate_result(NODE_DEF, output)

        assert evaluation.metrics["schema_compliant"].passed is False

    async def test_no_report_is_not_applicable_pass(self):
        runner = _runner(metrics=["schema_compliant"])
        output = NodeOutput(result={"anything": 1})
        evaluation = await runner.evaluate_result(NODE_DEF, output)

        metric = evaluation.metrics["schema_compliant"]
        assert metric.passed is True
        assert "not applicable" in metric.details


# ── execution_success checker ──────────────────────────────────────────


class TestExecutionSuccessMetric:
    async def test_output_errors_fail_evaluation(self):
        runner = _runner(metrics=["execution_success"])
        output = NodeOutput(result=None, errors=["boom"])
        evaluation = await runner.evaluate_result(NODE_DEF, output)

        assert evaluation.passed is False
        assert evaluation.metrics["execution_success"].passed is False

    async def test_error_status_in_result_fails(self):
        runner = _runner(metrics=["execution_success"])
        output = NodeOutput(result={"status": "execution_error", "rows": []})
        evaluation = await runner.evaluate_result(NODE_DEF, output)

        assert evaluation.metrics["execution_success"].passed is False

    async def test_success_status_passes(self):
        runner = _runner(metrics=["execution_success"])
        output = NodeOutput(result={"status": "success", "rows": [[1]]})
        evaluation = await runner.evaluate_result(NODE_DEF, output)

        assert evaluation.metrics["execution_success"].passed is True

    async def test_non_dict_result_passes(self):
        runner = _runner(metrics=["execution_success"])
        output = NodeOutput(result="plain result")
        evaluation = await runner.evaluate_result(NODE_DEF, output)

        assert evaluation.metrics["execution_success"].passed is True

    async def test_none_output_fails(self):
        runner = _runner(metrics=["execution_success"])
        evaluation = await runner.evaluate_result(NODE_DEF, None)

        assert evaluation.passed is False
        assert evaluation.metrics["execution_success"].passed is False


# ── Dispatch / aggregation ─────────────────────────────────────────────


class TestEvaluationDispatch:
    async def test_unknown_metric_passes(self):
        runner = _runner(metrics=["fancy_new_metric"])
        output = NodeOutput(result={"status": "success"})
        evaluation = await runner.evaluate_result(NODE_DEF, output)

        metric = evaluation.metrics["fancy_new_metric"]
        assert metric.passed is True
        assert "no checker registered" in metric.details
        assert evaluation.passed is True

    async def test_one_failing_metric_fails_evaluation(self):
        runner = _runner(metrics=["syntax_valid", "execution_success"])
        output = NodeOutput(result={"status": "success"}, context={"sql": "SELECT FROM WHERE"})
        evaluation = await runner.evaluate_result(NODE_DEF, output)

        assert evaluation.metrics["syntax_valid"].passed is False
        assert evaluation.metrics["execution_success"].passed is True
        assert evaluation.passed is False

    async def test_default_metrics_pass_for_benign_output(self):
        """Default plan metrics must stay lenient for nodes without SQL/reports."""
        runner = _runner(metrics=["syntax_valid", "schema_compliant", "execution_success"])
        output = NodeOutput(result={"tables": ["users"]}, metadata={"status": "success"})
        evaluation = await runner.evaluate_result(NODE_DEF, output)

        assert evaluation.passed is True
        assert len(evaluation.metrics) == 3

    async def test_evaluation_disabled_short_circuits(self):
        runner = _runner(metrics=["syntax_valid"], enabled=False)
        output = NodeOutput(result=None, context={"sql": "SELECT * FROM"})
        evaluation = await runner.evaluate_result(NODE_DEF, output)

        assert evaluation.passed is True
        assert evaluation.metrics == {}

    async def test_needs_revision_propagates_from_metadata(self):
        runner = _runner(metrics=["execution_success"])
        output = NodeOutput(result={"status": "success"}, metadata={"needs_revision": True})
        evaluation = await runner.evaluate_result(NODE_DEF, output)

        assert evaluation.needs_revision is True
        assert evaluation.passed is True

    async def test_needs_revision_false_by_default(self):
        runner = _runner(metrics=["execution_success"])
        output = NodeOutput(result={"status": "success"})
        evaluation = await runner.evaluate_result(NODE_DEF, output)

        assert evaluation.needs_revision is False


# ── Reflection branching ───────────────────────────────────────────────


class TestReflectionBranching:
    async def test_revise_node_actually_executes(self, registry):
        """Regression: the reflect jump must execute 'revise', not skip past it."""
        reg, register = registry
        gen = register(FakeNode("fake_gen"))
        reflect = register(
            FakeNode(
                "fake_reflect",
                outputs=[
                    NodeOutput(result={"row_count": 0}, metadata={"needs_revision": True}),
                    NodeOutput(result={"status": "success"}, metadata={"needs_revision": False}),
                ],
            )
        )
        revise = register(FakeNode("fake_revise"))
        final = register(FakeNode("fake_output"))

        plan = _plan(
            node_order=[
                NodeDef(id="gen", node="fake_gen"),
                NodeDef(id="reflect", node="fake_reflect"),
                NodeDef(id="revise", node="fake_revise"),
                NodeDef(id="output", node="fake_output"),
            ],
        )
        runner = WorkflowRunner(plan=plan, registry=reg)
        result = await runner.run("test query")

        assert result.status == "completed"
        assert revise.calls >= 1, "revise node was skipped — reflection jump bug"
        assert final.calls == 1
        assert gen.calls == 1
        assert reflect.calls == 1  # jump goes forward to revise; reflect not re-run

    async def test_trace_records_revise_after_reflect(self, registry):
        reg, register = registry
        register(FakeNode("fake_gen2"))
        register(
            FakeNode(
                "fake_reflect2",
                outputs=[NodeOutput(result={}, metadata={"needs_revision": True})],
            )
        )
        register(FakeNode("fake_revise2"))
        register(FakeNode("fake_output2"))

        plan = _plan(
            node_order=[
                NodeDef(id="gen", node="fake_gen2"),
                NodeDef(id="reflect", node="fake_reflect2"),
                NodeDef(id="revise", node="fake_revise2"),
                NodeDef(id="output", node="fake_output2"),
            ],
        )
        runner = WorkflowRunner(plan=plan, registry=reg)
        result = await runner.run("test query")

        node_ids = [t.node_id for t in result.trace.nodes]
        assert node_ids[:3] == ["gen", "reflect", "revise"]

    async def test_no_jump_when_revision_not_needed(self, registry):
        """revise placed before reflect must not be re-entered without needs_revision."""
        reg, register = registry
        revise = register(FakeNode("fake_revise3"))
        reflect = register(
            FakeNode(
                "fake_reflect3",
                outputs=[NodeOutput(result={"status": "success"})],
            )
        )

        plan = _plan(
            node_order=[
                NodeDef(id="revise", node="fake_revise3"),
                NodeDef(id="reflect", node="fake_reflect3"),
            ],
        )
        runner = WorkflowRunner(plan=plan, registry=reg)
        result = await runner.run("test query")

        assert result.status == "completed"
        assert revise.calls == 1  # only the initial sequential pass
        assert reflect.calls == 1

    async def test_loop_back_bounded_by_max_iterations(self, registry):
        """reflect that always needs revision must terminate via max_iterations."""
        reg, register = registry
        revise = register(FakeNode("fake_revise4"))
        reflect = register(
            FakeNode(
                "fake_reflect4",
                outputs=[NodeOutput(result={}, metadata={"needs_revision": True})],
            )
        )

        max_iterations = 6
        plan = _plan(
            node_order=[
                NodeDef(id="revise", node="fake_revise4"),
                NodeDef(id="reflect", node="fake_reflect4"),
            ],
            max_iterations=max_iterations,
        )
        runner = WorkflowRunner(plan=plan, registry=reg)
        result = await runner.run("test query")

        assert result.status == "completed"
        assert revise.calls >= 2  # loop actually ran more than once
        assert revise.calls + reflect.calls <= max_iterations

    async def test_needs_revision_without_revise_node_advances(self, registry):
        """No 'revise' node in the plan — fall through to the next node."""
        reg, register = registry
        register(
            FakeNode(
                "fake_reflect5",
                outputs=[NodeOutput(result={}, metadata={"needs_revision": True})],
            )
        )
        final = register(FakeNode("fake_final5"))

        plan = _plan(
            node_order=[
                NodeDef(id="reflect", node="fake_reflect5"),
                NodeDef(id="output", node="fake_final5"),
            ],
        )
        runner = WorkflowRunner(plan=plan, registry=reg)
        result = await runner.run("test query")

        assert result.status == "completed"
        assert final.calls == 1


# ── Runner trace persistence ───────────────────────────────────────────


class TestRunnerTracePersistence:
    async def test_run_persists_trace_jsonl(self, registry, tmp_path, monkeypatch):
        monkeypatch.setenv("DE_TRACE_DIR", str(tmp_path))
        reg, register = registry
        register(FakeNode("fake_persist_node"))

        plan = _plan(node_order=[NodeDef(id="step1", node="fake_persist_node")])
        runner = WorkflowRunner(plan=plan, registry=reg)
        result = await runner.run("persist me", session_id="sess-1")

        files = list(tmp_path.glob("*.jsonl"))
        assert len(files) == 1
        assert files[0].stem == result.trace.workflow_id

        lines = files[0].read_text(encoding="utf-8").splitlines()
        header = json.loads(lines[0])
        assert header["plan_id"] == "test_plan"
        assert header["session_id"] == "sess-1"
        node_line = json.loads(lines[1])
        assert node_line["node_id"] == "step1"
