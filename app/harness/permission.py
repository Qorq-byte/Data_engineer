"""PermissionManager — three-tier tool access control.

See SPEC §3.4.5 (agent.yml permissions) and §4.12.3 (③ PermissionManager).

Tiers:
  - allow: Tool executes silently.
  - deny:  Tool execution is blocked; an error is logged.
  - ask:   Tool execution is blocked pending user confirmation (Phase 2: UI popup).

Permission rules are loaded from agent.yml's nodes.{node}.permissions map.
"""

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class PermissionLevel(StrEnum):
    ALLOW = "allow"
    DENY = "deny"
    ASK = "ask"


@dataclass
class ToolPermission:
    """A single permission rule for a tool."""

    tool_name: str
    level: PermissionLevel = PermissionLevel.DENY
    conditions: dict[str, Any] = field(default_factory=dict)


class PermissionManager:
    """Enforces tool access control.

    Rules are loaded from agent.yml (nodes.{node}.permissions).
    The default level for unlisted tools is 'deny' (secure-by-default).
    """

    def __init__(
        self,
        rules: list[dict[str, str]] | None = None,
        default_level: str = "deny",
    ):
        self.default_level = PermissionLevel(default_level)
        self._rules: dict[str, PermissionLevel] = {}
        if rules:
            for rule in rules:
                for tool_name, level_str in rule.items():
                    self._rules[tool_name] = PermissionLevel(level_str)

    def check(
        self,
        tool_name: str,
        context: dict[str, Any] | None = None,
    ) -> str:
        """Check if a tool is allowed in the given context.

        Args:
            tool_name: The tool being called (e.g., "execute_read_query").
            context: Optional context dict for future conditional rules.

        Returns:
            "allow", "deny", or "ask".

        Harness invariant: the caller MUST respect "deny" — the tool
        MUST NOT execute. The caller MAY choose to prompt for "ask".
        """
        level = self._rules.get(tool_name, self.default_level)

        # Future: conditional rules based on context
        # e.g., "execute_read_query: allow IF read_only=True"

        return level.value

    def is_allowed(self, tool_name: str, context: dict | None = None) -> bool:
        """Convenience: returns True only if permission is 'allow'."""
        return self.check(tool_name, context) == "allow"

    def is_denied(self, tool_name: str, context: dict | None = None) -> bool:
        """Convenience: returns True if permission is 'deny'."""
        return self.check(tool_name, context) == "deny"

    def add_rule(self, tool_name: str, level: str) -> None:
        """Add or update a permission rule at runtime."""
        self._rules[tool_name] = PermissionLevel(level)

    def remove_rule(self, tool_name: str) -> None:
        """Remove a permission rule (falls back to default)."""
        self._rules.pop(tool_name, None)

    def list_rules(self) -> dict[str, str]:
        """Return all current rules as {tool_name: level}."""
        return {k: v.value for k, v in self._rules.items()}
