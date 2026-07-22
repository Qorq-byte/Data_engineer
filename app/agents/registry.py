"""Agent registry — global singleton for agent instances.

Mirrors the pattern established by ``app.nodes.registry.NodeRegistry``.
Agents are registered at startup in ``main.py`` and looked up by name
during orchestration.
"""

from __future__ import annotations

from app.agents.base import BaseAgent


class AgentRegistry:
    """Global registry of agent instances, keyed by agent name.

    Usage::

        registry = AgentRegistry()
        registry.register(NLUnderstandingAgent(parser=...))
        agent = registry.get("nl_understander")
    """

    def __init__(self) -> None:
        self._agents: dict[str, BaseAgent] = {}

    def register(self, agent: BaseAgent) -> None:
        """Register an agent instance. Overwrites any existing entry with the same name."""
        self._agents[agent.name] = agent

    def get(self, name: str) -> BaseAgent | None:
        """Look up an agent by name. Returns None if not found."""
        return self._agents.get(name)

    def list_all(self) -> list[str]:
        """Return all registered agent names."""
        return list(self._agents.keys())

    def reset(self) -> None:
        """Clear all registered agents. Useful for testing."""
        self._agents.clear()


# ── Module-level singleton ──────────────────────────────────────────────

agent_registry = AgentRegistry()
