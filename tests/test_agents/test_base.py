"""Tests for agent base classes — AgentStatus, AgentResult, BaseAgent."""

from __future__ import annotations

from app.agents.base import AgentResult, AgentStatus, BaseAgent

# ── Test agent (concrete implementation for testing) ──────────────────


class _TestAgent(BaseAgent):
    """Minimal concrete agent for testing BaseAgent lifecycle."""

    name = "test_agent"
    description = "Test agent for unit tests"

    async def execute(self, input, context):
        if isinstance(input, str) and input == "fail":
            return self._error("deliberate failure")
        if isinstance(input, str) and input == "raise":
            raise RuntimeError("unhandled error")
        return self._ok(input, latency_ms=10.5)


# ═══════════════════════════════════════════════════════════════════════════════
# AgentStatus
# ═══════════════════════════════════════════════════════════════════════════════


class TestAgentStatus:
    def test_all_statuses_exist(self):
        assert AgentStatus.IDLE == "idle"
        assert AgentStatus.BUSY == "busy"
        assert AgentStatus.DONE == "done"
        assert AgentStatus.ERROR == "error"

    def test_is_str_enum(self):
        assert isinstance(AgentStatus.IDLE, str)
        assert AgentStatus.IDLE == "idle"

    def test_status_iteration(self):
        values = list(AgentStatus)
        assert len(values) == 4


# ═══════════════════════════════════════════════════════════════════════════════
# AgentResult
# ═══════════════════════════════════════════════════════════════════════════════


class TestAgentResult:
    def test_defaults(self):
        r = AgentResult(agent_name="test")
        assert r.agent_name == "test"
        assert r.status == AgentStatus.DONE
        assert r.data is None
        assert r.errors == []
        assert r.metadata == {}

    def test_with_data(self):
        r = AgentResult(agent_name="nl", data={"sqr": "..."})
        assert r.data == {"sqr": "..."}

    def test_with_errors(self):
        r = AgentResult(
            agent_name="bad",
            status=AgentStatus.ERROR,
            errors=["oops", "double oops"],
        )
        assert len(r.errors) == 2
        assert r.errors[0] == "oops"

    def test_with_metadata(self):
        r = AgentResult(
            agent_name="gen",
            metadata={"latency_ms": 123.4, "model": "sonnet"},
        )
        assert r.metadata["latency_ms"] == 123.4


# ═══════════════════════════════════════════════════════════════════════════════
# BaseAgent lifecycle
# ═══════════════════════════════════════════════════════════════════════════════


class TestBaseAgent:
    def test_default_status_is_idle(self):
        agent = _TestAgent()
        assert agent.status == AgentStatus.IDLE

    def test_execute_success(self):
        agent = _TestAgent()
        result = agent.execute_sync("hello")
        assert result.status == AgentStatus.DONE
        assert result.data == "hello"
        assert result.metadata["latency_ms"] == 10.5

    def test_execute_error(self):
        agent = _TestAgent()
        result = agent.execute_sync("fail")
        assert result.status == AgentStatus.ERROR
        assert "deliberate failure" in result.errors[0]

    def test_reset(self):
        agent = _TestAgent()
        agent.execute_sync("hello")
        agent.reset()
        assert agent.status == AgentStatus.IDLE

    def test_config_defaults_to_empty_dict(self):
        agent = _TestAgent()
        assert agent.config == {}

    def test_config_with_values(self):
        agent = _TestAgent(config={"key": "val"})
        assert agent.config["key"] == "val"

    def test_name_class_attribute(self):
        agent = _TestAgent()
        assert agent.name == "test_agent"

    def test_description_class_attribute(self):
        agent = _TestAgent()
        assert "Test agent" in agent.description

    def test_ok_helper_with_custom_metadata(self):
        agent = _TestAgent()
        result = agent._ok("data", extra_info=42)
        assert result.status == AgentStatus.DONE
        assert result.data == "data"
        assert result.metadata["extra_info"] == 42

    def test_error_helper_with_multiple_messages(self):
        agent = _TestAgent()
        result = agent._error("first", "second", "third")
        assert result.status == AgentStatus.ERROR
        assert result.errors == ["first", "second", "third"]

    def test_execute_sync_helper(self):
        """Verify the synchronous test helper works."""
        agent = _TestAgent()

        async def run():
            return await agent.execute("test_input", {})

        import asyncio
        result = asyncio.run(run())
        assert result.status == AgentStatus.DONE
        assert result.data == "test_input"


# ── Helpers ──────────────────────────────────────────────────────────────


def _extend_test_agent() -> None:
    """Extend _TestAgent with a sync execute method for test convenience."""
    import asyncio

    def execute_sync(self, input, context=None):
        if context is None:
            context = {}
        return asyncio.run(self.execute(input, context))

    _TestAgent.execute_sync = execute_sync


_extend_test_agent()
