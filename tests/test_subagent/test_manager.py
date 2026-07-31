"""Tests for SubagentManager — registration, lifecycle, YAML loading."""

from __future__ import annotations

import pytest

from app.models.subagent import (
    AccessControl,
    DeliveryConfig,
    LLMConfig,
    SubagentConfig,
    SubagentContext,
)
from app.subagent.instance import SubagentStatus
from app.subagent.manager import SubagentManager, subagent_manager


def _make_config(**overrides) -> SubagentConfig:
    defaults = {
        "name": "test_sub",
        "domain": "ecommerce",
        "description": "Test",
        "context": SubagentContext(domain="ecommerce"),
        "capabilities": ["nl_query"],
        "delivery": [DeliveryConfig(type="api", endpoint="/test")],
        "llm_config": LLMConfig(),
        "access_control": AccessControl(allowed_users=["*"]),
    }
    defaults.update(overrides)
    return SubagentConfig(**defaults)


# ═══════════════════════════════════════════════════════════════════════════════
# SubagentManager — registration
# ═══════════════════════════════════════════════════════════════════════════════


class TestSubagentManagerRegistration:
    @pytest.fixture
    def mgr(self):
        m = SubagentManager()
        yield m
        m.reset()

    def test_register_adds_instance(self, mgr):
        mgr.register(_make_config())
        assert "test_sub" in mgr.list_all()

    def test_register_overwrites_same_name(self, mgr):
        cfg1 = _make_config(description="First")
        cfg2 = _make_config(description="Second")
        mgr.register(cfg1)
        mgr.register(cfg2)
        inst = mgr.get("test_sub")
        assert inst is not None
        assert inst.config.description == "Second"

    def test_unregister_removes(self, mgr):
        mgr.register(_make_config())
        mgr.unregister("test_sub")
        assert "test_sub" not in mgr.list_all()

    def test_unregister_nonexistent_no_error(self, mgr):
        mgr.unregister("nonexistent")

    def test_get_nonexistent_returns_none(self, mgr):
        assert mgr.get("nonexistent") is None

    def test_list_all_empty_initially(self, mgr):
        assert mgr.list_all() == []

    def test_list_active_empty_when_none_activated(self, mgr):
        mgr.register(_make_config())
        assert mgr.list_active() == []


# ═══════════════════════════════════════════════════════════════════════════════
# SubagentManager — lifecycle
# ═══════════════════════════════════════════════════════════════════════════════


class TestSubagentManagerLifecycle:
    @pytest.fixture
    def mgr(self):
        m = SubagentManager()
        yield m
        m.reset()

    @pytest.mark.anyio
    async def test_activate_transitions_to_active(self, mgr):
        mgr.register(_make_config())
        inst = await mgr.activate("test_sub")
        assert inst.status == SubagentStatus.ACTIVE
        assert "test_sub" in mgr.list_active()

    @pytest.mark.anyio
    async def test_activate_unregistered_raises(self, mgr):
        with pytest.raises(KeyError, match="not registered"):
            await mgr.activate("nonexistent")

    @pytest.mark.anyio
    async def test_deactivate(self, mgr):
        mgr.register(_make_config())
        await mgr.activate("test_sub")
        await mgr.deactivate("test_sub")
        assert "test_sub" not in mgr.list_active()

    @pytest.mark.anyio
    async def test_deactivate_nonexistent_no_error(self, mgr):
        await mgr.deactivate("nonexistent")

    @pytest.mark.anyio
    async def test_deactivate_all(self, mgr):
        mgr.register(_make_config(name="a"))
        mgr.register(_make_config(name="b"))
        await mgr.activate("a")
        await mgr.activate("b")
        await mgr.deactivate_all()
        assert mgr.list_active() == []


# ═══════════════════════════════════════════════════════════════════════════════
# SubagentManager — YAML config loading
# ═══════════════════════════════════════════════════════════════════════════════


class TestSubagentManagerConfigLoading:
    @pytest.fixture
    def mgr(self):
        m = SubagentManager(config_dir="app/config/subagents")
        yield m
        m.reset()

    def test_load_configs_finds_yaml(self, mgr):
        configs = mgr.load_configs()
        assert len(configs) >= 1  # ecommerce_analyst.yaml exists
        names = [c.name for c in configs]
        assert "ecommerce_analyst" in names

    def test_loaded_config_has_domain(self, mgr):
        configs = mgr.load_configs()
        ecom = [c for c in configs if c.name == "ecommerce_analyst"][0]
        assert ecom.domain == "ecommerce"

    def test_loaded_config_has_capabilities(self, mgr):
        configs = mgr.load_configs()
        ecom = [c for c in configs if c.name == "ecommerce_analyst"][0]
        assert "nl_query" in ecom.capabilities

    def test_loaded_config_has_delivery(self, mgr):
        configs = mgr.load_configs()
        ecom = [c for c in configs if c.name == "ecommerce_analyst"][0]
        delivery_types = [d.type for d in ecom.delivery]
        assert "web" in delivery_types
        assert "api" in delivery_types
        assert "mcp" in delivery_types

    def test_loaded_config_has_llm_config(self, mgr):
        configs = mgr.load_configs()
        ecom = [c for c in configs if c.name == "ecommerce_analyst"][0]
        assert ecom.llm_config.model == "claude-sonnet-4"

    def test_load_and_register(self, mgr):
        names = mgr.load_and_register()
        assert "ecommerce_analyst" in names

    def test_nonexistent_dir_returns_empty(self):
        mgr = SubagentManager(config_dir="nonexistent/path")
        configs = mgr.load_configs()
        assert configs == []


# ═══════════════════════════════════════════════════════════════════════════════
# SubagentManager — listing
# ═══════════════════════════════════════════════════════════════════════════════


class TestSubagentManagerListing:
    @pytest.fixture
    def mgr(self):
        m = SubagentManager()
        yield m
        m.reset()

    def test_list_details(self, mgr):
        mgr.register(_make_config())
        details = mgr.list_details()
        assert len(details) == 1
        assert details[0]["name"] == "test_sub"
        assert details[0]["domain"] == "ecommerce"
        assert details[0]["status"] == "created"

    @pytest.mark.anyio
    async def test_list_details_reflects_status(self, mgr):
        mgr.register(_make_config())
        await mgr.activate("test_sub")
        details = mgr.list_details()
        assert details[0]["status"] == "active"


# ═══════════════════════════════════════════════════════════════════════════════
# Module-level singleton
# ═══════════════════════════════════════════════════════════════════════════════


class TestSubagentManagerSingleton:
    def test_singleton_exists(self):
        assert isinstance(subagent_manager, SubagentManager)

    def test_singleton_shared(self):
        from app.subagent.manager import subagent_manager as mgr2
        assert subagent_manager is mgr2
