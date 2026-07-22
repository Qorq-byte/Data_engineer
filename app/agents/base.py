"""Base agent — abstract foundation for all agents in the multi-agent system.

See SPEC §4.11.2 for agent role definitions and the implementation plan §4.11 for
the full multi-agent architecture.

Each agent wraps existing core modules (NL parser, Schema retriever, SQL generator, etc.)
and adds tracing/logging. Agents are NOT nodes — they are a separate abstraction layer
that can be called from within AgenticNode subclasses.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class AgentStatus(StrEnum):
    """Lifecycle states for an agent (SPEC §4.11.3 state machine)."""

    IDLE = "idle"
    BUSY = "busy"
    DONE = "done"
    ERROR = "error"


@dataclass
class AgentResult:
    """Structured output from any agent's execute() call.

    Attributes:
        agent_name: The agent that produced this result.
        status: Final agent status (DONE or ERROR).
        data: The structured output — an SQR, list[SQLCandidate], ValidationReport, etc.
        errors: Error messages collected during execution.
        metadata: Timing, token usage, model info, and tools_called.
    """

    agent_name: str
    status: AgentStatus = AgentStatus.DONE
    data: Any = None
    errors: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


class BaseAgent(ABC):
    """Abstract base for all agents in the multi-agent system.

    Subclasses must:
      - Set ``name`` and ``description`` class-level attributes.
      - Implement ``execute(input, context)`` returning an ``AgentResult``.

    The lifecycle is: IDLE → BUSY (during execute) → DONE | ERROR.

    Usage::

        agent = NLUnderstandingAgent(parser=nlp_parser)
        result = await agent.execute("上个月订单总数", context={})
        sqr: SQR = result.data
    """

    name: str = "base_agent"
    description: str = "Base agent — override in subclasses"

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        self.config = config or {}
        self._status = AgentStatus.IDLE

    # ── Core interface ──────────────────────────────────────────────

    @abstractmethod
    async def execute(self, input: Any, context: dict[str, Any]) -> AgentResult:
        """Execute the agent's core logic.

        Args:
            input: The primary input — varies by agent type (str, SQR, SQLCandidate, dict).
            context: Shared context dict (schema, domain, db_id, session_id, etc.).

        Returns:
            AgentResult with structured data, errors, and metadata.
        """
        ...

    # ── Lifecycle helpers ────────────────────────────────────────────

    @property
    def status(self) -> AgentStatus:
        return self._status

    def reset(self) -> None:
        """Reset the agent back to IDLE state."""
        self._status = AgentStatus.IDLE

    # ── Utility for subclasses ───────────────────────────────────────

    def _ok(self, data: Any, **metadata: Any) -> AgentResult:
        """Build a successful AgentResult."""
        metadata.setdefault("latency_ms", 0)
        return AgentResult(
            agent_name=self.name,
            status=AgentStatus.DONE,
            data=data,
            metadata=metadata,
        )

    def _error(self, message: str, *extra: str, **metadata: Any) -> AgentResult:
        """Build a failed AgentResult."""
        return AgentResult(
            agent_name=self.name,
            status=AgentStatus.ERROR,
            errors=[message, *extra],
            metadata=metadata,
        )
