"""Subagent Router — dispatches requests across 3 delivery channels.

See SPEC §4.10.4 (runtime architecture) and §4.10.5 (delivery channels).

The router sits in front of the :class:`SubagentManager` and provides
channel-specific routing logic:

  - **API**: lookup by subagent name → invoke ``instance.query()``
  - **Web**: return web path for front-end routing
  - **MCP**: return tool prefix for MCP server tool registration
"""

from __future__ import annotations

from typing import Any

from app.subagent.instance import SubagentStatus
from app.subagent.manager import SubagentManager, subagent_manager


class SubagentRouter:
    """Routes requests to the correct subagent via API, Web, or MCP channels.

    Usage::

        router = SubagentRouter(manager=subagent_manager)
        # API channel
        result = await router.route_api("ecommerce", "orders last month", {})
        # Web channel
        paths = router.list_web_subagents()
        # MCP channel
        prefix = router.get_mcp_tool_prefix("ecommerce")
    """

    def __init__(self, manager: SubagentManager | None = None) -> None:
        self._manager = manager or subagent_manager

    # ── API Channel ────────────────────────────────────────────────────

    async def route_api(
        self,
        name: str,
        nl_text: str,
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Route an API request to the named subagent.

        Args:
            name: Subagent name as registered in the manager.
            nl_text: Natural language query text.
            context: Optional extra context.

        Returns:
            dict with ``status``, ``data``, ``errors``, ``trace_id``.

        Raises:
            KeyError: If the subagent is not registered.
            PermissionError: If access control blocks the request.
        """
        inst = self._manager.get(name)
        if inst is None:
            raise KeyError(
                f"Subagent '{name}' not found. Available: {self._manager.list_all()}"
            )

        if inst.status != SubagentStatus.ACTIVE:
            return {
                "status": "rejected",
                "data": None,
                "errors": [f"Subagent '{name}' is not active"],
                "trace_id": "",
            }

        result = await inst.query(nl_text, context)
        return {
            "status": result.status,
            "data": result.data,
            "errors": result.errors,
            "trace_id": result.trace_id,
        }

    def list_api_endpoints(self) -> list[dict[str, str]]:
        """Return API endpoint info for all registered subagents."""
        endpoints: list[dict[str, str]] = []
        for name, inst in self._manager._instances.items():
            for d in inst.config.delivery:
                if d.type == "api":
                    endpoints.append({
                        "name": name,
                        "endpoint": d.endpoint or f"/api/v1/subagents/{name}",
                    })
        return endpoints

    # ── Web Channel ────────────────────────────────────────────────────

    def get_web_path(self, name: str) -> str | None:
        """Return the Web UI path for a subagent, or None."""
        inst = self._manager.get(name)
        if inst is None:
            return None
        for d in inst.config.delivery:
            if d.type == "web":
                return d.path
        return None

    def list_web_subagents(self) -> list[dict[str, str]]:
        """Return Web UI info for all subagents with web delivery."""
        web_list: list[dict[str, str]] = []
        for name, inst in self._manager._instances.items():
            for d in inst.config.delivery:
                if d.type == "web":
                    web_list.append({
                        "name": name,
                        "label_zh": inst.config.label_zh,
                        "label_en": inst.config.label_en,
                        "path": d.path,
                        "domain": inst.config.domain,
                        "description": inst.config.description,
                    })
        return web_list

    # ── MCP Channel ────────────────────────────────────────────────────

    def get_mcp_tool_prefix(self, name: str) -> str | None:
        """Return the MCP tool prefix for a subagent, or None."""
        inst = self._manager.get(name)
        if inst is None:
            return None
        for d in inst.config.delivery:
            if d.type == "mcp":
                return d.tool_prefix
        return None

    def list_mcp_subagents(self) -> list[dict[str, str]]:
        """Return MCP tool info for all subagents with MCP delivery."""
        mcp_list: list[dict[str, str]] = []
        for name, inst in self._manager._instances.items():
            for d in inst.config.delivery:
                if d.type == "mcp":
                    mcp_list.append({
                        "name": name,
                        "tool_prefix": d.tool_prefix,
                        "description": inst.config.description,
                    })
        return mcp_list

    # ── Access control ─────────────────────────────────────────────────

    def check_access(self, name: str, user: str | None = None) -> bool:
        """Check whether *user* is allowed to access a subagent.

        Returns True if ``allowed_users`` contains ``"*"`` or *user*.
        """
        inst = self._manager.get(name)
        if inst is None:
            return False
        allowed = inst.config.access_control.allowed_users
        if "*" in allowed:
            return True
        return bool(user is not None and user in allowed)

    def check_rate_limit(self, name: str, _current_count: int | None = None) -> bool:
        """Check if the subagent is within its rate limit.

        Phase 4: simple check against `rate_limit` in config (no counter).
        Phase 5: actual sliding-window counter.
        """
        inst = self._manager.get(name)
        if inst is None:
            return False
        limit = inst.config.access_control.rate_limit
        return not (_current_count is not None and _current_count >= limit)


# ── Module-level singleton ──────────────────────────────────────────────

subagent_router = SubagentRouter(manager=subagent_manager)
