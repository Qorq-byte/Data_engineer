"""Tests for AgentTraceCollector — trace recording, OrchestratorTrace, SSE events."""

from __future__ import annotations

import pytest

from app.agents.trace import (
    AgentTraceCollector,
    AgentTraceEntry,
    OrchestratorTrace,
    agent_trace_collector,
)

# ═══════════════════════════════════════════════════════════════════════════════
# AgentTraceEntry
# ═══════════════════════════════════════════════════════════════════════════════


class TestAgentTraceEntry:
    def test_defaults(self):
        entry = AgentTraceEntry(agent_name="nl")
        assert entry.agent_name == "nl"
        assert entry.input_summary == ""
        assert entry.tools_called == []
        assert entry.latency_ms == 0.0

    def test_full_entry(self):
        entry = AgentTraceEntry(
            agent_name="sql_generator",
            input_summary="Generate SQL for: 上个月订单",
            output_summary="SELECT COUNT(*) FROM orders WHERE...",
            tools_called=["llm_completion", "build_prompt"],
            latency_ms=234.5,
            model="claude-sonnet-4",
            tokens_used=450,
            status="done",
        )
        assert entry.tools_called == ["llm_completion", "build_prompt"]
        assert entry.model == "claude-sonnet-4"
        assert entry.tokens_used == 450

    def test_error_entry(self):
        entry = AgentTraceEntry(
            agent_name="sql_validator",
            status="error",
            errors=["Syntax error near 'FROM'"],
        )
        assert len(entry.errors) == 1


# ═══════════════════════════════════════════════════════════════════════════════
# OrchestratorTrace
# ═══════════════════════════════════════════════════════════════════════════════


class TestOrchestratorTrace:
    def test_defaults(self):
        trace = OrchestratorTrace(trace_id="abc")
        assert trace.trace_id == "abc"
        assert trace.agent_traces == []
        assert trace.collaboration_mode == ""
        assert trace.total_latency_ms == 0.0

    def test_with_traces(self):
        entries = [
            AgentTraceEntry(agent_name="nl"),
            AgentTraceEntry(agent_name="sql_generator"),
        ]
        trace = OrchestratorTrace(
            trace_id="t1",
            agent_traces=entries,
            collaboration_mode="pipeline",
            total_latency_ms=500.0,
        )
        assert len(trace.agent_traces) == 2
        assert trace.collaboration_mode == "pipeline"

    def test_handoffs_collection(self):
        trace = OrchestratorTrace(
            trace_id="t2",
            handoffs=[
                {"from": "orch", "to": "nl", "msg_type": "handoff"},
                {"from": "orch", "to": "sql", "msg_type": "handoff"},
            ],
        )
        assert len(trace.handoffs) == 2

    def test_task_breakdown(self):
        trace = OrchestratorTrace(
            trace_id="t3",
            task_breakdown={"complexity": "simple", "subtasks": []},
        )
        assert trace.task_breakdown["complexity"] == "simple"


# ═══════════════════════════════════════════════════════════════════════════════
# AgentTraceCollector — lifecycle
# ═══════════════════════════════════════════════════════════════════════════════


class TestAgentTraceCollector:
    @pytest.fixture
    def collector(self):
        c = AgentTraceCollector()
        yield c
        c.reset()

    def test_start_trace_generates_id(self, collector):
        tid = collector.start_trace(query="test query")
        assert tid
        assert len(tid) > 0
        assert collector.current_trace is not None

    def test_start_trace_with_explicit_id(self, collector):
        tid = collector.start_trace(trace_id="my_trace_id", query="q")
        assert tid == "my_trace_id"

    def test_record_agent(self, collector):
        collector.start_trace(query="q")
        entry = AgentTraceEntry(agent_name="nl", latency_ms=10.0)
        collector.record_agent(entry)
        trace = collector.finish_trace()
        assert len(trace.agent_traces) == 1
        assert trace.agent_traces[0].agent_name == "nl"

    def test_record_multiple_agents(self, collector):
        collector.start_trace(query="q")
        collector.record_agent(AgentTraceEntry(agent_name="nl", latency_ms=5.0))
        collector.record_agent(AgentTraceEntry(agent_name="sql_generator", latency_ms=50.0))
        trace = collector.finish_trace()
        assert len(trace.agent_traces) == 2

    def test_record_handoff(self, collector):
        collector.start_trace(query="q")
        collector.record_handoff("orch", "nl", "handoff")
        collector.record_handoff("orch", "sql_generator", "handoff")
        trace = collector.finish_trace()
        assert len(trace.handoffs) == 2

    def test_record_conflict(self, collector):
        collector.start_trace(query="q")
        collector.record_conflict({"agent": "sql_generator", "resolution": "retry"})
        trace = collector.finish_trace()
        assert len(trace.conflict_resolutions) == 1

    def test_set_task_breakdown(self, collector):
        collector.start_trace(query="q")
        collector.set_task_breakdown({"complexity": "medium"})
        trace = collector.finish_trace()
        assert trace.task_breakdown["complexity"] == "medium"

    def test_set_collaboration_mode(self, collector):
        collector.start_trace(query="q")
        collector.set_collaboration_mode("parallel_debate")
        trace = collector.finish_trace()
        assert trace.collaboration_mode == "parallel_debate"

    def test_finish_trace_sets_timestamps(self, collector):
        collector.start_trace(query="q")
        collector.record_agent(AgentTraceEntry(agent_name="nl"))
        trace = collector.finish_trace()
        assert trace.started_at
        assert trace.completed_at
        assert trace.total_latency_ms >= 0

    def test_finish_trace_calculates_cost(self, collector):
        collector.start_trace(query="q")
        collector.record_agent(AgentTraceEntry(agent_name="sql", tokens_used=2000))
        trace = collector.finish_trace()
        assert trace.llm_cost_estimate_usd > 0  # 2000 * 0.50 / 1e6

    def test_finish_without_start_returns_empty_trace(self, collector):
        trace = collector.finish_trace()
        assert trace.trace_id == "no_trace"

    def test_reset_clears_state(self, collector):
        collector.start_trace(query="q")
        collector.record_agent(AgentTraceEntry(agent_name="nl"))
        collector.reset()
        assert collector.current_trace is None


# ═══════════════════════════════════════════════════════════════════════════════
# SSE event generation
# ═══════════════════════════════════════════════════════════════════════════════


class TestSSEEvents:
    @pytest.fixture
    def collector(self):
        c = AgentTraceCollector()
        yield c
        c.reset()

    def test_empty_collector_produces_no_events(self, collector):
        events = collector.to_sse_events()
        assert events == []

    def test_agent_trace_events(self, collector):
        collector.start_trace(trace_id="t1", query="q")
        collector.record_agent(AgentTraceEntry(
            agent_name="nl",
            input_summary="query text",
            output_summary="SQR(...)",
            tools_called=["parse"],
            latency_ms=12.0,
            status="done",
        ))
        collector.record_agent(AgentTraceEntry(
            agent_name="sql_generator",
            input_summary="SQR",
            output_summary="SELECT...",
            tools_called=["llm"],
            latency_ms=300.0,
            model="sonnet",
            tokens_used=400,
            status="done",
        ))
        collector.finish_trace()
        events = collector.to_sse_events()
        # Should have agent_trace events (no collaboration event since mode is empty)
        agent_trace_events = [e for e in events if "event: agent_trace" in e]
        assert len(agent_trace_events) == 2

    def test_collaboration_mode_event(self, collector):
        collector.start_trace(trace_id="t2", query="q")
        collector.set_collaboration_mode("pipeline")
        collector.record_agent(AgentTraceEntry(agent_name="nl"))
        collector.finish_trace()
        events = collector.to_sse_events()
        collab_events = [e for e in events if "event: collaboration" in e]
        assert len(collab_events) == 1
        assert "pipeline" in collab_events[0]

    def test_handoff_events(self, collector):
        collector.start_trace(trace_id="t3", query="q")
        collector.record_handoff("orch", "nl", "handoff")
        collector.record_handoff("orch", "sql", "handoff")
        collector.finish_trace()
        events = collector.to_sse_events()
        handoff_events = [e for e in events if "event: agent_handoff" in e]
        assert len(handoff_events) == 2

    def test_sse_format_is_valid(self, collector):
        collector.start_trace(trace_id="t4", query="q")
        collector.set_collaboration_mode("pipeline")
        collector.record_agent(AgentTraceEntry(agent_name="nl", status="done"))
        collector.finish_trace()
        events = collector.to_sse_events()
        for event in events:
            assert event.startswith("event: "), f"Bad event: {event[:50]}"
            assert "\ndata: " in event, f"Missing data field: {event[:50]}"
            assert event.endswith("\n\n"), f"Missing double newline: {event[-10:]}"


# ═══════════════════════════════════════════════════════════════════════════════
# Module-level singleton
# ═══════════════════════════════════════════════════════════════════════════════


class TestTraceCollectorSingleton:
    def test_singleton_exists(self):
        assert isinstance(agent_trace_collector, AgentTraceCollector)

    def test_singleton_is_shared(self):
        from app.agents.trace import agent_trace_collector as tc2
        assert agent_trace_collector is tc2
