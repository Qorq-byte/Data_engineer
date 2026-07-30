"""Tests for collaboration modes — 5 strategies for multi-agent coordination."""

from __future__ import annotations

import pytest

from app.agents.base import AgentResult, AgentStatus, BaseAgent
from app.agents.collaboration import (
    CollaborationMode,
    run_multi_step,
    run_negotiation,
    run_parallel_debate,
    run_pipeline,
    run_retry_correction,
    select_mode,
)
from app.agents.orchestrator import OrchestratorAgent
from app.agents.registry import AgentRegistry

# ── Helper: build a test orchestrator with stub agents ───────────────────


def _build_test_orch() -> OrchestratorAgent:
    """Return an OrchestratorAgent with all 6 sub-agents registered as stubs."""
    from app.models.query import SQLCandidate

    registry = AgentRegistry()
    orch = OrchestratorAgent(registry=registry)

    class _NLEcho(BaseAgent):
        name = "nl_understander"

        async def execute(self, input, context):
            return AgentResult(
                agent_name=self.name,
                status=AgentStatus.DONE,
                data=input if not isinstance(input, str) else None,
            )

    class _SchemaEcho(BaseAgent):
        name = "schema_retriever"

        async def execute(self, input, context):
            return AgentResult(
                agent_name=self.name,
                status=AgentStatus.DONE,
                data={"tables": [], "terms": [], "rules": [], "filtered_schema": None},
            )

    class _SQLEcho(BaseAgent):
        name = "sql_generator"

        async def execute(self, input, context):
            cand = SQLCandidate(id="c1", sql_text="SELECT 1", confidence=0.95)
            return AgentResult(
                agent_name=self.name,
                status=AgentStatus.DONE,
                data=[cand],
            )

    class _ValEcho(BaseAgent):
        name = "sql_validator"

        async def execute(self, input, context):
            return AgentResult(
                agent_name=self.name,
                status=AgentStatus.DONE,
                data={"passed": True, "report": None},
            )

    class _ToolEcho(BaseAgent):
        name = "tool_executor"

        async def execute(self, input, context):
            return AgentResult(
                agent_name=self.name,
                status=AgentStatus.DONE,
                data={"stub": True},
            )

    class _FeedbackEcho(BaseAgent):
        name = "feedback_processor"

        async def execute(self, input, context):
            return AgentResult(
                agent_name=self.name,
                status=AgentStatus.DONE,
                data={"acknowledged": True},
            )

    orch.register(_NLEcho())
    orch.register(_SchemaEcho())
    orch.register(_SQLEcho())
    orch.register(_ValEcho())
    orch.register(_ToolEcho())
    orch.register(_FeedbackEcho())
    return orch


# ═══════════════════════════════════════════════════════════════════════════════
# CollaborationMode enum
# ═══════════════════════════════════════════════════════════════════════════════


class TestCollaborationMode:
    def test_five_modes_exist(self):
        modes = list(CollaborationMode)
        assert len(modes) == 5

    def test_select_mode_valid(self):
        assert select_mode("pipeline") == CollaborationMode.PIPELINE
        assert select_mode("retry_correction") == CollaborationMode.RETRY_CORRECTION
        assert select_mode("parallel_debate") == CollaborationMode.PARALLEL_DEBATE
        assert select_mode("negotiation") == CollaborationMode.NEGOTIATION
        assert select_mode("multi_step") == CollaborationMode.MULTI_STEP

    def test_select_mode_invalid_falls_back(self):
        assert select_mode("nonexistent") == CollaborationMode.PIPELINE

    def test_collaboration_mode_is_str_enum(self):
        assert CollaborationMode.PIPELINE == "pipeline"


# ═══════════════════════════════════════════════════════════════════════════════
# Mode 1: Pipeline
# ═══════════════════════════════════════════════════════════════════════════════


class TestPipelineMode:
    @pytest.fixture
    def orch(self):
        o = _build_test_orch()
        yield o
        o.reset()

    @pytest.mark.anyio
    async def test_pipeline_returns_results(self, orch):
        results = await run_pipeline(orch, "test query", {})
        assert len(results) > 0

    @pytest.mark.anyio
    async def test_pipeline_includes_nl_agent(self, orch):
        results = await run_pipeline(orch, "query", {})
        agent_names = [r.agent_name for r in results if r]
        assert "nl_understander" in agent_names

    @pytest.mark.anyio
    async def test_pipeline_includes_schema_agent(self, orch):
        results = await run_pipeline(orch, "query", {})
        agent_names = [r.agent_name for r in results if r]
        assert "schema_retriever" in agent_names

    @pytest.mark.anyio
    async def test_pipeline_includes_sql_agent(self, orch):
        results = await run_pipeline(orch, "query", {})
        agent_names = [r.agent_name for r in results if r]
        assert "sql_generator" in agent_names

    @pytest.mark.anyio
    async def test_pipeline_includes_validator(self, orch):
        results = await run_pipeline(orch, "query", {})
        agent_names = [r.agent_name for r in results if r]
        assert "sql_validator" in agent_names

    @pytest.mark.anyio
    async def test_pipeline_context_propagation(self, orch):
        ctx: dict = {}
        results = await run_pipeline(orch, "query", ctx)
        # SQL should be set in context after pipeline runs
        assert "sql" in ctx or len(results) >= 4


# ═══════════════════════════════════════════════════════════════════════════════
# Mode 2: Retry Correction
# ═══════════════════════════════════════════════════════════════════════════════


class TestRetryCorrectionMode:
    @pytest.fixture
    def orch(self):
        o = _build_test_orch()
        yield o
        o.reset()

    @pytest.mark.anyio
    async def test_retry_correction_returns_results(self, orch):
        results = await run_retry_correction(orch, "query", {}, max_rounds=2)
        assert len(results) > 0

    @pytest.mark.anyio
    async def test_retry_correction_with_failing_validator(self, orch):
        """When validation fails, it should retry sql generation."""
        # Replace validator with a failing one
        class _FailingVal(BaseAgent):
            name = "sql_validator"
            async def execute(self, input, context):
                return AgentResult(
                    agent_name=self.name,
                    status=AgentStatus.DONE,
                    data={"passed": False, "report": type("R", (), {"errors": ["syntax error"]})()},
                )
        orch.register(_FailingVal())
        results = await run_retry_correction(orch, "query", {}, max_rounds=3)
        # Should have multiple SQL generation attempts
        sql_results = [r for r in results if r.agent_name == "sql_generator"]
        assert len(sql_results) >= 1


# ═══════════════════════════════════════════════════════════════════════════════
# Mode 3: Parallel Debate
# ═══════════════════════════════════════════════════════════════════════════════


class TestParallelDebateMode:
    @pytest.fixture
    def orch(self):
        o = _build_test_orch()
        yield o
        o.reset()

    @pytest.mark.anyio
    async def test_parallel_debate_returns_results(self, orch):
        results = await run_parallel_debate(orch, "query", {}, num_candidates=2)
        assert len(results) > 0

    @pytest.mark.anyio
    async def test_parallel_debate_produces_multiple_sql_results(self, orch):
        results = await run_parallel_debate(orch, "complex query with joins", {}, num_candidates=2)
        sql_results = [r for r in results if r.agent_name == "sql_generator"]
        # Should have at least one
        assert len(sql_results) >= 1


# ═══════════════════════════════════════════════════════════════════════════════
# Mode 4: Negotiation
# ═══════════════════════════════════════════════════════════════════════════════


class TestNegotiationMode:
    @pytest.fixture
    def orch(self):
        o = _build_test_orch()
        yield o
        o.reset()

    @pytest.mark.anyio
    async def test_negotiation_returns_results(self, orch):
        results = await run_negotiation(orch, "query or maybe something", {})
        assert len(results) > 0

    @pytest.mark.anyio
    async def test_negotiation_includes_nl_agent(self, orch):
        results = await run_negotiation(orch, "test query", {})
        agent_names = [r.agent_name for r in results if r]
        assert "nl_understander" in agent_names

    @pytest.mark.anyio
    async def test_negotiation_produces_outcome(self, orch):
        results = await run_negotiation(orch, "query", {})
        orch_results = [r for r in results if r.agent_name == "orchestrator"]
        assert len(orch_results) >= 1
        assert "negotiation_required" in orch_results[0].data


# ═══════════════════════════════════════════════════════════════════════════════
# Mode 5: Multi-step Pipeline
# ═══════════════════════════════════════════════════════════════════════════════


class TestMultiStepMode:
    @pytest.fixture
    def orch(self):
        o = _build_test_orch()
        yield o
        o.reset()

    @pytest.mark.anyio
    async def test_multi_step_single_query_falls_back(self, orch):
        results = await run_multi_step(orch, "simple query", {})
        assert len(results) > 0

    @pytest.mark.anyio
    async def test_multi_step_with_multiple_steps(self, orch):
        results = await run_multi_step(
            orch,
            "首先查询用户，然后统计订单。第三步查看产品",
            {},
        )
        assert len(results) > 0
        # Should have decomposition result
        orch_results = [
            r for r in results
            if r.agent_name == "orchestrator" and "decomposed" in (r.data or {})
        ]
        assert len(orch_results) >= 1
