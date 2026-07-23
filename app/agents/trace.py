"""Agent observability — trace collection and SSE event generation.

See SPEC §4.11.8 (Agent observability) for the full specification.

The trace collector captures per-agent execution data (input/output,
tools called, latency, model, tokens) and produces ``OrchestratorTrace``
objects that can be serialized to JSON or emitted as SSE events for
real-time front-end visibility.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import uuid4


@dataclass
class AgentTraceEntry:
    """A single agent execution record within a trace.

    Mirrors the per-agent block in SPEC §4.11.8 Agent Trace JSON.
    """

    agent_name: str
    input_summary: str = ""  # Truncated input for display
    output_summary: str = ""  # Truncated output for display
    tools_called: list[str] = field(default_factory=list)
    latency_ms: float = 0.0
    model: str | None = None
    tokens_used: int = 0
    status: str = "done"  # "done" | "error"
    errors: list[str] = field(default_factory=list)


@dataclass
class OrchestratorTrace:
    """Complete trace of an orchestrated multi-agent run.

    Mirrors the top-level Agent Trace structure in SPEC §4.11.8.
    """

    trace_id: str
    task_breakdown: dict = field(default_factory=dict)
    agent_traces: list[AgentTraceEntry] = field(default_factory=list)
    collaboration_mode: str = ""
    conflict_resolutions: list[dict] = field(default_factory=list)
    handoffs: list[dict] = field(default_factory=list)
    total_latency_ms: float = 0.0
    llm_cost_estimate_usd: float = 0.0
    started_at: str = ""
    completed_at: str = ""


class AgentTraceCollector:
    """Collects per-agent traces during an orchestrated run.

    Usage::

        collector = AgentTraceCollector()
        collector.start_trace(trace_id, query="上个月订单总数")

        collector.record_agent(AgentTraceEntry(
            agent_name="nl_understander",
            input_summary="上个月订单总数",
            output_summary="SQR(intent=AGGREGATE, ...)",
            tools_called=["parse_language", "extract_time_entities"],
            latency_ms=45.2,
            status="done",
        ))

        trace: OrchestratorTrace = collector.finish_trace()
        events: list[str] = collector.to_sse_events()
    """

    def __init__(self) -> None:
        self._trace: OrchestratorTrace | None = None
        self._t0: float = 0.0
        self._query: str = ""

    # ── Lifecycle ─────────────────────────────────────────────────────

    def start_trace(self, trace_id: str | None = None, query: str = "") -> str:
        """Begin a new trace. Returns the trace_id (auto-generated if not provided)."""
        tid = trace_id or uuid4().hex[:12]
        self._t0 = time.perf_counter()
        self._query = query
        self._trace = OrchestratorTrace(
            trace_id=tid,
            started_at=datetime.now(UTC).isoformat(),
        )
        return tid

    def record_agent(self, entry: AgentTraceEntry) -> None:
        """Append an agent execution record to the current trace."""
        if self._trace is not None:
            self._trace.agent_traces.append(entry)

    def record_handoff(self, from_agent: str, to_agent: str, msg_type: str = "handoff") -> None:
        """Record an inter-agent handoff event."""
        if self._trace is not None:
            self._trace.handoffs.append({
                "from": from_agent,
                "to": to_agent,
                "msg_type": msg_type,
                "timestamp": datetime.now(UTC).isoformat(),
            })

    def record_conflict(self, resolution: dict) -> None:
        """Record a conflict resolution event."""
        if self._trace is not None:
            self._trace.conflict_resolutions.append(resolution)

    def set_task_breakdown(self, breakdown: dict) -> None:
        """Set the task breakdown for the current trace."""
        if self._trace is not None:
            self._trace.task_breakdown = breakdown

    def set_collaboration_mode(self, mode: str) -> None:
        """Set the collaboration mode used."""
        if self._trace is not None:
            self._trace.collaboration_mode = mode

    def finish_trace(self) -> OrchestratorTrace:
        """Finalize the trace with total latency and completion time. Returns the trace."""
        if self._trace is None:
            return OrchestratorTrace(trace_id="no_trace")
        self._trace.total_latency_ms = (time.perf_counter() - self._t0) * 1000
        self._trace.completed_at = datetime.now(UTC).isoformat()
        # Estimate LLM cost (simple: $0.50/1M tokens blended)
        total_tokens = sum(e.tokens_used for e in self._trace.agent_traces)
        self._trace.llm_cost_estimate_usd = total_tokens / 1_000_000 * 0.50
        return self._trace

    # ── SSE event generation ──────────────────────────────────────────

    def to_sse_events(self) -> list[str]:
        """Convert the current trace into SSE-formatted event strings.

        Each agent trace entry becomes an ``agent_trace`` event.
        Handoffs become ``agent_handoff`` events.
        """
        if self._trace is None:
            return []

        events: list[str] = []
        tid = self._trace.trace_id

        # Collaboration mode event
        if self._trace.collaboration_mode:
            collab_payload = json.dumps(
                {"mode": self._trace.collaboration_mode, "trace_id": tid},
                ensure_ascii=False,
            )
            events.append(f"event: collaboration\ndata: {collab_payload}\n\n")

        # Per-agent trace events
        for entry in self._trace.agent_traces:
            payload = {
                "agent": entry.agent_name,
                "status": entry.status,
                "input": entry.input_summary,
                "output": entry.output_summary,
                "tools_called": entry.tools_called,
                "latency_ms": entry.latency_ms,
                "model": entry.model,
                "tokens_used": entry.tokens_used,
                "errors": entry.errors,
                "trace_id": tid,
            }
            events.append(
                f"event: agent_trace\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"
            )

        # Handoff events
        for h in self._trace.handoffs:
            payload = {**h, "trace_id": tid}
            events.append(
                f"event: agent_handoff\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"
            )

        return events

    # ── Inspection ────────────────────────────────────────────────────

    @property
    def current_trace(self) -> OrchestratorTrace | None:
        return self._trace

    def reset(self) -> None:
        """Reset the collector for a new trace. Useful for testing."""
        self._trace = None
        self._t0 = 0.0
        self._query = ""


# ── Module-level singleton ──────────────────────────────────────────────

agent_trace_collector = AgentTraceCollector()
