"""Tests for OrchestratorAgent — task decomposition, agent dispatch, synthesis."""

from __future__ import annotations

import pytest

from app.agents.base import AgentResult, AgentStatus, BaseAgent
from app.agents.orchestrator import (
    OrchestratorAgent,
    OrchestratorResult,
    TaskBreakdown,
)

# ── Stub agents for testing ─────────────────────────────────────────────


class _EchoAgent(BaseAgent):
    """Returns whatever was passed in."""

    name = "echo"

    async def execute(self, input, context):
        return AgentResult(
            agent_name=self.name,
            status=AgentStatus.DONE,
            data=input,
        )


class _FailingAgent(BaseAgent):
    """Always returns an error."""

    name = "failing"

    async def execute(self, input, context):
        return AgentResult(
            agent_name=self.name,
            status=AgentStatus.ERROR,
            errors=["always fails"],
        )


# ═══════════════════════════════════════════════════════════════════════════════
# TaskBreakdown
# ═══════════════════════════════════════════════════════════════════════════════


class TestTaskBreakdown:
    def test_defaults(self):
        tb = TaskBreakdown(original_query="test")
        assert tb.original_query == "test"
        assert tb.complexity == "simple"
        assert tb.collaboration_mode == "pipeline"

    def test_complex(self):
        tb = TaskBreakdown(
            original_query="complex query",
            complexity="complex",
            collaboration_mode="parallel_debate",
            reason="lots of joins",
        )
        assert tb.complexity == "complex"
        assert tb.reason == "lots of joins"


# ═══════════════════════════════════════════════════════════════════════════════
# OrchestratorResult
# ═══════════════════════════════════════════════════════════════════════════════


class TestOrchestratorResult:
    def test_defaults(self):
        r = OrchestratorResult(trace_id="abc")
        assert r.trace_id == "abc"
        assert r.status == "completed"
        assert r.agent_results == []

    def test_with_results(self):
        r = OrchestratorResult(
            trace_id="t1",
            agent_results=[
                AgentResult(agent_name="nl", status=AgentStatus.DONE, data="ok"),
                AgentResult(agent_name="sql", status=AgentStatus.ERROR, errors=["bad"]),
            ],
        )
        assert len(r.agent_results) == 2


# ═══════════════════════════════════════════════════════════════════════════════
# OrchestratorAgent — complexity analysis
# ═══════════════════════════════════════════════════════════════════════════════


class TestComplexityAnalysis:
    @pytest.fixture
    def orch(self):
        o = OrchestratorAgent()
        yield o
        o.reset()

    def test_simple_query(self, orch):
        tb = orch._analyze_complexity("查询用户")
        assert tb.complexity == "simple"
        assert tb.collaboration_mode == "pipeline"

    def test_medium_join(self, orch):
        tb = orch._analyze_complexity("查询用户和订单的关联信息")
        assert tb.complexity in ("medium", "complex")

    def test_medium_group_by(self, orch):
        tb = orch._analyze_complexity("统计每个用户的订单总数")
        assert tb.complexity == "medium"

    def test_complex_ranking(self, orch):
        tb = orch._analyze_complexity("排名前10的用户")
        assert tb.complexity in ("medium", "complex")

    def test_complex_window(self, orch):
        tb = orch._analyze_complexity("使用窗口函数查询")
        assert tb.complexity == "complex"

    def test_complex_vs_compare(self, orch):
        tb = orch._analyze_complexity("对比去年同期的订单数据")
        assert tb.complexity == "complex"

    def test_ambiguous_or(self, orch):
        tb = orch._analyze_complexity("查询用户或者订单数据")
        assert tb.complexity == "ambiguous"
        assert tb.collaboration_mode == "negotiation"

    def test_ambiguous_maybe(self, orch):
        tb = orch._analyze_complexity("大概查询一下订单")
        assert tb.complexity == "ambiguous"

    def test_multi_step_then(self, orch):
        tb = orch._analyze_complexity("先查询用户，然后统计订单")
        assert tb.complexity == "multi_step"
        assert tb.collaboration_mode == "multi_step"

    def test_english_group_by(self, orch):
        tb = orch._analyze_complexity("find users group by status with count")
        assert tb.complexity in ("medium", "complex")

    def test_english_ambiguous(self, orch):
        tb = orch._analyze_complexity("maybe find users or orders")
        assert tb.complexity == "ambiguous"


# ═══════════════════════════════════════════════════════════════════════════════
# OrchestratorAgent — registration
# ═══════════════════════════════════════════════════════════════════════════════


class TestOrchestratorRegistration:
    def test_register_agent(self):
        from app.agents.registry import AgentRegistry
        registry = AgentRegistry()
        orch = OrchestratorAgent(registry=registry)
        orch.register(_EchoAgent())
        assert "echo" in registry.list_all()

    def test_get_agent(self):
        from app.agents.registry import AgentRegistry
        registry = AgentRegistry()
        orch = OrchestratorAgent(registry=registry)
        orch.register(_EchoAgent())
        agent = orch.get_agent("echo")
        assert agent is not None
        assert agent.name == "echo"

    def test_get_nonexistent(self):
        orch = OrchestratorAgent()
        assert orch.get_agent("nonexistent") is None


# ═══════════════════════════════════════════════════════════════════════════════
# OrchestratorAgent — run
# ═══════════════════════════════════════════════════════════════════════════════


class TestOrchestratorRun:
    @pytest.fixture
    def orch(self):
        from app.agents.registry import AgentRegistry
        registry = AgentRegistry()
        o = OrchestratorAgent(registry=registry)
        # Register all 6 agents as stubs
        o.register(_EchoAgent())
        # Use different stub agents for different names
        class _NLEcho(_EchoAgent):
            name = "nl_understander"

        class _SchemaEcho(_EchoAgent):
            name = "schema_retriever"

        class _SQLEcho(_EchoAgent):
            name = "sql_generator"
            async def execute(self, input, context):
                # Return a list of mock candidates
                from app.models.query import SQLCandidate
                candidate = SQLCandidate(
                    id="c1",
                    sql_text="SELECT 1",
                    confidence=0.95,
                )
                return AgentResult(
                    agent_name=self.name,
                    status=AgentStatus.DONE,
                    data=[candidate],
                )

        class _ValEcho(_EchoAgent):
            name = "sql_validator"
            async def execute(self, input, context):
                return AgentResult(
                    agent_name=self.name,
                    status=AgentStatus.DONE,
                    data={"passed": True, "report": None},
                )

        class _ToolEcho(_EchoAgent):
            name = "tool_executor"

        class _FeedbackEcho(_EchoAgent):
            name = "feedback_processor"

        o.register(_NLEcho())
        o.register(_SchemaEcho())
        o.register(_SQLEcho())
        o.register(_ValEcho())
        o.register(_ToolEcho())
        o.register(_FeedbackEcho())
        yield o
        o.reset()
        registry.reset()

    @pytest.mark.anyio
    async def test_run_simple_query(self, orch):
        result = await orch.run("查询用户", {})
        assert result.status in ("completed", "partial")
        assert len(result.agent_results) > 0

    @pytest.mark.anyio
    async def test_run_returns_trace_id(self, orch):
        result = await orch.run("test query", {})
        assert result.trace_id
        assert len(result.trace_id) == 12

    @pytest.mark.anyio
    async def test_run_returns_collaboration_mode(self, orch):
        result = await orch.run("查询用户", {})
        assert result.collaboration_mode_used == "pipeline"

    @pytest.mark.anyio
    async def test_run_with_explicit_mode(self, orch):
        result = await orch.run("查询用户或者订单", {}, mode="negotiation")
        assert result.collaboration_mode_used == "negotiation"

    @pytest.mark.anyio
    async def test_run_returns_task_breakdown(self, orch):
        result = await orch.run("查询用户", {})
        assert result.task_breakdown is not None
        assert result.task_breakdown.original_query == "查询用户"

    @pytest.mark.anyio
    async def test_run_returns_total_latency(self, orch):
        result = await orch.run("test", {})
        assert result.total_latency_ms >= 0

    @pytest.mark.anyio
    async def test_run_final_output_has_query_text(self, orch):
        result = await orch.run("查询订单", {})
        assert result.final_output["query_text"] == "查询订单"

    @pytest.mark.anyio
    async def test_execute_base_agent_contract(self, orch):
        result = await orch.execute("查询用户", {})
        assert result.status == AgentStatus.DONE
        assert isinstance(result.data, OrchestratorResult)

    @pytest.mark.anyio
    async def test_execute_with_dict_input(self, orch):
        result = await orch.execute({"query_text": "find users"}, {})
        assert result.status == AgentStatus.DONE

    @pytest.mark.anyio
    async def test_reset(self, orch):
        orch.reset()
        assert orch.status == AgentStatus.IDLE
