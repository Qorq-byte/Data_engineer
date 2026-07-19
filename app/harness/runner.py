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

from datetime import datetime
from typing import Any

import sqlglot

from app.harness.constraint import ConstraintEnforcer, ConstraintLimits
from app.models.workflow import (
    Evaluation,
    MetricResult,
    NodeTrace,
    WorkflowPlan,
    WorkflowResult,
    WorkflowTrace,
)
from app.nodes.base import NodeInput
from app.nodes.registry import NodeRegistry


class WorkflowRunner:
    """Executes a WorkflowPlan by advancing through node_order step by step.

    The runner:
      - NEVER calls an LLM for routing decisions.
      - ALWAYS follows node_order in sequence (or as modified by on_failure rules).
      - ALWAYS evaluates results after each node.
      - ALWAYS enforces constraint limits (max_iterations, max_llm_calls).
    """

    def __init__(self, plan: WorkflowPlan, registry: NodeRegistry):
        self.plan = plan
        self.registry = registry
        self.constraints = ConstraintEnforcer(ConstraintLimits())
        self.trace = WorkflowTrace(workflow_id="", plan_id=plan.id)

    async def run(
        self, input_text: str, session_id: str = "", **kwargs: Any
    ) -> WorkflowResult:
        """Execute the full workflow lifecycle.

        Args:
            input_text: The user's NL query text.
            session_id: Optional session identifier for persistence.
            **kwargs: Additional context passed to every node.

        Returns:
            WorkflowResult with final status, context, and trace.
        """
        self.constraints.reset()
        self.trace = WorkflowTrace(
            workflow_id=f"wf_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}",
            plan_id=self.plan.id,
            session_id=session_id,
            started_at=datetime.now(),
        )

        # ── 1. Initialize shared context ────────────────────────
        shared_context: dict[str, Any] = {
            "query_text": input_text,
            "session_id": session_id,
            **kwargs,
        }

        node_index = 0
        iteration = 0

        # ── 2. Advance through node_order ───────────────────────
        while node_index < len(self.plan.node_order):
            if iteration >= self.plan.max_iterations:
                break

            node_def = self.plan.node_order[node_index]

            try:
                node = self.registry.get(node_def.node)
            except KeyError:
                self.trace.record(
                    node_def.id,
                    f"ERROR: node '{node_def.node}' not found in registry",
                )
                node_index += 1
                iteration += 1
                continue

            # ── 3. Setup input ──────────────────────────────────
            raw_input = {
                "query_text": input_text,
                "context": shared_context,
                "config": {
                    "temperature": node_def.config.temperature,
                    "max_candidates": node_def.config.max_candidates,
                    "max_rows": node_def.config.max_rows,
                    "timeout_ms": node_def.config.timeout_ms,
                    **node_def.config.extras,
                },
                "session_id": session_id,
            }
            node_input: NodeInput = await node.setup_input(raw_input)

            # ── 4. Execute node ─────────────────────────────────
            trace_entry = NodeTrace(
                node_id=node_def.id,
                node_name=node_def.node,
                input_summary=input_text[:200],
                started_at=datetime.now(),
            )

            try:
                output = await node.execute(node_input)
                trace_entry.completed_at = datetime.now()
                trace_entry.latency_ms = (
                    trace_entry.completed_at - trace_entry.started_at
                ).total_seconds() * 1000
                trace_entry.output_summary = str(output.result)[:200]
                trace_entry.error = (
                    output.errors[0] if output.errors else None
                )
            except Exception as e:
                output = None
                trace_entry.error = str(e)
                trace_entry.completed_at = datetime.now()

            # ── 5. Quality evaluation ───────────────────────────
            evaluation = await self.evaluate_result(node_def, output)
            trace_entry.evaluation = evaluation
            self.trace.nodes.append(trace_entry)

            # ── 6. Handle failure ───────────────────────────────
            if evaluation and not evaluation.passed:
                self.constraints.record_retry(node_def.id)

                if self.constraints.can_retry(node_def.id):
                    continue  # Retry the same node

                if node_def.on_failure == "abort":
                    await self.trace.persist()
                    return WorkflowResult(
                        status="failed",
                        context=shared_context,
                        trace=self.trace,
                        error=trace_entry.error or "Node evaluation failed",
                    )
                elif node_def.on_failure == "skip":
                    node_index += 1
                    iteration += 1
                    continue
                else:
                    # fallback: continue to next node
                    node_index += 1
                    iteration += 1
                    continue

            # ── 7. Update shared context ────────────────────────
            if output is not None:
                shared_context = await node.update_context(
                    output, shared_context
                )

            # ── 8. Handle reflection branching ──────────────────
            if node_def.id == "reflect" and evaluation and evaluation.needs_revision:
                # Jump to the 'revise' node. The loop is bounded by the
                # max_iterations check at the top of the while loop.
                revise_idx = next(
                    (i for i, nd in enumerate(self.plan.node_order) if nd.id == "revise"),
                    None,
                )
                if revise_idx is not None:
                    node_index = revise_idx
                    iteration += 1
                    continue
                # No revise node found — advance normally.

            node_index += 1
            iteration += 1

        # ── 9. Persist trace ────────────────────────────────────
        self.trace.completed_at = datetime.now()
        await self.trace.persist()

        return WorkflowResult(
            status="completed",
            context=shared_context,
            trace=self.trace,
            final_output=shared_context.get("last_execution"),
        )

    async def evaluate_result(
        self, node_def: Any, output: Any
    ) -> Evaluation:
        """Evaluate a node's output against configured quality metrics.

        Each metric name in the plan's evaluation config is dispatched to a
        real checker:

          - ``syntax_valid``: parse the SQL found in the output via sqlglot.
          - ``schema_compliant``: follow ValidateSQLNode's validation report.
          - ``execution_success``: fail on node errors or an error status.

        Checkers are lenient by design: when the information a metric needs is
        absent from the output, the metric passes with a "not applicable" note.
        Unknown metric names pass with "no checker registered". needs_revision
        is propagated from node output metadata (set by ReflectionNode to
        trigger the revise branch).
        """
        if not self.plan.evaluation.enabled:
            return Evaluation(passed=True)

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

    def _check_metric(self, metric_name: str, output: Any) -> MetricResult:
        """Dispatch a single metric name to its checker."""
        match metric_name:
            case "syntax_valid":
                return self._check_syntax_valid(output)
            case "schema_compliant":
                return self._check_schema_compliant(output)
            case "execution_success":
                return self._check_execution_success(output)
            case _:
                return MetricResult(
                    name=metric_name, passed=True, details="no checker registered"
                )

    @classmethod
    def _extract_sql(cls, output: Any) -> str | None:
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
    def _check_syntax_valid(cls, output: Any) -> MetricResult:
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
    def _check_schema_compliant(output: Any) -> MetricResult:
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
    def _check_execution_success(output: Any) -> MetricResult:
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
