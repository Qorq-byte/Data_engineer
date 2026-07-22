"""Agent communication bus — pub/sub message routing.

See SPEC §4.11.4 (Agent communication and Handoff mechanism) and
implementation plan §4.11.4 for the full specification.

The AgentBus is the central nervous system of the multi-agent architecture.
All inter-agent communication flows through it: handoffs, queries, results,
errors, and cancellations. Agents subscribe to their name and receive messages
asynchronously via registered callbacks.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from uuid import uuid4


class MessageType(StrEnum):
    """Types of messages that can flow through the AgentBus."""

    HANDOFF = "handoff"  # Inter-agent task transfer
    QUERY = "query"  # Query request
    RESULT = "result"  # Result return
    ERROR = "error"  # Error report
    CANCEL = "cancel"  # Cancel request


@dataclass
class AgentMessage:
    """A single message routed through the AgentBus.

    Mirrors SPEC §4.11.4 AgentMessage definition.
    """

    msg_id: str
    msg_type: MessageType
    from_agent: str
    to_agent: str
    payload: dict
    trace_id: str
    timestamp: str = field(default_factory=lambda: datetime.now(UTC).isoformat())


# Type alias for callback functions
AgentCallback = Callable[[AgentMessage], Awaitable[None]]


class AgentBus:
    """Pub/sub message bus for inter-agent communication.

    Agents subscribe to their own name to receive messages. The bus
    logs every message for later inspection (trace/debug).

    Usage::

        bus = AgentBus()

        async def handle(msg: AgentMessage) -> None:
            print(f"Received: {msg.msg_type} from {msg.from_agent}")

        bus.subscribe("my_agent", handle)
        await bus.send(AgentMessage(...))
    """

    def __init__(self) -> None:
        self._subscribers: dict[str, list[AgentCallback]] = {}
        self._message_log: list[AgentMessage] = []

    # ── Subscription management ───────────────────────────────────────

    def subscribe(self, agent_name: str, callback: AgentCallback) -> None:
        """Register a callback to receive messages addressed to *agent_name*."""
        self._subscribers.setdefault(agent_name, []).append(callback)

    def unsubscribe(self, agent_name: str, callback: AgentCallback) -> None:
        """Remove a previously registered callback."""
        subs = self._subscribers.get(agent_name, [])
        if callback in subs:
            subs.remove(callback)

    # ── Message routing ───────────────────────────────────────────────

    async def send(self, msg: AgentMessage) -> None:
        """Deliver a message to all subscribers of ``msg.to_agent``.

        All callbacks are invoked concurrently (fire-and-forget per callback).
        The message is also appended to the internal log.
        """
        self._message_log.append(msg)
        import contextlib
        for callback in self._subscribers.get(msg.to_agent, []):
            # Each callback runs independently; errors in one don't affect others
            with contextlib.suppress(Exception):
                await callback(msg)

    async def handoff(
        self,
        from_agent: str,
        to_agent: str,
        task: dict,
        trace_id: str,
    ) -> AgentMessage:
        """Convenience: create and send a HANDOFF message in one call.

        Args:
            from_agent: The agent initiating the handoff.
            to_agent: The target agent.
            task: The task payload (structured dict).
            trace_id: Global trace ID for correlating messages.

        Returns:
            The sent AgentMessage (with auto-generated msg_id).
        """
        msg = AgentMessage(
            msg_id=uuid4().hex,
            msg_type=MessageType.HANDOFF,
            from_agent=from_agent,
            to_agent=to_agent,
            payload=task,
            trace_id=trace_id,
        )
        await self.send(msg)
        return msg

    # ── History / inspection ──────────────────────────────────────────

    def get_history(self, trace_id: str | None = None) -> list[AgentMessage]:
        """Return the message log, optionally filtered by trace_id."""
        if trace_id is None:
            return list(self._message_log)
        return [m for m in self._message_log if m.trace_id == trace_id]

    def reset(self) -> None:
        """Clear all subscribers and message history. Useful for testing."""
        self._subscribers.clear()
        self._message_log.clear()


# ── Module-level singleton ──────────────────────────────────────────────

agent_bus = AgentBus()
