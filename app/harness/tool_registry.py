"""ToolRegistry — unified registry for func tools and MCP tools.

See SPEC §4.12.3 (② Tool integration).

AgenticNode uses this registry to manage callable tools by name:
  - func tools: plain Python callables registered directly
  - mcp tools: callables that proxy to an MCP server (registered with
    source="mcp" and the owning server name in metadata)

Routing is by name only — the caller is responsible for checking
permissions via PermissionManager before invoking a tool.
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ToolEntry:
    """A single registered tool."""

    name: str
    func: Callable[..., Any]
    description: str = ""
    source: str = "func"  # "func" | "mcp" | "skill"
    metadata: dict[str, Any] = field(default_factory=dict)


class ToolRegistry:
    """Name-keyed registry of callable tools.

    Later registrations overwrite earlier ones (latest wins), matching
    the behavior of the global node/MCP registries.
    """

    def __init__(self) -> None:
        self._tools: dict[str, ToolEntry] = {}

    def register(
        self,
        name: str,
        func: Callable[..., Any],
        description: str = "",
        source: str = "func",
        **metadata: Any,
    ) -> None:
        """Register a callable tool under a name."""
        self._tools[name] = ToolEntry(
            name=name,
            func=func,
            description=description,
            source=source,
            metadata=metadata,
        )

    def register_mcp_server(self, server_name: str, tools: dict[str, Callable[..., Any]]) -> None:
        """Register all tools exposed by an MCP server."""
        for tool_name, func in tools.items():
            self.register(tool_name, func, source="mcp", server=server_name)

    def get(self, name: str) -> ToolEntry | None:
        """Return the tool entry, or None if not registered."""
        return self._tools.get(name)

    def has(self, name: str) -> bool:
        """Check whether a tool is registered."""
        return name in self._tools

    def call(self, name: str, *args: Any, **kwargs: Any) -> Any:
        """Invoke a registered tool by name.

        Raises:
            KeyError: If the tool is not registered.
        """
        entry = self._tools.get(name)
        if entry is None:
            raise KeyError(f"Tool '{name}' is not registered")
        return entry.func(*args, **kwargs)

    def list_all(self) -> list[str]:
        """Return all registered tool names (sorted)."""
        return sorted(self._tools)

    def unregister(self, name: str) -> None:
        """Remove a tool (no-op if absent)."""
        self._tools.pop(name, None)

    def __len__(self) -> int:
        return len(self._tools)

    def __contains__(self, name: str) -> bool:
        return name in self._tools
