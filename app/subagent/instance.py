"""Subagent instance — scoped, domain-specific multi-agent system.

See SPEC §4.10.4 (Subagent runtime architecture) and §4.10.7 (relationship
with the multi-agent system).

A ``SubagentInstance`` wraps an :class:`OrchestratorAgent` with a filtered
set of agents, an isolated scoped context, and a lifecycle state machine:
CREATED → INITIALIZING → ACTIVE → INACTIVE.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from app.models.subagent import SubagentConfig


class SubagentStatus(StrEnum):
    """Lifecycle states (SPEC §4.10.3)."""

    CREATED = "created"
    INITIALIZING = "initializing"
    ACTIVE = "active"
    INACTIVE = "inactive"
    ERROR = "error"


@dataclass
class SubagentResult:
    """Result from a subagent query execution."""

    subagent_name: str
    status: str = "completed"  # completed | failed | rejected
    data: Any = None
    errors: list[str] = field(default_factory=list)
    latency_ms: float = 0.0
    trace_id: str = ""


class SubagentInstance:
    """A scoped, domain-specific multi-agent system instance.

    Each subagent has its own :class:`OrchestratorAgent` with a filtered
    set of child agents based on ``config.capabilities``, plus an isolated
    scoped context (domain, databases, llm_config).

    Usage::

        config = SubagentConfig(name="ecommerce", domain="ecommerce", ...)
        instance = SubagentInstance(config)
        await instance.initialize()
        result = await instance.query("上月订单总数", context={})
    """

    def __init__(self, config: SubagentConfig) -> None:
        self.config = config
        self.status = SubagentStatus.CREATED
        self._orchestrator: Any = None
        self._context: dict[str, Any] = {}

    # ── Lifecycle ───────────────────────────────────────────────────────

    async def initialize(self) -> None:
        """Create the internal orchestrator and register filtered agents.

        Called by :meth:`SubagentManager.activate`.
        """
        self.status = SubagentStatus.INITIALIZING
        try:
            from app.agents.bus import AgentBus
            from app.agents.orchestrator import OrchestratorAgent
            from app.agents.registry import AgentRegistry

            # Create isolated registry + bus per subagent
            registry = AgentRegistry()
            bus = AgentBus()

            self._orchestrator = OrchestratorAgent(
                registry=registry,
                bus=bus,
                default_mode="pipeline",
            )

            # Register filtered agents based on capabilities
            self._register_agents(registry)

            # Build scoped context
            llm_cfg = self.config.llm_config
            self._context = {
                "domain": self.config.domain,
                "databases": (
                    self.config.context.databases if self.config.context else []
                ),
                "llm_config": {
                    "model": llm_cfg.model if llm_cfg else "claude-sonnet-4",
                    "temperature": llm_cfg.temperature if llm_cfg else 0.3,
                },
            }

            self.status = SubagentStatus.ACTIVE
        except Exception:
            self.status = SubagentStatus.ERROR
            raise

    async def activate(self) -> None:
        """Alias for initialize — transition to ACTIVE."""
        await self.initialize()

    async def deactivate(self) -> None:
        """Transition to INACTIVE without destroying resources."""
        self.status = SubagentStatus.INACTIVE

    # ── Query interface ────────────────────────────────────────────────

    async def query(
        self,
        nl_text: str,
        context: dict[str, Any] | None = None,
    ) -> SubagentResult:
        """Execute a natural language query through the internal orchestrator.

        Args:
            nl_text: The user's natural language query.
            context: Optional additional context (merged with scoped context).

        Returns:
            SubagentResult with status, data, errors, and timing.
        """
        if self.status != SubagentStatus.ACTIVE:
            return SubagentResult(
                subagent_name=self.config.name,
                status="rejected",
                errors=[f"Subagent '{self.config.name}' is not active (status: {self.status})"],
            )

        if self._orchestrator is None:
            return SubagentResult(
                subagent_name=self.config.name,
                status="failed",
                errors=["Orchestrator not initialized"],
            )

        t0 = time.perf_counter()
        try:
            merged_ctx = {**self._context, **(context or {})}
            result = await self._orchestrator.run(nl_text, merged_ctx)

            elapsed = (time.perf_counter() - t0) * 1000
            return SubagentResult(
                subagent_name=self.config.name,
                status=result.status,
                data=result.final_output,
                errors=[e["errors"] for e in result.conflict_resolutions if "errors" in e],
                latency_ms=round(elapsed, 2),
                trace_id=result.trace_id,
            )
        except Exception as exc:
            elapsed = (time.perf_counter() - t0) * 1000
            return SubagentResult(
                subagent_name=self.config.name,
                status="failed",
                errors=[str(exc)],
                latency_ms=round(elapsed, 2),
            )

    # ── Private helpers ────────────────────────────────────────────────

    def _register_agents(self, registry: Any) -> None:
        """Register only the agents declared in ``config.capabilities``.

        Capability mapping:
          - ``nl_query`` → NLUnderstandingAgent, SchemaRetrievalAgent,
            SQLGenerationAgent, ValidationAgent, ToolExecutionAgent
          - ``metric_query`` → additionally MetricResolve (if available)
          - ``explain`` → ExplainPlan (reuses existing agents)
        """
        capabilities = set(self.config.capabilities)

        if "nl_query" in capabilities or not capabilities:
            from app.agents.feedback import FeedbackAgent
            from app.agents.nl_understander import NLUnderstandingAgent
            from app.agents.schema_retriever import SchemaRetrievalAgent
            from app.agents.sql_generator import SQLGenerationAgent
            from app.agents.sql_validator import ValidationAgent
            from app.agents.tool_executor import ToolExecutionAgent

            registry.register(NLUnderstandingAgent())
            registry.register(SchemaRetrievalAgent())
            registry.register(SQLGenerationAgent())
            registry.register(ValidationAgent())
            registry.register(ToolExecutionAgent())
            registry.register(FeedbackAgent())

        if "metric_query" in capabilities:
            # Metric resolution is handled by the existing agents + MetricFlow
            pass  # MetricResolveNode is registered in main.py already

    # ── Inspection ─────────────────────────────────────────────────────

    @property
    def agent_count(self) -> int:
        """Return the number of registered agents in the internal orchestrator."""
        if self._orchestrator is not None and hasattr(self._orchestrator, "_registry"):
            return len(self._orchestrator._registry.list_all())
        return 0

    def __repr__(self) -> str:
        return f"SubagentInstance(name={self.config.name!r}, status={self.status})"
