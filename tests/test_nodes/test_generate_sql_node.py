"""Tests for GenerateSQLNode — the NL→SQL generation node."""

import pytest

from app.models.query import SQR, IntentType
from app.nodes.base import NodeInput, NodeOutput
from app.nodes.generate_sql import GenerateSQLNode

pytestmark = pytest.mark.anyio


# ── Fixtures ────────────────────────────────────────────────────────────


@pytest.fixture
def node():
    return GenerateSQLNode()


@pytest.fixture
def sample_sqr():
    return SQR(
        raw_text="查询所有订单",
        language="zh",
        intent=IntentType.SELECT,
    )


# ═══════════════════════════════════════════════════════════════════════════
# Node metadata
# ═══════════════════════════════════════════════════════════════════════════


class TestNodeMetadata:
    def test_name(self, node):
        assert node.name == "generate_sql"

    def test_description(self, node):
        assert node.description
        assert "SQL" in node.description

    def test_is_agentic_node(self, node):
        from app.nodes.agentic import AgenticNode

        assert isinstance(node, AgenticNode)

    def test_lazy_router_created(self, node):
        assert node.router is not None

    def test_lazy_prompt_builder_created(self, node):
        assert node.prompt_builder is not None

    def test_lazy_generator_created(self, node):
        assert node.generator is not None


# ═══════════════════════════════════════════════════════════════════════════
# execute — basic
# ═══════════════════════════════════════════════════════════════════════════


class TestExecuteBasic:
    async def test_generates_single_candidate(self, node, sample_sqr):
        output = await node.execute(
            NodeInput(
                query_text="查询所有订单",
                context={"sqr": sample_sqr},
            )
        )
        assert output.metadata["status"] == "success"
        assert output.metadata["candidates_count"] == 1
        assert output.result["primary_sql"] is not None

    async def test_generates_multiple_candidates(self, node, sample_sqr):
        output = await node.execute(
            NodeInput(
                query_text="查询所有订单",
                context={"sqr": sample_sqr},
                config={"num_candidates": 3},
            )
        )
        assert output.metadata["candidates_count"] == 3
        assert len(output.result["candidates"]) == 3

    async def test_fallback_when_no_sqr_in_context(self, node):
        """When no SQR in context, wraps query_text."""
        output = await node.execute(
            NodeInput(query_text="SELECT * FROM users")
        )
        assert output.metadata["status"] == "success"
        assert output.result["primary_sql"] is not None

    async def test_primary_candidate_in_context(self, node, sample_sqr):
        output = await node.execute(
            NodeInput(query_text="查询所有订单", context={"sqr": sample_sqr})
        )
        assert "primary_candidate" in output.context
        assert output.context["primary_candidate"] is not None

    async def test_candidates_in_context(self, node, sample_sqr):
        output = await node.execute(
            NodeInput(query_text="查询所有订单", context={"sqr": sample_sqr})
        )
        assert "candidates" in output.context
        assert len(output.context["candidates"]) >= 1


# ═══════════════════════════════════════════════════════════════════════════
# execute — with config
# ═══════════════════════════════════════════════════════════════════════════


class TestExecuteWithConfig:
    async def test_model_override(self, node, sample_sqr):
        output = await node.execute(
            NodeInput(
                query_text="查询所有订单",
                context={"sqr": sample_sqr},
                config={"model": "claude-haiku-4-5"},
            )
        )
        assert output.metadata["status"] == "success"

    async def test_dialect_override(self, node, sample_sqr):
        output = await node.execute(
            NodeInput(
                query_text="查询所有订单",
                context={"sqr": sample_sqr},
                config={"dialect": "mysql"},
            )
        )
        assert output.metadata["status"] == "success"

    async def test_multi_perspective_strategy(self, node, sample_sqr):
        output = await node.execute(
            NodeInput(
                query_text="查询所有订单",
                context={"sqr": sample_sqr},
                config={"strategy": "multi_perspective", "num_candidates": 2},
            )
        )
        assert output.metadata["status"] == "success"
        assert output.metadata["candidates_count"] == 2


# ═══════════════════════════════════════════════════════════════════════════
# execute — streaming
# ═══════════════════════════════════════════════════════════════════════════


class TestExecuteStreaming:
    async def test_streaming_mode(self, node, sample_sqr):
        output = await node.execute(
            NodeInput(
                query_text="查询所有订单",
                context={"sqr": sample_sqr},
                config={"streaming": True},
            )
        )
        assert output.metadata["status"] == "success"
        assert output.result["primary_sql"] is not None
        # Streaming collects chunks and assembles a single candidate
        assert output.metadata["candidates_count"] == 1


# ═══════════════════════════════════════════════════════════════════════════
# execute — with schema and domain
# ═══════════════════════════════════════════════════════════════════════════


class TestExecuteWithContext:
    async def test_with_schema_in_context(self, node, sample_sqr):
        from app.models.schema import ColumnSchema, SchemaSnapshot, TableSchema

        schema = SchemaSnapshot(
            database_type="postgresql",
            database_name="test",
            tables={
                "users": TableSchema(
                    name="users",
                    columns=[ColumnSchema(name="id", type="INTEGER")],
                ),
            },
        )
        output = await node.execute(
            NodeInput(
                query_text="find users",
                context={"sqr": sample_sqr, "schema": schema},
            )
        )
        assert output.metadata["status"] == "success"

    async def test_with_domain_in_context(self, node, sample_sqr):
        from app.models.domain import DomainConfig

        domain = DomainConfig(name="ecommerce")
        output = await node.execute(
            NodeInput(
                query_text="查询订单",
                context={"sqr": sample_sqr, "domain": domain},
            )
        )
        assert output.metadata["status"] == "success"

    async def test_with_history_in_context(self, node, sample_sqr):
        history = [{"nl_input": "previous query", "sql": "SELECT 1"}]
        output = await node.execute(
            NodeInput(
                query_text="查询订单",
                context={"sqr": sample_sqr, "history": history},
            )
        )
        assert output.metadata["status"] == "success"


# ═══════════════════════════════════════════════════════════════════════════
# execute — edge cases
# ═══════════════════════════════════════════════════════════════════════════


class TestExecuteEdgeCases:
    async def test_empty_query_text(self, node):
        output = await node.execute(NodeInput(query_text=""))
        assert output.metadata["status"] == "success"

    async def test_return_type(self, node, sample_sqr):
        output = await node.execute(
            NodeInput(query_text="test", context={"sqr": sample_sqr})
        )
        assert isinstance(output, NodeOutput)


# ═══════════════════════════════════════════════════════════════════════════
# WorkflowRunner compatibility
# ═══════════════════════════════════════════════════════════════════════════


class TestWorkflowChain:
    async def test_parse_nl_to_generate_sql_chain(self):
        """Simulate a 2-node chain: parse_nl → generate_sql."""
        from app.nodes.parse_nl import ParseNLNode

        # Step 1: parse NL
        parse_node = ParseNLNode()
        parse_input = await parse_node.setup_input(
            {"query_text": "统计上个月的订单数", "context": {}, "config": {}}
        )
        parse_output = await parse_node.execute(parse_input)
        shared = await parse_node.update_context(parse_output, {})

        # Step 2: generate SQL
        gen_node = GenerateSQLNode()
        gen_input = await gen_node.setup_input(
            {"query_text": "统计上个月的订单数", "context": shared, "config": {}}
        )
        gen_output = await gen_node.execute(gen_input)
        shared = await gen_node.update_context(gen_output, shared)

        assert gen_output.metadata["status"] == "success"
        assert shared["primary_sql"] is not None
        assert "sqr" in shared
        assert "candidates" in shared
