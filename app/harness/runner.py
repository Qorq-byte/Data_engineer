"""WorkflowRunner — the Harness execution engine.

See SPEC §3.4.3 for the full specification.

WorkflowRunner implements the core Harness invariant:
  LLM doesn't decide the next step — the plan's node_order does.

Lifecycle:
  1. Initialize: load plan, resolve nodes, create shared context.
  2. Advance: iterate over node_order, execute each node in sequence.
  3. Evaluate: after each node, run quality metrics via evaluate_result().
  4. Handle failures: retry / skip / abort based on failure_policy.
  5. Persist: save WorkflowTrace to disk for audit/replay.
"""

import asyncio
from datetime import datetime
from typing import Any

import sqlglot

from app.harness.constraint import ConstraintEnforcer, ConstraintLimits
from app.harness.events import NodeEventHandler
from app.models.workflow import (
    Evaluation,
    MetricResult,
    NodeDef,
    NodeTrace,
    WorkflowPlan,
    WorkflowResult,
    WorkflowTrace,
)
from app.nodes.base import NodeOutput
from app.nodes.registry import NodeRegistry


class WorkflowRunner:
    """Executes a WorkflowPlan by advancing through node_order step by step.

    The runner:
      - NEVER calls an LLM for routing decisions.
      - ALWAYS follows node_order in sequence (or as modified by on_failure rules).
      - ALWAYS evaluates results after each node.
      - ALWAYS enforces constraint limits (max_iterations, max_llm_calls).
    """

    def __init__(
        self,
        plan: WorkflowPlan,
        registry: NodeRegistry,
        *,
        constraints: ConstraintEnforcer | None = None,
    ):
        self.plan = plan
        self.registry = registry
        self.constraints = constraints or ConstraintEnforcer(ConstraintLimits())
        self.trace = WorkflowTrace(workflow_id="", plan_id=plan.id)

    async def run(
        self,
        input_text: str,
        session_id: str = "",
        *,
        node_configs: dict[str, dict[str, Any]] | None = None,
        on_node_event: NodeEventHandler | None = None,
        reset_constraints: bool = True,
        **kwargs: Any,
    ) -> WorkflowResult:
        from app.llm.budget import active_budget

        token = active_budget.set(self.constraints)
        try:
            return await self._run(
                input_text,
                session_id,
                node_configs=node_configs,
                on_node_event=on_node_event,
                reset_constraints=reset_constraints,
                **kwargs,
            )
        finally:
            active_budget.reset(token)

    async def _run(
        self,
        input_text: str,
        session_id: str = "",
        *,
        node_configs: dict[str, dict[str, Any]] | None = None,
        on_node_event: NodeEventHandler | None = None,
        reset_constraints: bool = True,
        **kwargs: Any,
    ) -> WorkflowResult:
        """Execute a fixed plan. Protocol adapters cannot choose the next node."""
        if reset_constraints:
            self.constraints.reset()
        self.trace = WorkflowTrace(
            workflow_id=f"wf_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}",
            plan_id=self.plan.id,
            session_id=session_id,
            started_at=datetime.now(),
        )
        context: dict[str, Any] = {"query_text": input_text, "session_id": session_id, **kwargs}

        async def finish(status: str, error: str | None = None) -> WorkflowResult:
            self.trace.completed_at = datetime.now()
            self.trace.status = status
            self.trace.error = error or ""
            await self.trace.persist()
            return WorkflowResult(
                status=status,
                error=error,
                context=context,
                trace=self.trace,
                final_output=context.get("last_execution"),
            )

        known_metrics = {"syntax_valid", "schema_compliant", "execution_success"}
        if not self.plan.node_order:
            return await finish("failed", "Workflow plan contains no nodes")
        if self.plan.evaluation.enabled and set(self.plan.evaluation.metrics) - known_metrics:
            return await finish("failed", "Unknown evaluation metric")
        seen: set[str] = set()
        for definition in self.plan.node_order:
            if definition.node not in self.registry or definition.id in seen:
                return await finish("failed", f"Missing or duplicate node: {definition.node}")
            if set(definition.depends_on) - seen:
                return await finish("failed", f"Unsatisfied dependency: {definition.id}")
            seen.add(definition.id)

        index = 0
        iterations = 0
        reflection_index = None
        overrides = node_configs or {}
        try:
            while index < len(self.plan.node_order):
                definition = self.plan.node_order[index]
                node = self.registry.get(definition.node)
                retries = min(
                    self.plan.failure_policy.max_retries, self.constraints.limits.max_node_retries
                )
                evaluation = None
                for attempt in range(max(0, retries) + 1):
                    if iterations >= self.plan.max_iterations:
                        return await finish("aborted", "Workflow iteration budget exhausted")
                    iterations += 1
                    config = {
                        "temperature": definition.config.temperature,
                        "num_candidates": definition.config.max_candidates,
                        "max_candidates": definition.config.max_candidates,
                        "max_rows": definition.config.max_rows,
                        "timeout_ms": definition.config.timeout_ms,
                        "mode": definition.config.mode,
                        **definition.config.extras,
                        **overrides.get(definition.id, {}),
                    }
                    trace = NodeTrace(
                        node_id=definition.id,
                        node_name=definition.node,
                        input_summary=f"input_chars={len(input_text)}",
                        started_at=datetime.now(),
                    )
                    if on_node_event:
                        await on_node_event(
                            "started",
                            {"node_id": definition.id, "node": node.name, "attempt": attempt + 1},
                        )
                    output = None
                    try:
                        prepared = await node.setup_input(
                            {"query_text": input_text, "context": context, "config": config}
                        )
                        from app.harness.permission import PermissionManager

                        manager = (
                            PermissionManager(rules=[config["permissions"]])
                            if "permissions" in config
                            else getattr(node, "permission_manager", None) or PermissionManager()
                        )
                        if config.get("enabled", True) and manager:
                            tools = set(config.get("tools", [])) | set(node.required_tools)
                            for tool in sorted(tools):
                                if manager.check(tool, context) != "allow":
                                    raise PermissionError(f"Tool permission not granted: {tool}")
                        if config.get("enabled", True):
                            output = await node.execute(prepared)
                        else:
                            output = NodeOutput(
                                result={"status": "skipped"}, metadata={"status": "skipped"}
                            )
                        context = await node.update_context(output, context)
                        evaluation = await self.evaluate_result(definition, output)
                        trace.output_summary = (
                            f"type={type(output.result).__name__}; "
                            f"status={output.metadata.get('status', 'unknown')}"
                        )
                        trace.error = "; ".join(output.errors) or None
                    except Exception as exc:
                        trace.error = str(exc)
                        evaluation = Evaluation(passed=False, notes=str(exc))
                    trace.completed_at = datetime.now()
                    trace.latency_ms = (
                        trace.completed_at - trace.started_at
                    ).total_seconds() * 1000
                    trace.evaluation = evaluation
                    self.trace.nodes.append(trace)
                    if on_node_event:
                        await on_node_event(
                            "completed" if evaluation.passed else "failed",
                            {
                                "node_id": definition.id,
                                "node": node.name,
                                "attempt": attempt + 1,
                                "latency_ms": trace.latency_ms,
                                "output": output,
                                "error": trace.error or evaluation.notes,
                            },
                        )
                    if evaluation.passed:
                        break
                    self.constraints.record_retry(definition.id)
                if not evaluation or not evaluation.passed:
                    if definition.on_failure == "skip":
                        index += 1
                        continue
                    return await finish(
                        "failed",
                        trace.error
                        or (evaluation.notes if evaluation is not None else "")
                        or "Node evaluation failed",
                    )
                if definition.on_pass == "output" and not evaluation.needs_revision:
                    return await finish("completed")
                if index == reflection_index and not evaluation.needs_revision:
                    return await finish("completed")
                if definition.id == "reflect" and evaluation.needs_revision:
                    revision = next(
                        (i for i, nd in enumerate(self.plan.node_order) if nd.id == "revise"), None
                    )
                    if revision is not None:
                        reflection_index = index
                        index = revision
                        continue
                if reflection_index is not None and index == len(self.plan.node_order) - 1:
                    index = reflection_index
                    continue
                index += 1
            return await finish("completed")
        except asyncio.CancelledError:
            if "trace" in locals() and trace not in self.trace.nodes:
                trace.error = "Request cancelled"
                trace.completed_at = datetime.now()
                self.trace.nodes.append(trace)
            await finish("aborted", "Request cancelled")
            raise

    async def evaluate_result(self, node_def: NodeDef, output: NodeOutput | None) -> Evaluation:
        """Evaluate a node's output against configured quality metrics.

        Each metric name in the plan's evaluation config is dispatched to a
        real checker:

          - ``syntax_valid``: parse the SQL found in the output via sqlglot.
          - ``schema_compliant``: follow ValidateSQLNode's validation report.
          - ``execution_success``: fail on node errors or an error status.

        Checkers are lenient by design: when the information a metric needs is
        absent from the output, the metric passes with a "not applicable" note.
        Unknown metric names fail closed. needs_revision
        is propagated from node output metadata (set by ReflectionNode to
        trigger the revise branch).
        """
        if output is None or output.errors:
            return Evaluation(
                passed=False,
                metrics={
                    "execution_success": MetricResult(
                        name="execution_success",
                        passed=False,
                        details=str(output.errors) if output else "No output",
                    )
                },
                notes="Node execution returned errors",
            )

        if not self.plan.evaluation.enabled:
            return Evaluation(passed=True)

        # Propagate needs_revision from node output metadata (used by ReflectionNode)
        needs_revision = bool(output.metadata.get("needs_revision"))

        metrics: dict[str, MetricResult] = {}
        all_passed = True
        for metric_name in self.plan.evaluation.metrics:
            result = self._check_metric(metric_name, output)
            metrics[metric_name] = result
            all_passed = all_passed and result.passed

        return Evaluation(passed=all_passed, metrics=metrics, needs_revision=needs_revision)

    # ── Metric checkers ─────────────────────────────────────────────

    #: Keys under which nodes commonly expose their generated SQL.
    _SQL_KEYS = ("sql", "primary_sql", "sql_text")

    def _check_metric(self, metric_name: str, output: NodeOutput) -> MetricResult:
        """Dispatch a single metric name to its checker."""
        match metric_name:
            case "syntax_valid":
                return self._check_syntax_valid(output)
            case "schema_compliant":
                return self._check_schema_compliant(output)
            case "execution_success":
                return self._check_execution_success(output)
            case _:
                return MetricResult(name=metric_name, passed=False, details="no checker registered")

    @classmethod
    def _extract_sql(cls, output: NodeOutput) -> str | None:
        """Find a SQL string in the node output (result dict, context, or metadata)."""
        for source in (output.result, output.context, output.metadata):
            if not isinstance(source, dict):
                continue
            for key in cls._SQL_KEYS:
                value = source.get(key)
                if isinstance(value, str) and value.strip():
                    return value
        return None

    @classmethod
    def _check_syntax_valid(cls, output: NodeOutput) -> MetricResult:
        """Check that the SQL found in the output parses via sqlglot."""
        sql = cls._extract_sql(output)
        if sql is None:
            return MetricResult(
                name="syntax_valid",
                passed=True,
                details="not applicable: no SQL found in output",
            )
        try:
            sqlglot.parse_one(sql)
        except Exception as exc:  # sqlglot raises ParseError/TokenError/ValueError
            return MetricResult(
                name="syntax_valid",
                passed=False,
                details=f"SQL failed to parse: {exc}"[:300],
            )
        return MetricResult(name="syntax_valid", passed=True, details="SQL parsed successfully")

    @staticmethod
    def _check_schema_compliant(output: NodeOutput) -> MetricResult:
        """Follow ValidateSQLNode's validation report/flag when present."""
        report: Any = None
        passed_flag: Any = None
        for source in (output.context, output.metadata, output.result):
            if not isinstance(source, dict):
                continue
            if report is None:
                report = source.get("validation_report")
            if passed_flag is None:
                passed_flag = source.get("validation_passed")

        if report is not None:
            report_passed = (
                report.get("passed")
                if isinstance(report, dict)
                else getattr(report, "passed", None)
            )
            if report_passed is not None:
                return MetricResult(
                    name="schema_compliant",
                    passed=bool(report_passed),
                    details="validation_report consulted",
                )
        if passed_flag is not None:
            return MetricResult(
                name="schema_compliant",
                passed=bool(passed_flag),
                details="validation_passed flag consulted",
            )
        return MetricResult(
            name="schema_compliant",
            passed=True,
            details="not applicable: no validation report in output",
        )

    @staticmethod
    def _check_execution_success(output: NodeOutput) -> MetricResult:
        """Fail when the node reported errors or an explicit error status."""
        if output.errors:
            return MetricResult(
                name="execution_success",
                passed=False,
                details=f"node reported errors: {output.errors}"[:300],
            )
        status = output.result.get("status") if isinstance(output.result, dict) else None
        if isinstance(status, str) and "error" in status.lower():
            return MetricResult(
                name="execution_success",
                passed=False,
                details=f"result status indicates failure: {status}",
            )
        return MetricResult(
            name="execution_success", passed=True, details="no execution errors detected"
        )
