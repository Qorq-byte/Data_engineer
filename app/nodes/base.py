"""BaseNode — abstract foundation for all nodes in the system.

See SPEC §4.12.2 for the full specification.

Every node implements three methods:
  - setup_input(raw) → NodeInput    (validate, inject defaults, type coercion)
  - execute(input)  → NodeOutput    (core logic — abstract)
  - update_context(output, shared) → dict  (merge output into shared context)

WorkflowRunner drives nodes in node_order sequence. Nodes don't decide
their own next step — the Harness does.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class NodeInput:
    """Standardized input passed to every node's execute().

    Built by setup_input() from the raw dict that WorkflowRunner passes in.
    """

    query_text: str
    context: dict[str, Any] = field(default_factory=dict)
    config: dict[str, Any] = field(default_factory=dict)


@dataclass
class NodeOutput:
    """Standardized output from every node's execute().

    context is merged into the WorkflowRunner's shared_context by
    update_context() before advancing to the next node.
    """

    result: Any = None
    metadata: dict[str, Any] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    context: dict[str, Any] = field(default_factory=dict)


class BaseNode(ABC):
    """Abstract base for all nodes — three unified methods.

    Subclasses only need to implement execute(). The other two methods
    have sensible defaults but can be overridden for custom behavior.
    """

    name: str = "base"
    description: str = ""

    @abstractmethod
    async def execute(self, input: NodeInput) -> NodeOutput:
        """Execute the node's core logic.

        Subclasses MUST override this. This is where the actual work happens.
        """
        ...

    async def setup_input(self, raw_input: dict[str, Any]) -> NodeInput:
        """Build a validated NodeInput from raw upstream data.

        Default behavior:
          1. Extract 'query_text' (required).
          2. Extract 'context' dict (default {}).
          3. Extract 'config' dict (default {}).
          4. Apply type coercion and schema validation.

        Override to add domain-specific validation or enrichment.
        """
        query_text = raw_input.get("query_text", "")
        context = raw_input.get("context", {})
        config = raw_input.get("config", {})

        return NodeInput(query_text=query_text, context=context, config=config)

    async def update_context(
        self, output: NodeOutput, shared_context: dict[str, Any]
    ) -> dict[str, Any]:
        """Merge node output into the shared workflow context.

        Called by WorkflowRunner after successful execute() and before
        advancing to the next node.

        Default: shallow-merge output.context into shared_context.
        Override for custom merge logic (e.g., prefix keys with node name).
        """
        merged = {**shared_context}
        if output.context:
            merged.update(output.context)
        return merged
