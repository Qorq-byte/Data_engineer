"""Tests for ParseNLNode — the NL→SQR pipeline wrapped as a Harness Node."""

import pytest

from app.models.query import SQR, IntentType
from app.nodes.base import NodeInput, NodeOutput
from app.nodes.parse_nl import ParseNLNode

pytestmark = pytest.mark.anyio


@pytest.fixture
def node():
    return ParseNLNode()


# ═══════════════════════════════════════════════════════════════════════════
# Node metadata
# ═══════════════════════════════════════════════════════════════════════════


class TestNodeMetadata:
    def test_name(self, node):
        assert node.name == "parse_nl"

    def test_description(self, node):
        assert node.description
        assert "SQR" in node.description

    def test_is_base_node(self, node):
        from app.nodes.agentic import AgenticNode
        from app.nodes.base import BaseNode

        assert isinstance(node, BaseNode)
        assert not isinstance(node, AgenticNode)


# ═══════════════════════════════════════════════════════════════════════════
# Lifecycle — setup_input
# ═══════════════════════════════════════════════════════════════════════════


class TestSetupInput:
    async def test_default_behavior(self, node):
        result = await node.setup_input({})
        assert isinstance(result, NodeInput)
        assert result.query_text == ""

    async def test_extracts_query_text(self, node):
        result = await node.setup_input({"query_text": "查询所有订单"})
        assert result.query_text == "查询所有订单"

    async def test_extracts_context(self, node):
        result = await node.setup_input(
            {"query_text": "test", "context": {"domain": "ecommerce"}}
        )
        assert result.context == {"domain": "ecommerce"}

    async def test_extracts_config(self, node):
        result = await node.setup_input(
            {"query_text": "test", "config": {"max_rows": 50}}
        )
        assert result.config == {"max_rows": 50}


# ═══════════════════════════════════════════════════════════════════════════
# Lifecycle — execute (Chinese)
# ═══════════════════════════════════════════════════════════════════════════


class TestExecuteChinese:
    async def test_simple_select(self, node):
        output = await node.execute(NodeInput(query_text="查询所有订单"))
        assert output.metadata["status"] == "success"
        assert isinstance(output.result, SQR)
        assert output.result.language == "zh"
        assert output.result.intent == IntentType.SELECT

    async def test_aggregate(self, node):
        output = await node.execute(NodeInput(query_text="统计上个月的销售额"))
        assert output.metadata["status"] == "success"
        assert output.result.intent == IntentType.AGGREGATE
        assert output.result.time_range is not None

    async def test_with_limit_and_order(self, node):
        output = await node.execute(NodeInput(query_text="查询前10个订单按金额降序"))
        assert output.result.limit == 10
        assert len(output.result.order_by) >= 1

    async def test_with_ambiguities(self, node):
        output = await node.execute(NodeInput(query_text="最近大额订单按地区排名"))
        assert len(output.result.ambiguities) > 0

    async def test_time_series(self, node):
        output = await node.execute(NodeInput(query_text="按月统计销售额趋势"))
        assert output.result.intent == IntentType.TIME_SERIES

    async def test_comparison(self, node):
        output = await node.execute(NodeInput(query_text="对比北京和上海的销售"))
        assert output.result.intent == IntentType.COMPARISON

    async def test_funnel(self, node):
        output = await node.execute(NodeInput(query_text="用户转化率漏斗分析"))
        assert output.result.intent == IntentType.FUNNEL

    async def test_join(self, node):
        output = await node.execute(NodeInput(query_text="关联用户表和订单表查询"))
        assert output.result.intent == IntentType.JOIN


# ═══════════════════════════════════════════════════════════════════════════
# Lifecycle — execute (English)
# ═══════════════════════════════════════════════════════════════════════════


class TestExecuteEnglish:
    async def test_simple_select(self, node):
        output = await node.execute(NodeInput(query_text="find all orders"))
        assert output.result.language == "en"
        assert output.result.intent == IntentType.SELECT

    async def test_aggregate(self, node):
        output = await node.execute(NodeInput(query_text="count orders last month"))
        assert output.result.intent == IntentType.AGGREGATE
        assert output.result.time_range is not None

    async def test_with_limit(self, node):
        output = await node.execute(NodeInput(query_text="top 5 products by revenue"))
        assert output.result.limit == 5

    async def test_with_order(self, node):
        output = await node.execute(NodeInput(query_text="orders sorted by date desc"))
        assert any(o.direction == "DESC" for o in output.result.order_by)


# ═══════════════════════════════════════════════════════════════════════════
# Lifecycle — execute edge cases
# ═══════════════════════════════════════════════════════════════════════════


class TestExecuteEdgeCases:
    async def test_empty_text(self, node):
        output = await node.execute(NodeInput(query_text=""))
        assert output.metadata["status"] == "empty_input"
        assert output.result is None
        assert len(output.errors) >= 1

    async def test_whitespace_only(self, node):
        output = await node.execute(NodeInput(query_text="   "))
        assert output.metadata["status"] == "empty_input"

    async def test_pure_numbers(self, node):
        output = await node.execute(NodeInput(query_text="12345"))
        assert output.metadata["status"] == "success"
        assert output.result.intent == IntentType.UNKNOWN

    async def test_mixed_language(self, node):
        output = await node.execute(NodeInput(query_text="查询iPhone 15 sales数据"))
        assert output.result.language == "mixed"

    async def test_confidence_in_output(self, node):
        output = await node.execute(NodeInput(query_text="查询订单"))
        assert 0.0 <= output.metadata["confidence"] <= 1.0

    async def test_metadata_keys(self, node):
        output = await node.execute(NodeInput(query_text="统计上个月各地区的销售额"))
        assert "language" in output.metadata
        assert "intent" in output.metadata
        assert "confidence" in output.metadata
        assert "ambiguity_count" in output.metadata
        assert "table_count" in output.metadata
        assert "entity_count" in output.metadata


# ═══════════════════════════════════════════════════════════════════════════
# Lifecycle — update_context
# ═══════════════════════════════════════════════════════════════════════════


class TestUpdateContext:
    async def test_sqr_stored_in_context(self, node):
        output = await node.execute(NodeInput(query_text="查询所有订单"))
        shared = {"query_text": "查询所有订单", "existing": "value"}
        merged = await node.update_context(output, shared)

        assert "sqr" in merged
        assert isinstance(merged["sqr"], SQR)
        assert merged["existing"] == "value"  # preserved

    async def test_convenience_keys_added(self, node):
        output = await node.execute(NodeInput(query_text="统计上个月的销售额"))
        shared = {"query_text": "统计上个月的销售额"}
        merged = await node.update_context(output, shared)

        assert merged["sqr_intent"] == IntentType.AGGREGATE
        assert merged["sqr_language"] == "zh"
        assert isinstance(merged["sqr_confidence"], float)

    async def test_failed_output_no_convenience_keys(self, node):
        output = NodeOutput(
            result=None,
            errors=["test error"],
            metadata={"status": "parse_error"},
        )
        shared = {"query_text": "test"}
        merged = await node.update_context(output, shared)
        # No convenience keys should be added for failed output
        assert "sqr_intent" not in merged
        assert "sqr_language" not in merged

    async def test_existing_keys_preserved(self, node):
        """Existing shared context keys take precedence over convenience keys."""
        output = await node.execute(NodeInput(query_text="查询所有订单"))
        shared = {
            "query_text": "查询所有订单",
            "sqr_intent": "OVERRIDE_ME_NOT",
        }
        merged = await node.update_context(output, shared)
        # setdefault means existing keys are NOT overwritten
        assert merged["sqr_intent"] == "OVERRIDE_ME_NOT"


# ═══════════════════════════════════════════════════════════════════════════
# Registry integration
# ═══════════════════════════════════════════════════════════════════════════


class TestRegistryIntegration:
    @pytest.fixture(autouse=True)
    def _clean_registry(self):
        """Reset the global class-level state before each test."""
        from app.nodes.registry import NodeRegistry

        NodeRegistry._nodes.clear()
        yield
        NodeRegistry._nodes.clear()

    def test_can_register(self, node):
        from app.nodes.registry import NodeRegistry

        registry = NodeRegistry()
        registry.register(node)
        assert "parse_nl" in registry
        retrieved = registry.get("parse_nl")
        assert retrieved is node

    def test_can_unregister(self, node):
        from app.nodes.registry import NodeRegistry

        registry = NodeRegistry()
        registry.register(node)
        registry.unregister("parse_nl")
        assert "parse_nl" not in registry

    def test_duplicate_register_raises(self, node):
        from app.nodes.registry import NodeRegistry

        registry = NodeRegistry()
        registry.register(node)
        with pytest.raises(ValueError, match="already registered"):
            registry.register(ParseNLNode())


# ═══════════════════════════════════════════════════════════════════════════
# Custom parser injection
# ═══════════════════════════════════════════════════════════════════════════


class TestCustomParserInjection:
    async def test_custom_parser_used(self):
        """ParseNLNode should accept and use a custom NLParser instance."""
        from app.core.nlp_parser import NLParser

        custom = NLParser()
        node = ParseNLNode(parser=custom)
        assert node.parser is custom

        output = await node.execute(NodeInput(query_text="查询订单"))
        assert output.metadata["status"] == "success"

    async def test_lazy_parser_creation(self):
        """When no parser is given, one should be created on first access."""
        node = ParseNLNode()
        assert node._parser is None
        _ = node.parser  # trigger lazy init
        assert node._parser is not None
        from app.core.nlp_parser import NLParser

        assert isinstance(node._parser, NLParser)


# ═══════════════════════════════════════════════════════════════════════════
# WorkflowRunner compatibility
# ═══════════════════════════════════════════════════════════════════════════


class TestWorkflowRunnerCompat:
    """Simulate how WorkflowRunner drives a node."""

    async def test_full_lifecycle_simulation(self, node):
        """Simulate WorkflowRunner's node execution cycle."""
        # 1. Setup
        raw = {
            "query_text": "统计上个月各地区的销售额前10名",
            "context": {"domain": "ecommerce"},
            "config": {},
        }
        node_input = await node.setup_input(raw)

        # 2. Execute
        output = await node.execute(node_input)
        assert output.metadata["status"] == "success"

        # 3. Update context
        shared = {"query_text": raw["query_text"]}
        merged = await node.update_context(output, shared)

        # 4. Verify downstream can consume SQR
        assert "sqr" in merged
        sqr = merged["sqr"]
        assert sqr.intent == IntentType.AGGREGATE
        assert sqr.limit == 10
        assert sqr.time_range is not None

    async def test_workflow_chain_simulation(self, node):
        """Simulate a 2-node workflow: parse_nl → schema_linking (stub)."""
        from app.nodes.schema_linking import SchemaLinkingNode

        # Step 1: parse_nl
        raw = {"query_text": "查询订单表的所有数据", "context": {}, "config": {}}
        nl_input = await node.setup_input(raw)
        nl_output = await node.execute(nl_input)
        shared = await node.update_context(nl_output, raw["context"])

        # Step 2: schema_linking consumes SQR from shared context
        link_node = SchemaLinkingNode()
        link_raw = {
            "query_text": raw["query_text"],
            "context": shared,  # contains "sqr"
            "config": {},
        }
        link_input = await link_node.setup_input(link_raw)
        link_output = await link_node.execute(link_input)

        assert link_output is not None
        # schema_linking stub returns empty tables for now
        assert link_output.result is not None
