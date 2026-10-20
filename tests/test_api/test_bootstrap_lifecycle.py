"""Launch-mode catalogs are idempotent; background resources drain on shutdown."""

import asyncio

from app.background import start_background, stop_background
from app.bootstrap import initialize_catalogs


def test_catalog_registration_is_idempotent(monkeypatch, tmp_path):
    from app.agents import registry as agents
    from app.mcp import registry as mcp
    from app.nodes import registry as nodes
    from app.subagent import manager

    monkeypatch.setattr(nodes, "node_registry", nodes.NodeRegistry())
    monkeypatch.setattr(agents, "agent_registry", agents.AgentRegistry())
    monkeypatch.setattr(mcp.MCPServerRegistry, "_servers", {})
    monkeypatch.setattr(manager, "subagent_manager", manager.SubagentManager(str(tmp_path)))
    initialize_catalogs()
    original = nodes.node_registry.get("execute_sql")
    initialize_catalogs()
    assert nodes.node_registry.get("execute_sql") is original
    assert len(nodes.node_registry) == 12
    assert len(mcp.mcp_registry.list_names()) == 4
    assert len(agents.agent_registry.list_all()) == 6


async def test_background_stop_waits_for_cleanup():
    entered, cleaned = asyncio.Event(), asyncio.Event()

    async def waiting():
        try:
            entered.set()
            await asyncio.Event().wait()
        finally:
            await asyncio.sleep(0)
            cleaned.set()

    task = start_background(waiting())
    await entered.wait()
    await stop_background()
    assert task.cancelled()
    assert cleaned.is_set()
