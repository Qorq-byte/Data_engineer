"""Tests for SubagentInstance — lifecycle, query delegation, scoped context."""

from __future__ import annotations

import pytest

from app.models.subagent import (
    AccessControl,
    DeliveryConfig,
    LLMConfig,
    SubagentConfig,
    SubagentContext,
)
from app.subagent.instance import SubagentInstance, SubagentResult, SubagentStatus


def _make_config(**overrides) -> SubagentConfig:
    """Build a minimal SubagentConfig for testing."""
    defaults = {
        "name": "test_sub",
        "domain": "ecommerce",
        "description": "Test subagent",
        "context": SubagentContext(domain="ecommerce"),
        "capabilities": ["nl_query"],
        "delivery": [DeliveryConfig(type="api", endpoint="/api/v1/subagents/test_sub")],
        "llm_config": LLMConfig(model="claude-haiku-4.5"),
        "access_control": AccessControl(allowed_users=["*"]),
    }
    defaults.update(overrides)
    return SubagentConfig(**defaults)


# ═══════════════════════════════════════════════════════════════════════════════
# SubagentInstance lifecycle
# ═══════════════════════════════════════════════════════════════════════════════


class TestSubagentInstanceLifecycle:
    def test_created_default_status(self):
        inst = SubagentInstance(config=_make_config())
        assert inst.status == SubagentStatus.CREATED

    @pytest.mark.anyio
    async def test_initialize_transitions_to_active(self):
        inst = SubagentInstance(config=_make_config())
        await inst.initialize()
        assert inst.status == SubagentStatus.ACTIVE

    @pytest.mark.anyio
    async def test_activate_alias(self):
        inst = SubagentInstance(config=_make_config())
        await inst.activate()
        assert inst.status == SubagentStatus.ACTIVE

    @pytest.mark.anyio
    async def test_deactivate_transitions_to_inactive(self):
        inst = SubagentInstance(config=_make_config())
        await inst.initialize()
        await inst.deactivate()
        assert inst.status == SubagentStatus.INACTIVE

    @pytest.mark.anyio
    async def test_double_initialize_is_idempotent(self):
        inst = SubagentInstance(config=_make_config())
        await inst.initialize()
        await inst.initialize()  # Should be safe
        assert inst.status == SubagentStatus.ACTIVE

    def test_repr(self):
        inst = SubagentInstance(config=_make_config())
        assert "test_sub" in repr(inst)
        assert "created" in repr(inst)

    @pytest.mark.anyio
    async def test_config_preserved_after_init(self):
        cfg = _make_config()
        inst = SubagentInstance(config=cfg)
        await inst.initialize()
        assert inst.config.name == "test_sub"

    @pytest.mark.anyio
    async def test_agent_count_after_init(self):
        inst = SubagentInstance(config=_make_config(capabilities=["nl_query"]))
        await inst.initialize()
        assert inst.agent_count >= 5  # 5 agents for nl_query

    @pytest.mark.anyio
    async def test_agent_count_no_capabilities(self):
        inst = SubagentInstance(config=_make_config(capabilities=[]))
        await inst.initialize()
        assert inst.agent_count >= 5  # default falls back to full set


# ═══════════════════════════════════════════════════════════════════════════════
# SubagentInstance query
# ═══════════════════════════════════════════════════════════════════════════════


class TestSubagentInstanceQuery:
    @pytest.mark.anyio
    async def test_query_when_not_active_rejected(self):
        inst = SubagentInstance(config=_make_config())
        # Not initialized
        result = await inst.query("test", {})
        assert result.status == "rejected"
        assert "not active" in result.errors[0]

    @pytest.mark.anyio
    async def test_query_when_active(self):
        inst = SubagentInstance(config=_make_config())
        await inst.initialize()
        result = await inst.query("查询用户", {})
        assert result.status in ("completed", "partial")
        assert result.subagent_name == "test_sub"

    @pytest.mark.anyio
    async def test_query_returns_trace_id(self):
        inst = SubagentInstance(config=_make_config())
        await inst.initialize()
        result = await inst.query("test query", {})
        assert result.trace_id

    @pytest.mark.anyio
    async def test_query_returns_latency(self):
        inst = SubagentInstance(config=_make_config())
        await inst.initialize()
        result = await inst.query("test", {})
        assert result.latency_ms >= 0

    @pytest.mark.anyio
    async def test_query_after_deactivate_rejected(self):
        inst = SubagentInstance(config=_make_config())
        await inst.initialize()
        await inst.deactivate()
        result = await inst.query("test", {})
        assert result.status == "rejected"


# ═══════════════════════════════════════════════════════════════════════════════
# SubagentResult
# ═══════════════════════════════════════════════════════════════════════════════


class TestSubagentResult:
    def test_defaults(self):
        r = SubagentResult(subagent_name="s")
        assert r.subagent_name == "s"
        assert r.status == "completed"
        assert r.data is None
        assert r.errors == []

    def test_with_data(self):
        r = SubagentResult(subagent_name="s", data={"sql": "SELECT 1"})
        assert r.data == {"sql": "SELECT 1"}

    def test_with_errors(self):
        r = SubagentResult(subagent_name="s", status="failed", errors=["boom"])
        assert r.status == "failed"
        assert len(r.errors) == 1
