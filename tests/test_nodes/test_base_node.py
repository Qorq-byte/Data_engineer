"""Tests for BaseNode, NodeInput, NodeOutput."""

import pytest

from app.nodes.base import BaseNode, NodeInput, NodeOutput


class _TestNode(BaseNode):
    """Minimal concrete node for testing."""

    name = "test"
    description = "Test node"

    async def execute(self, input: NodeInput) -> NodeOutput:
        return NodeOutput(
            result=input.query_text.upper(),
            metadata={"echo": True},
            context={"last_test": input.query_text},
        )


@pytest.mark.asyncio
async def test_base_node_setup_input_defaults():
    node = _TestNode()
    result = await node.setup_input({})
    assert isinstance(result, NodeInput)
    assert result.query_text == ""
    assert result.context == {}
    assert result.config == {}


@pytest.mark.asyncio
async def test_base_node_setup_input_with_data():
    node = _TestNode()
    raw = {
        "query_text": "SELECT * FROM users",
        "context": {"db": "test"},
        "config": {"max_rows": 50},
        "extra": "ignored",
    }
    result = await node.setup_input(raw)
    assert result.query_text == "SELECT * FROM users"
    assert result.context == {"db": "test"}
    assert result.config == {"max_rows": 50}


@pytest.mark.asyncio
async def test_base_node_execute():
    node = _TestNode()
    output = await node.execute(
        NodeInput(query_text="hello", context={}, config={})
    )
    assert output.result == "HELLO"
    assert output.metadata["echo"] is True
    assert output.errors == []


@pytest.mark.asyncio
async def test_base_node_update_context():
    node = _TestNode()
    shared = {"existing": "data", "query_text": "old"}
    output = NodeOutput(
        result="done",
        context={"new_key": "new_value", "query_text": "updated"},
    )
    merged = await node.update_context(output, shared)
    assert merged["existing"] == "data"
    assert merged["new_key"] == "new_value"
    assert merged["query_text"] == "updated"


def test_node_registry():
    from app.nodes.registry import NodeRegistry

    registry = NodeRegistry()
    node = _TestNode()
    registry.register(node)

    assert "test" in registry
    assert len(registry) == 1
    assert registry.list_all() == ["test"]
    assert registry.get("test") is node

    with pytest.raises(KeyError):
        registry.get("nonexistent")

    with pytest.raises(ValueError, match="already registered"):
        registry.register(_TestNode())

    registry.reset()
    assert len(registry) == 0
