"""End-to-end tests — full NL→SQL pipeline with real SQLite.

See SPEC §5.14 (Phase 5: End-to-end tests).
"""

from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_pipeline_end_to_end():
    """Verify the full pipeline runs end-to-end through WorkflowRunner."""
    from app.harness.runner import WorkflowRunner
    from app.models.workflow import NodeDef, WorkflowPlan
    from app.nodes.registry import node_registry

    plan = WorkflowPlan(
        id="e2e_test",
        name="E2E Test Pipeline",
        node_order=[
            NodeDef(id="step1", node="parse_nl"),
            NodeDef(id="step2", node="schema_linking"),
        ],
    )

    runner = WorkflowRunner(plan=plan, registry=node_registry)
    result = await runner.run("How many orders are there?", session_id="e2e_test")
    assert result is not None


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
    assert output.result is not None


@pytest.mark.asyncio
async def test_validate_sql_syntax():
    """Verify ValidateSQLNode runs without crashing on bad SQL."""
    from app.nodes.base import NodeInput
    from app.nodes.validate_sql import ValidateSQLNode

    node = ValidateSQLNode()
    node_input = NodeInput(
        query_text="test",
        context={"sql": "SELECT * FORM orders"},  # intentional typo
    )

    output = await node.execute(node_input)
    assert output is not None


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
    assert output is not None
