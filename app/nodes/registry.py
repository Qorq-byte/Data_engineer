"""NodeRegistry — global registry of all available nodes.

WorkflowRunner resolves node names from workflow.yml's node_order
into actual BaseNode instances through this registry.
"""

from app.nodes.base import BaseNode


class NodeRegistry:
    """Thread-safe registry mapping node names → BaseNode instances."""

    _nodes: dict[str, BaseNode] = {}

    def register(self, node: BaseNode) -> None:
        """Register a node instance.

        Raises ValueError if a node with the same name already exists.
        """
        if node.name in self._nodes:
            raise ValueError(
                f"Node '{node.name}' is already registered. "
                f"Unregister it first or use a different name."
            )
        self._nodes[node.name] = node

    def get(self, name: str) -> BaseNode:
        """Look up a node by name.

        Raises KeyError if the node is not found.
        """
        if name not in self._nodes:
            available = ", ".join(sorted(self._nodes.keys()))
            raise KeyError(
                f"Node '{name}' not found in registry. Available: [{available}]"
            )
        return self._nodes[name]

    def unregister(self, name: str) -> None:
        """Remove a node from the registry."""
        self._nodes.pop(name, None)

    def list_all(self) -> list[str]:
        """Return sorted list of all registered node names."""
        return sorted(self._nodes.keys())

    def reset(self) -> None:
        """Clear all registered nodes. Useful for testing."""
        self._nodes.clear()

    def __contains__(self, name: str) -> bool:
        return name in self._nodes

    def __len__(self) -> int:
        return len(self._nodes)


# Global singleton instance
node_registry = NodeRegistry()
