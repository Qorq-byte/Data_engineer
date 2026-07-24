"""Custom Node template — copy and modify to create your own node.

See ``docs/node-development.md`` for the complete guide.

Usage::

    # 1. Copy this file to a new name (e.g., app/nodes/my_custom_node.py).
    # 2. Search for ``TODO`` and fill in your implementation.
    # 3. Register in ``app/config/agent.yml`` under ``agent.nodes``.
    # 4. Register in ``app/nodes/registry.py`` (node_registry).
    # 5. (Optional) Register in ``main.py`` for startup.

"""

from __future__ import annotations

from typing import Any

from app.nodes.base import BaseNode, NodeInput, NodeOutput


class MyCustomNode(BaseNode):
    """TODO: Describe what this node does.

    Replace ``MyCustomNode`` with a descriptive name (e.g., ``DataQualityNode``,
    ``CacheCheckNode``, ``AuditLogNode``).
    """

    name: str = "my_custom_node"  # TODO: change to your node name
    description: str = "TODO: one-line description"

    async def execute(self, node_input: NodeInput) -> NodeOutput:
        """Core execution logic.

        Args:
            node_input: Contains ``query_text``, ``config``, and ``context``.

        Returns:
            NodeOutput with ``result``, ``errors``, and ``context`` fields.

        TODO: Implement your logic here.  Example skeleton below.
        """
        # ── 1. Read from input ────────────────────────────────────────
        query_text = node_input.query_text
        config = node_input.config or {}  # noqa: F841

        # ── 2. Your business logic ─────────────────────────────────────
        # TODO: Replace with your actual implementation.
        result_data: dict[str, Any] = {
            "processed": True,
            "input_length": len(query_text),
        }

        # ── 3. Return output ───────────────────────────────────────────
        return NodeOutput(
            result=result_data,
            metadata={"ok": True},
            errors=[],
        )

    async def setup_input(self, context: dict[str, Any]) -> NodeInput:
        """Prepare input from the workflow context.

        Called by :class:`~app.harness.runner.WorkflowRunner` before
        :meth:`execute`.

        Args:
            context: The shared workflow context dict accumulated
                during this runner invocation.

        Returns:
            A populated :class:`NodeInput`.
        """
        return NodeInput(
            query_text=context.get("query_text", context.get("nl_text", "")),
            config=context.get("config", {}),
            context=context,
        )

    async def update_context(
        self, output: NodeOutput, context: dict[str, Any]
    ) -> dict[str, Any]:
        """Write results back to the shared workflow context.

        Called by :class:`~app.harness.runner.WorkflowRunner` after
        :meth:`execute`.

        Args:
            output: The :class:`NodeOutput` from :meth:`execute`.
            context: Mutable shared workflow context dict.
        """
        result = {**context}
        result[f"{self.name}_output"] = output.result
        result[f"{self.name}_metadata"] = output.metadata
        return result


# ── Optional: register in tests ────────────────────────────────────────────
# Add to tests/test_nodes/ to verify your node works:
#
#   from app.nodes.my_custom_node import MyCustomNode
#
#   class TestMyCustomNode:
#       @pytest.mark.asyncio
#       async def test_execute(self):
#           node = MyCustomNode()
#           node_input = await node.setup_input({"query_text": "test"})
#           output = await node.execute(node_input)
#           assert not output.errors
#           assert output.result["processed"] is True
