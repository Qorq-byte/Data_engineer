"""End-to-end tests — full NL→SQL pipeline with real SQLite.

See SPEC §5.14 (Phase 5: End-to-end tests).
"""

from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_pipeline_end_to_end(sqlite_connection):
    """A successful five-node Runner must return the actual fixture answer."""
    from app.db.connections import DatabaseType
    from app.db.schema_extractor import SchemaExtractor
    from app.harness.plan_loader import PlanLoader
    from app.harness.runner import WorkflowRunner
    from app.llm.budget import BudgetedRouter
    from app.llm.router import LiteLLMRouter, RouterConfig
    from app.nodes.execute_sql import ExecuteSQLNode
    from app.nodes.generate_sql import GenerateSQLNode
    from app.nodes.parse_nl import ParseNLNode
    from app.nodes.registry import NodeRegistry
    from app.nodes.schema_linking import SchemaLinkingNode
    from app.nodes.validate_sql import ValidateSQLNode

    class ScriptedRouter(LiteLLMRouter):
        async def complete(self, messages, **kwargs):
            return {"choices": [{"message": {"content": "SELECT COUNT(*) FROM orders"}}]}

    registry = NodeRegistry()
    for node in [
        ParseNLNode(),
        SchemaLinkingNode(),
        GenerateSQLNode(router=BudgetedRouter(ScriptedRouter(RouterConfig(), mock_mode=True))),
        ValidateSQLNode(dialect="sqlite"),
        ExecuteSQLNode(),
    ]:
        registry.register(node)
    schema = await SchemaExtractor.extract(sqlite_connection, DatabaseType.SQLITE)
    plan = PlanLoader().load("request_query")
    runner = WorkflowRunner(plan, registry)
    result = await runner.run(
        "How many orders are there?",
        session_id="e2e_test",
        schema=schema,
        connection=sqlite_connection,
        require_validation=True,
        node_configs={
            "gen": {
                "num_candidates": 1,
                "permissions": {"build_prompt": "allow", "call_llm": "allow"},
            },
            "exec": {"permissions": {"execute_read_query": "allow"}},
        },
    )
    assert result.status == "completed", result.error
    assert result.final_output["rows"] == [[5]]
    assert result.context["validation_passed"] is True
    assert runner.constraints.llm_calls_used == 1
    assert [trace.node_name for trace in result.trace.nodes] == [
        "parse_nl",
        "schema_linking",
        "generate_sql",
        "validate_sql",
        "execute_sql",
    ]


@pytest.mark.asyncio
async def test_pipeline_with_sqlite_execution(sqlite_connection):
    """Verify ExecuteSQLNode runs successfully against the test schema."""
    from app.nodes.base import NodeInput
    from app.nodes.execute_sql import ExecuteSQLNode

    node = ExecuteSQLNode()
    node_input = NodeInput(
        query_text="SELECT COUNT(*) FROM orders",
        context={"connection": sqlite_connection, "sql": "SELECT COUNT(*) FROM orders"},
    )

    output = await node.execute(node_input)
    assert output.result["rows"] == [[5]]
    assert not output.errors


@pytest.mark.asyncio
async def test_validate_sql_syntax():
    """Verify ValidateSQLNode runs without crashing on bad SQL."""
    from app.nodes.base import NodeInput
    from app.nodes.validate_sql import ValidateSQLNode

    node = ValidateSQLNode()
    node_input = NodeInput(
        query_text="test",
        context={"sql": "SELECT * FROM"},  # incomplete syntax must fail
    )

    output = await node.execute(node_input)
    assert output.result["passed"] is False


@pytest.mark.asyncio
async def test_parse_nl_node():
    """Verify ParseNLNode processes natural language input."""
    from app.nodes.parse_nl import ParseNLNode

    node = ParseNLNode()
    raw_input = {
        "query_text": "查询上月订单总额",
        "domain": "default",
    }
    # setup_input may be async
    node_input = node.setup_input(raw_input)
    if hasattr(node_input, "__await__"):
        node_input = await node_input

    output = await node.execute(node_input)
    assert output.result.language == "zh"
    assert output.result.raw_text == "查询上月订单总额"
