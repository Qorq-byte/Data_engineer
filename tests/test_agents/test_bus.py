"""Tests for AgentBus — pub/sub messaging, handoff, history."""

from __future__ import annotations

import pytest

from app.agents.bus import AgentBus, AgentMessage, MessageType, agent_bus

# ═══════════════════════════════════════════════════════════════════════════════
# AgentMessage
# ═══════════════════════════════════════════════════════════════════════════════


class TestAgentMessage:
    def test_defaults(self):
        msg = AgentMessage(
            msg_id="abc",
            msg_type=MessageType.QUERY,
            from_agent="a",
            to_agent="b",
            payload={},
            trace_id="t1",
        )
        assert msg.msg_id == "abc"
        assert msg.msg_type == MessageType.QUERY
        assert msg.from_agent == "a"
        assert msg.to_agent == "b"
        assert msg.trace_id == "t1"

    def test_timestamp_auto_generated(self):
        msg = AgentMessage(
            msg_id="x",
            msg_type=MessageType.RESULT,
            from_agent="a",
            to_agent="b",
            payload={},
            trace_id="t",
        )
        assert msg.timestamp  # non-empty ISO string

    def test_handoff_type(self):
        msg = AgentMessage(
            msg_id="h1",
            msg_type=MessageType.HANDOFF,
            from_agent="orch",
            to_agent="sql",
            payload={"task": "generate"},
            trace_id="t2",
        )
        assert msg.msg_type == MessageType.HANDOFF

    def test_error_type(self):
        msg = AgentMessage(
            msg_id="e1",
            msg_type=MessageType.ERROR,
            from_agent="validator",
            to_agent="sql_generator",
            payload={"error": "syntax error"},
            trace_id="t3",
        )
        assert msg.msg_type == MessageType.ERROR

    def test_cancel_type(self):
        msg = AgentMessage(
            msg_id="c1",
            msg_type=MessageType.CANCEL,
            from_agent="orchestrator",
            to_agent="tool_executor",
            payload={},
            trace_id="t4",
        )
        assert msg.msg_type == MessageType.CANCEL


# ═══════════════════════════════════════════════════════════════════════════════
# MessageType
# ═══════════════════════════════════════════════════════════════════════════════


class TestMessageType:
    def test_all_types(self):
        types = list(MessageType)
        assert len(types) == 5
        assert MessageType.HANDOFF in types
        assert MessageType.QUERY in types
        assert MessageType.RESULT in types
        assert MessageType.ERROR in types
        assert MessageType.CANCEL in types


# ═══════════════════════════════════════════════════════════════════════════════
# AgentBus — subscribe / send
# ═══════════════════════════════════════════════════════════════════════════════


class TestAgentBusSubscribeSend:
    @pytest.fixture
    def bus(self):
        b = AgentBus()
        yield b
        b.reset()

    @pytest.mark.anyio
    async def test_subscribe_and_receive(self, bus):
        received: list[AgentMessage] = []

        async def handler(msg: AgentMessage) -> None:
            received.append(msg)

        bus.subscribe("agent_a", handler)
        msg = AgentMessage(
            msg_id="m1",
            msg_type=MessageType.QUERY,
            from_agent="orch",
            to_agent="agent_a",
            payload={"q": "test"},
            trace_id="t1",
        )
        await bus.send(msg)
        assert len(received) == 1
        assert received[0].msg_id == "m1"

    @pytest.mark.anyio
    async def test_only_target_receives(self, bus):
        received_a: list[AgentMessage] = []
        received_b: list[AgentMessage] = []

        async def handler_a(msg): received_a.append(msg)
        async def handler_b(msg): received_b.append(msg)

        bus.subscribe("a", handler_a)
        bus.subscribe("b", handler_b)

        msg = AgentMessage(
            msg_id="m2", msg_type=MessageType.QUERY,
            from_agent="x", to_agent="a", payload={}, trace_id="t",
        )
        await bus.send(msg)
        assert len(received_a) == 1
        assert len(received_b) == 0

    @pytest.mark.anyio
    async def test_multiple_subscribers_same_agent(self, bus):
        r1: list = []
        r2: list = []

        async def h1(msg): r1.append(msg)
        async def h2(msg): r2.append(msg)

        bus.subscribe("agent", h1)
        bus.subscribe("agent", h2)
        msg = AgentMessage(
            msg_id="m3", msg_type=MessageType.RESULT,
            from_agent="a", to_agent="agent", payload={}, trace_id="t",
        )
        await bus.send(msg)
        assert len(r1) == 1
        assert len(r2) == 1

    @pytest.mark.anyio
    async def test_unsubscribe(self, bus):
        received: list[AgentMessage] = []

        async def handler(msg): received.append(msg)

        bus.subscribe("agent", handler)
        bus.unsubscribe("agent", handler)

        msg = AgentMessage(
            msg_id="m4", msg_type=MessageType.QUERY,
            from_agent="x", to_agent="agent", payload={}, trace_id="t",
        )
        await bus.send(msg)
        assert len(received) == 0

    @pytest.mark.anyio
    async def test_no_subscriber_no_error(self, bus):
        msg = AgentMessage(
            msg_id="m5", msg_type=MessageType.QUERY,
            from_agent="x", to_agent="nobody", payload={}, trace_id="t",
        )
        await bus.send(msg)  # Should not raise

    @pytest.mark.anyio
    async def test_misbehaving_subscriber_does_not_crash_bus(self, bus):
        async def bad_handler(msg):
            raise RuntimeError("boom")

        async def good_handler(msg):
            pass  # no-op

        bus.subscribe("agent", bad_handler)
        bus.subscribe("agent", good_handler)

        msg = AgentMessage(
            msg_id="m6", msg_type=MessageType.QUERY,
            from_agent="x", to_agent="agent", payload={}, trace_id="t",
        )
        await bus.send(msg)  # Should not raise despite bad_handler failure


# ═══════════════════════════════════════════════════════════════════════════════
# AgentBus — handoff
# ═══════════════════════════════════════════════════════════════════════════════


class TestAgentBusHandoff:
    @pytest.fixture
    def bus(self):
        b = AgentBus()
        yield b
        b.reset()

    @pytest.mark.anyio
    async def test_handoff_creates_message(self, bus):
        msg = await bus.handoff("orch", "nl", {"task": "parse"}, "trace_1")
        assert msg.msg_type == MessageType.HANDOFF
        assert msg.from_agent == "orch"
        assert msg.to_agent == "nl"
        assert msg.payload == {"task": "parse"}
        assert msg.trace_id == "trace_1"

    @pytest.mark.anyio
    async def test_handoff_delivers_to_subscriber(self, bus):
        received: list[AgentMessage] = []

        async def handler(msg): received.append(msg)

        bus.subscribe("nl", handler)
        await bus.handoff("orch", "nl", {"task": "parse"}, "trace_2")
        assert len(received) == 1
        assert received[0].payload == {"task": "parse"}

    @pytest.mark.anyio
    async def test_handoff_generates_unique_ids(self, bus):
        msg1 = await bus.handoff("a", "b", {}, "t")
        msg2 = await bus.handoff("a", "b", {}, "t")
        assert msg1.msg_id != msg2.msg_id


# ═══════════════════════════════════════════════════════════════════════════════
# AgentBus — history
# ═══════════════════════════════════════════════════════════════════════════════


class TestAgentBusHistory:
    @pytest.fixture
    def bus(self):
        b = AgentBus()
        yield b
        b.reset()

    @pytest.mark.anyio
    async def test_get_history_all(self, bus):
        msg = AgentMessage(
            msg_id="h1", msg_type=MessageType.QUERY,
            from_agent="a", to_agent="b", payload={}, trace_id="ta",
        )
        await bus.send(msg)
        history = bus.get_history()
        assert len(history) == 1

    @pytest.mark.anyio
    async def test_get_history_filtered_by_trace(self, bus):
        await bus.send(AgentMessage(
            msg_id="h2", msg_type=MessageType.QUERY,
            from_agent="a", to_agent="b", payload={}, trace_id="ta",
        ))
        await bus.send(AgentMessage(
            msg_id="h3", msg_type=MessageType.QUERY,
            from_agent="c", to_agent="d", payload={}, trace_id="tb",
        ))
        assert len(bus.get_history("ta")) == 1
        assert len(bus.get_history("tb")) == 1
        assert len(bus.get_history("tc")) == 0

    @pytest.mark.anyio
    async def test_reset_clears_history(self, bus):
        await bus.send(AgentMessage(
            msg_id="h4", msg_type=MessageType.QUERY,
            from_agent="a", to_agent="b", payload={}, trace_id="t",
        ))
        bus.reset()
        assert len(bus.get_history()) == 0


# ═══════════════════════════════════════════════════════════════════════════════
# Module-level singleton
# ═══════════════════════════════════════════════════════════════════════════════


class TestAgentBusSingleton:
    def test_singleton_exists(self):
        assert isinstance(agent_bus, AgentBus)

    def test_singleton_is_shared(self):
        from app.agents.bus import agent_bus as bus2
        assert agent_bus is bus2
