"""Subagent Manager — lifecycle management and YAML config loading.

See SPEC §4.10.3 (lifecycle) and implementation plan §4.12.

The manager is the central registry for all subagent instances. It loads
YAML definitions from ``app/config/subagents/``, creates ``SubagentInstance``
objects, and manages their lifecycle (create → initialize → activate → deactivate).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from app.models.subagent import (
    AccessControl,
    DeliveryConfig,
    LLMConfig,
    SubagentConfig,
    SubagentContext,
)
from app.subagent.instance import SubagentInstance, SubagentStatus


class SubagentManager:
    """Manages the lifecycle of all subagent instances.

    Usage::

        manager = SubagentManager(config_dir="app/config/subagents")
        configs = manager.load_configs()
        for cfg in configs:
            manager.register(cfg)
        await manager.activate("ecommerce_analyst")
        result = await manager.get("ecommerce_analyst").query("orders last month")
    """

    def __init__(self, config_dir: str = "app/config/subagents") -> None:
        self._instances: dict[str, SubagentInstance] = {}
        self._usage_stats: dict[str, Any] = {}
        self._config_dir = Path(config_dir)

    # ── Registration ───────────────────────────────────────────────────

    def register(self, config: SubagentConfig) -> SubagentInstance:
        """Create and register a subagent instance from its config.

        Overwrites any existing instance with the same name.
        """
        instance = SubagentInstance(config)
        self._instances[config.name] = instance
        return instance

    def unregister(self, name: str) -> None:
        """Remove a subagent instance (deactivates first if active)."""
        inst = self._instances.pop(name, None)
        if inst is not None and inst.status == SubagentStatus.ACTIVE:
            # Fire-and-forget deactivate (can't await in sync method)
            import asyncio
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(inst.deactivate())
            except RuntimeError:
                pass

    def get(self, name: str) -> SubagentInstance | None:
        """Look up a subagent instance by name."""
        return self._instances.get(name)

    # ── Lifecycle ──────────────────────────────────────────────────────

    async def activate(self, name: str) -> SubagentInstance:
        """Initialize and activate a registered subagent.

        Raises:
            KeyError: If the subagent is not registered.
            RuntimeError: If initialization fails.
        """
        inst = self._instances.get(name)
        if inst is None:
            raise KeyError(
                f"Subagent '{name}' not registered. "
                f"Available: {self.list_all()}"
            )
        await inst.initialize()
        return inst

    async def deactivate(self, name: str) -> None:
        """Deactivate a subagent by name (no-op if not found or not active)."""
        inst = self._instances.get(name)
        if inst is not None:
            await inst.deactivate()

    async def deactivate_all(self) -> None:
        """Deactivate all active subagents."""
        for inst in self._instances.values():
            if inst.status == SubagentStatus.ACTIVE:
                await inst.deactivate()

    # ── Listing ────────────────────────────────────────────────────────

    def list_all(self) -> list[str]:
        """Return names of all registered subagents."""
        return list(self._instances.keys())

    def list_active(self) -> list[str]:
        """Return names of currently active subagents."""
        return [
            name
            for name, inst in self._instances.items()
            if inst.status == SubagentStatus.ACTIVE
        ]

    def list_details(self) -> list[dict[str, Any]]:
        """Return detailed info for all registered subagents."""
        return [
            {
                "name": inst.config.name,
                "domain": inst.config.domain,
                "status": inst.status.value,
                "capabilities": inst.config.capabilities,
                "delivery": [
                    {"type": d.type, "path": d.path or d.endpoint}
                    for d in inst.config.delivery
                ],
            }
            for inst in self._instances.values()
        ]

    # ── YAML config loading ────────────────────────────────────────────

    def load_configs(self) -> list[SubagentConfig]:
        """Scan ``config_dir`` for ``*.yaml`` files and parse them as SubagentConfig.

        Does NOT register the configs — call :meth:`register` separately.
        """
        configs: list[SubagentConfig] = []
        config_path = Path(self._config_dir)

        if not config_path.exists():
            return configs

        for yaml_file in sorted(config_path.glob("*.yaml")):
            try:
                cfg = self._load_one(yaml_file)
                if cfg is not None:
                    configs.append(cfg)
            except Exception:
                # Skip malformed files — log in production
                pass

        return configs

    def load_and_register(self) -> list[str]:
        """Load all YAML configs and register them. Returns list of names."""
        configs = self.load_configs()
        for cfg in configs:
            self.register(cfg)
        return self.list_all()

    # ── Private YAML parser ────────────────────────────────────────────

    def _load_one(self, filepath: Path) -> SubagentConfig | None:
        """Parse a single YAML file into a SubagentConfig."""
        with open(filepath, encoding="utf-8") as fh:
            raw = yaml.safe_load(fh)
        if not raw or not isinstance(raw, dict):
            return None

        name = raw.get("name", filepath.stem)

        # ── context ────────────────────────────────────────────────
        ctx_raw = raw.get("context", {})
        context = SubagentContext(
            domain=raw.get("domain", ""),
            databases=ctx_raw.get("databases", []),
            max_turns=int(ctx_raw.get("max_turns", 20)),
            enable_sql_edit=bool(ctx_raw.get("enable_sql_edit", True)),
            enable_execution=bool(ctx_raw.get("enable_execution", True)),
            enable_multi_candidate=bool(ctx_raw.get("enable_multi_candidate", True)),
        )

        # ── llm_config ─────────────────────────────────────────────
        llm_raw = raw.get("llm_config", {})
        llm_config = LLMConfig(
            model=llm_raw.get("model", "claude-sonnet-4"),
            temperature=float(llm_raw.get("temperature", 0.3)),
            max_tokens=int(llm_raw.get("max_tokens", 4096)),
        )

        # ── delivery ───────────────────────────────────────────────
        delivery = [
            DeliveryConfig(
                type=d.get("type", ""),
                path=d.get("path", ""),
                endpoint=d.get("endpoint", ""),
                tool_prefix=d.get("tool_prefix", ""),
            )
            for d in raw.get("delivery", [])
        ]

        # ── access_control ─────────────────────────────────────────
        ac_raw = raw.get("access_control", {})
        access_control = AccessControl(
            allowed_users=ac_raw.get("allowed_users", ["*"]),
            rate_limit=int(ac_raw.get("rate_limit", 100)),
        )

        return SubagentConfig(
            name=name,
            label=raw.get("label", {}),
            domain=raw.get("domain", ""),
            description=raw.get("description", ""),
            context=context,
            capabilities=raw.get("capabilities", []),
            delivery=delivery,
            llm_config=llm_config,
            access_control=access_control,
        )

    # ── Adaptive tuning (Phase 5.7) ─────────────────────────────────

    def track_usage(
        self, subagent_name: str, success: bool, latency_ms: int = 0
    ) -> None:
        """Record a usage outcome for adaptive tuning.

        Args:
            subagent_name: Name of the subagent that was invoked.
            success: Whether the invocation was successful.
            latency_ms: Wall-clock latency in milliseconds.
        """
        metrics = self._usage_stats.setdefault(
            subagent_name,
            {
                "total": 0,
                "success": 0,
                "latencies": [],
                "errors": [],
            },
        )
        metrics["total"] += 1
        if success:
            metrics["success"] += 1
        if latency_ms > 0:
            metrics["latencies"].append(latency_ms)
            # Keep only last 100 latencies
            if len(metrics["latencies"]) > 100:
                metrics["latencies"] = metrics["latencies"][-100:]

    def get_effectiveness_score(self, subagent_name: str) -> float:
        """Return the rolling success rate (last 100 invocations)."""
        metrics = self._usage_stats.get(subagent_name)
        if not metrics or metrics["total"] == 0:
            return 1.0
        recent = min(metrics["total"], 100)
        recent_success = min(metrics["success"], recent)
        return round(recent_success / max(recent, 1), 4) if recent > 0 else 1.0

    def suggest_config_changes(self, subagent_name: str) -> list[str]:
        """Suggest configuration changes based on usage patterns.

        Returns:
            List of human-readable suggestion strings.
        """
        suggestions: list[str] = []
        score = self.get_effectiveness_score(subagent_name)
        metrics = self._usage_stats.get(subagent_name, {})

        if score < 0.7 and metrics.get("total", 0) >= 10:
            suggestions.append(
                f"Success rate low ({score:.0%}): consider upgrading model "
                f"(e.g. haiku → sonnet) for subagent '{subagent_name}'"
            )

        latencies = metrics.get("latencies", [])
        if latencies:
            avg_lat = sum(latencies) / len(latencies)
            if avg_lat > 5000:
                suggestions.append(
                    f"High average latency ({avg_lat:.0f}ms): consider reducing "
                    f"context window or tool set for '{subagent_name}'"
                )

        return suggestions

    def get_metrics(self, subagent_name: str = "") -> dict[str, Any] | list[dict[str, Any]]:
        """Return usage metrics for one or all subagents.

        Args:
            subagent_name: Specific subagent name, or empty for all.

        Returns:
            Single metrics dict (if name given) or list of all metrics dicts.
        """
        if subagent_name:
            m = self._usage_stats.get(subagent_name, {})
            latencies = m.get("latencies", [])
            return {
                "subagent_name": subagent_name,
                "total_invocations": m.get("total", 0),
                "success_count": m.get("success", 0),
                "avg_latency_ms": round(sum(latencies) / len(latencies), 1)
                if latencies
                else 0.0,
                "last_100_success_rate": self.get_effectiveness_score(subagent_name),
                "top_errors": m.get("errors", [])[-5:],
                "suggested_adjustments": self.suggest_config_changes(subagent_name),
            }

        return [
            self.get_metrics(name)  # type: ignore[arg-type]
            for name in self._instances
        ]

    # ── Housekeeping ──────────────────────────────────────────────────

    def reset(self) -> None:
        """Clear all registered instances. Useful for testing."""
        self._instances.clear()
        self._usage_stats.clear()


# ── Module-level singleton ──────────────────────────────────────────────

subagent_manager = SubagentManager()
