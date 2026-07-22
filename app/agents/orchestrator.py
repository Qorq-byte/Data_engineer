"""Orchestrator Agent — central dispatcher for the multi-agent system.

See SPEC §4.11.3 (Orchestrator design) and implementation plan §4.11.

The Orchestrator is the entry point of the multi-agent system. It:
  1. Analyses query complexity (rule-based, no LLM required)
  2. Selects agents and a collaboration mode
  3. Dispatches to the chosen mode's runner function
  4. Synthesises results and resolves conflicts

It does NOT replace WorkflowRunner — it is an alternative execution strategy
that can be called from within an AgenticNode or directly from the API layer.
"""

from __future__ import annotations

import re
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from app.agents.base import AgentResult, AgentStatus, BaseAgent
from app.agents.bus import AgentBus, agent_bus
from app.agents.registry import AgentRegistry, agent_registry

# ── Data models ─────────────────────────────────────────────────────────


@dataclass
class TaskBreakdown:
    """Result of complexity analysis on a user query."""

    original_query: str
    subtasks: list[dict[str, Any]] = field(default_factory=list)
    complexity: str = "simple"  # simple | medium | complex | ambiguous | multi_step
    collaboration_mode: str = "pipeline"
    reason: str = ""


@dataclass
class OrchestratorResult:
    """Complete result of an orchestrated multi-agent run."""

    trace_id: str
    status: str = "completed"  # completed | failed | partial
    task_breakdown: TaskBreakdown = field(default_factory=lambda: TaskBreakdown(original_query=""))
    agent_results: list[AgentResult] = field(default_factory=list)
    final_output: dict[str, Any] = field(default_factory=dict)
    conflict_resolutions: list[dict[str, Any]] = field(default_factory=list)
    total_latency_ms: float = 0.0
    collaboration_mode_used: str = "pipeline"


# ── Orchestrator ────────────────────────────────────────────────────────


class OrchestratorAgent(BaseAgent):
    """Central dispatcher — analyses queries and coordinates sub-agents.

    Usage::

        orch = OrchestratorAgent(registry=agent_registry, bus=agent_bus)
        orch.register(NLUnderstandingAgent())
        orch.register(SchemaRetrievalAgent())
        # ...
        result = await orch.run("上个月订单总数", context={})
        print(result.final_output)
    """

    name = "orchestrator"
    description = (
        "Central dispatcher: analyses query complexity, selects agents and "
        "collaboration mode, dispatches tasks, synthesises results."
    )

    def __init__(
        self,
        registry: AgentRegistry | None = None,
        bus: AgentBus | None = None,
        default_mode: str = "pipeline",
        config: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(config)
        self._registry = registry or agent_registry
        self._bus = bus or agent_bus
        self._default_mode = default_mode

    # ── Registration ───────────────────────────────────────────────────

    def register(self, agent: BaseAgent) -> None:
        """Register a sub-agent in the registry."""
        self._registry.register(agent)

    def get_agent(self, name: str) -> BaseAgent | None:
        """Look up a registered agent by name."""
        return self._registry.get(name)

    # ── Main entry point ───────────────────────────────────────────────

    async def run(
        self,
        query_text: str,
        context: dict[str, Any] | None = None,
        *,
        mode: str | None = None,
    ) -> OrchestratorResult:
        """Execute the multi-agent pipeline for *query_text*.

        Args:
            query_text: The user's natural language query.
            context: Shared context dict (domain_id, db_id, schema, session_id, etc.).
            mode: Override the auto-selected collaboration mode.

        Returns:
            OrchestratorResult with trace, agent results, and final output.
        """
        ctx = dict(context or {})
        ctx["query_text"] = query_text

        trace_id = uuid.uuid4().hex[:12]
        t0 = time.perf_counter()

        # ── Step 1: Complexity analysis ────────────────────────────
        breakdown = self._analyze_complexity(query_text)
        selected_mode = mode or breakdown.collaboration_mode or self._default_mode

        # ── Step 2: Dispatch via collaboration mode ─────────────────
        from app.agents.collaboration import (
            run_multi_step,
            run_negotiation,
            run_parallel_debate,
            run_pipeline,
            run_retry_correction,
        )

        mode_runners = {
            "pipeline": run_pipeline,
            "retry_correction": run_retry_correction,
            "parallel_debate": run_parallel_debate,
            "negotiation": run_negotiation,
            "multi_step": run_multi_step,
        }

        runner = mode_runners.get(selected_mode, run_pipeline)
        try:
            agent_results = await runner(self, query_text, ctx)
        except Exception as exc:
            elapsed = (time.perf_counter() - t0) * 1000
            return OrchestratorResult(
                trace_id=trace_id,
                status="failed",
                task_breakdown=breakdown,
                final_output={"error": str(exc)},
                total_latency_ms=round(elapsed, 2),
                collaboration_mode_used=selected_mode,
            )

        # ── Step 3: Synthesise results ─────────────────────────────
        final_output = self._synthesize(agent_results, ctx)
        conflict_resolutions = self._collect_conflicts(agent_results)

        elapsed = (time.perf_counter() - t0) * 1000
        return OrchestratorResult(
            trace_id=trace_id,
            status=(
                "completed"
                if all(r.status == AgentStatus.DONE for r in agent_results if r)
                else "partial"
            ),
            task_breakdown=breakdown,
            agent_results=agent_results,
            final_output=final_output,
            conflict_resolutions=conflict_resolutions,
            total_latency_ms=round(elapsed, 2),
            collaboration_mode_used=selected_mode,
        )

    # ── Complexity analysis (rule-based, no LLM) ────────────────────────

    def _analyze_complexity(self, query_text: str) -> TaskBreakdown:
        """Analyse query text to determine complexity and collaboration mode.

        Pure rules — no LLM call.  Fast (< 1 ms) and deterministic.
        """
        qt = query_text.lower()

        # Multi-step indicators
        multi_step_patterns = [
            r"然后|再|接着|之后|然后|第二步|第三步",
            r"first.*then|after.*then|step\s*\d|next.*then",
            r"分别|各自|separately|respectively|each",
        ]
        if any(re.search(p, qt) for p in multi_step_patterns):
            return TaskBreakdown(
                original_query=query_text,
                complexity="multi_step",
                collaboration_mode="multi_step",
                reason="Multiple sequential steps detected",
            )

        # Ambiguity indicators
        ambiguity_patterns = [
            r"或者|还是|要么|不确定|大概|也许|可能",
            r"or maybe|either|not sure|perhaps|maybe|approximately",
        ]
        if any(re.search(p, qt) for p in ambiguity_patterns):
            return TaskBreakdown(
                original_query=query_text,
                complexity="ambiguous",
                collaboration_mode="negotiation",
                reason="Ambiguity markers detected — negotiation recommended",
            )

        # Complex query indicators
        complex_patterns = [
            r"\bjoin\b|\bcte\b|\bwindow\b|子查询|嵌套|窗口函数|递归",
            r"\bgroup\s+by\b.*\bhaving\b|\bpartition\b|\bover\b",
            r"对比|比较|同比|环比|vs|versus|compared?\s+to",
            r"排名|排行|前\d+|top\s*\d+|rank|dense_rank",
        ]
        if any(re.search(p, qt) for p in complex_patterns):
            return TaskBreakdown(
                original_query=query_text,
                complexity="complex",
                collaboration_mode="parallel_debate",
                reason="Complex query patterns detected — parallel debate recommended",
            )

        # Medium complexity
        medium_patterns = [
            r"\bjoin\b|关联|联合",
            r"\bgroup\s+by\b|分组|汇总|统计|sum|avg|count",
            r"趋势|变化|走势|trend|monthly|daily|weekly",
        ]
        if any(re.search(p, qt) for p in medium_patterns):
            return TaskBreakdown(
                original_query=query_text,
                complexity="medium",
                collaboration_mode="pipeline",
                reason="Medium complexity — standard pipeline",
            )

        # Default: simple
        return TaskBreakdown(
            original_query=query_text,
            complexity="simple",
            collaboration_mode="pipeline",
            reason="Simple query — standard pipeline",
        )

    # ── Synthesis ──────────────────────────────────────────────────────

    def _synthesize(
        self,
        results: list[AgentResult],
        context: dict[str, Any],
    ) -> dict[str, Any]:
        """Combine agent results into a single output dict."""
        output: dict[str, Any] = {
            "query_text": context.get("query_text", ""),
        }

        for r in results:
            if r is None:
                continue
            if r.data is None:
                continue

            # Collect key outputs from known agents
            if r.agent_name == "nl_understander" and hasattr(r.data, "intent"):
                output["sqr"] = r.data
            elif r.agent_name == "schema_retriever" and isinstance(r.data, dict):
                output["schema"] = r.data.get("filtered_schema")
                output["tables"] = r.data.get("tables", [])
                output["terms"] = r.data.get("terms", [])
                output["rules"] = r.data.get("rules", [])
            elif r.agent_name == "sql_generator" and isinstance(r.data, list):
                output["candidates"] = r.data
                if r.data:
                    sql_val = (
                        r.data[0].sql_text
                        if hasattr(r.data[0], "sql_text")
                        else str(r.data[0])
                    )
                    output["sql"] = sql_val
            elif r.agent_name == "sql_validator" and isinstance(r.data, dict):
                output["validation"] = r.data.get("report")
                output["validation_passed"] = r.data.get("passed", False)
            elif r.agent_name == "tool_executor" and isinstance(r.data, dict):
                output["execution_result"] = r.data
            elif r.agent_name == "feedback_processor" and isinstance(r.data, dict):
                output["feedback"] = r.data

        return output

    def _collect_conflicts(self, results: list[AgentResult]) -> list[dict[str, Any]]:
        """Collect error/conflict resolutions from agent results."""
        conflicts: list[dict[str, Any]] = []
        for r in results:
            if r is None:
                continue
            if r.status == AgentStatus.ERROR and r.errors:
                conflicts.append({
                    "agent": r.agent_name,
                    "type": "agent_error",
                    "errors": r.errors,
                    "resolution": "logged",
                })
        return conflicts

    # ── BaseAgent contract ─────────────────────────────────────────────

    async def execute(self, input: Any, context: dict[str, Any]) -> AgentResult:
        """Run the orchestration pipeline (implements BaseAgent.execute)."""
        query_text = ""
        if isinstance(input, str):
            query_text = input
        elif isinstance(input, dict):
            query_text = input.get("query_text") or input.get("nl_text") or ""

        orch_result = await self.run(query_text, context)
        return AgentResult(
            agent_name=self.name,
            status=AgentStatus.DONE if orch_result.status == "completed" else AgentStatus.ERROR,
            data=orch_result,
            metadata={
                "trace_id": orch_result.trace_id,
                "collaboration_mode": orch_result.collaboration_mode_used,
                "total_latency_ms": orch_result.total_latency_ms,
            },
        )

    def reset(self) -> None:
        """Reset the orchestrator to IDLE state."""
        super().reset()
