"""Tests for SubagentRouter — API/Web/MCP 3-channel dispatch."""

from __future__ import annotations

import pytest

from app.models.subagent import (
    AccessControl,
    DeliveryConfig,
    LLMConfig,
    SubagentConfig,
    SubagentContext,
)
from app.subagent.manager import SubagentManager
from app.subagent.router import SubagentRouter, subagent_router


def _make_config(**overrides) -> SubagentConfig:
    defaults = {
        "name": "test_sub",
        "domain": "ecommerce",
        "description": "Test subagent",
        "context": SubagentContext(domain="ecommerce"),
        "capabilities": ["nl_query"],
        "delivery": [
            DeliveryConfig(type="web", path="/subagents/test_sub"),
            DeliveryConfig(type="api", endpoint="/api/v1/subagents/test_sub"),
            DeliveryConfig(type="mcp", tool_prefix="test_"),
        ],
        "llm_config": LLMConfig(),
        "access_control": AccessControl(allowed_users=["*"]),
    }
    defaults.update(overrides)
    return SubagentConfig(**defaults)


def _setup_router() -> tuple[SubagentRouter, SubagentManager]:
    mgr = SubagentManager()
    router = SubagentRouter(manager=mgr)
    mgr.register(_make_config())
    return router, mgr


# ═══════════════════════════════════════════════════════════════════════════════
# API Channel
# ═══════════════════════════════════════════════════════════════════════════════


class TestAPIRouting:
    @pytest.fixture
    def router(self):
        r, mgr = _setup_router()
        yield r
        mgr.reset()

    @pytest.mark.anyio
    async def test_route_api_unregistered_raises(self, router):
        with pytest.raises(KeyError, match="not found"):
            await router.route_api("nonexistent", "query", {})

    @pytest.mark.anyio
    async def test_route_api_not_active_rejected(self, router):
        result = await router.route_api("test_sub", "query", {})
        assert result["status"] == "rejected"
        assert "not active" in result["errors"][0]

    @pytest.mark.anyio
    async def test_route_api_active_returns_result(self, router):
        router._manager.register(_make_config())  # overwrite
        inst = await router._manager.activate("test_sub")
        assert inst.status.value == "active"
        result = await router.route_api("test_sub", "查询用户", {})
        assert result["status"] in ("completed", "partial")
        assert "trace_id" in result

    def test_list_api_endpoints(self, router):
        eps = router.list_api_endpoints()
        assert len(eps) >= 1
        assert any(e["name"] == "test_sub" for e in eps)


# ═══════════════════════════════════════════════════════════════════════════════
# Web Channel
# ═══════════════════════════════════════════════════════════════════════════════


class TestWebRouting:
    @pytest.fixture
    def router(self):
        r, mgr = _setup_router()
        yield r
        mgr.reset()

    def test_get_web_path_exists(self, router):
        path = router.get_web_path("test_sub")
        assert path == "/subagents/test_sub"

    def test_get_web_path_nonexistent(self, router):
        assert router.get_web_path("nonexistent") is None

    def test_list_web_subagents(self, router):
        web_list = router.list_web_subagents()
        assert len(web_list) >= 1
        assert web_list[0]["name"] == "test_sub"
        assert web_list[0]["path"] == "/subagents/test_sub"

    def test_list_web_subagents_no_web_delivery(self):
        mgr = SubagentManager()
        router = SubagentRouter(manager=mgr)
        cfg = _make_config(delivery=[DeliveryConfig(type="api", endpoint="/x")])
        mgr.register(cfg)
        assert router.list_web_subagents() == []
        mgr.reset()


# ═══════════════════════════════════════════════════════════════════════════════
# MCP Channel
# ═══════════════════════════════════════════════════════════════════════════════


class TestMCPRouting:
    @pytest.fixture
    def router(self):
        r, mgr = _setup_router()
        yield r
        mgr.reset()

    def test_get_mcp_tool_prefix_exists(self, router):
        prefix = router.get_mcp_tool_prefix("test_sub")
        assert prefix == "test_"

    def test_get_mcp_tool_prefix_nonexistent(self, router):
        assert router.get_mcp_tool_prefix("nonexistent") is None

    def test_list_mcp_subagents(self, router):
        mcp_list = router.list_mcp_subagents()
        assert len(mcp_list) >= 1
        assert mcp_list[0]["name"] == "test_sub"
        assert mcp_list[0]["tool_prefix"] == "test_"

    def test_list_mcp_subagents_no_mcp_delivery(self):
        mgr = SubagentManager()
        router = SubagentRouter(manager=mgr)
        cfg = _make_config(delivery=[DeliveryConfig(type="api", endpoint="/x")])
        mgr.register(cfg)
        assert router.list_mcp_subagents() == []
        mgr.reset()


# ═══════════════════════════════════════════════════════════════════════════════
# Access control
# ═══════════════════════════════════════════════════════════════════════════════


class TestAccessControl:
    @pytest.fixture
    def router(self):
        r, mgr = _setup_router()
        yield r
        mgr.reset()

    def test_wildcard_allows_all(self, router):
        assert router.check_access("test_sub") is True
        assert router.check_access("test_sub", user="anyone") is True

    def test_nonexistent_denied(self, router):
        assert router.check_access("nonexistent") is False

    def test_specific_user_allowed(self):
        mgr = SubagentManager()
        router = SubagentRouter(manager=mgr)
        cfg = _make_config(
            access_control=AccessControl(allowed_users=["alice", "bob"]),
        )
        mgr.register(cfg)
        assert router.check_access("test_sub", user="alice") is True
        assert router.check_access("test_sub", user="charlie") is False
        mgr.reset()

    def test_rate_limit_default(self, router):
        assert router.check_rate_limit("test_sub") is True

    def test_rate_limit_exceeded(self, router):
        # rate_limit=100 in default config, current_count=101 exceeds
        assert router.check_rate_limit("test_sub", 101) is False

    def test_rate_limit_nonexistent(self, router):
        assert router.check_rate_limit("nonexistent") is False


# ═══════════════════════════════════════════════════════════════════════════════
# Module-level singleton
# ═══════════════════════════════════════════════════════════════════════════════


class TestSubagentRouterSingleton:
    def test_singleton_exists(self):
        assert isinstance(subagent_router, SubagentRouter)

    def test_singleton_shared(self):
        from app.subagent.router import subagent_router as sr2
        assert subagent_router is sr2
