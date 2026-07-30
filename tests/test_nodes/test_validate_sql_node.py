"""Tests for ValidateSQLNode — the 3-stage SQL validation node."""

import pytest

from app.models.schema import ColumnSchema, SchemaSnapshot, TableSchema
from app.nodes.base import NodeInput
from app.nodes.validate_sql import ValidateSQLNode

pytestmark = pytest.mark.anyio


# ── Fixtures ────────────────────────────────────────────────────────────


@pytest.fixture
def node():
    return ValidateSQLNode()


@pytest.fixture
def sample_schema():
    return SchemaSnapshot(
        database_type="postgresql",
        database_name="test",
        tables={
            "users": TableSchema(
                name="users",
                columns=[
                    ColumnSchema(name="id", type="INTEGER", is_primary_key=True),
                    ColumnSchema(name="name", type="VARCHAR(100)"),
                ],
            ),
        },
    )


# ═══════════════════════════════════════════════════════════════════════════
# Node metadata
# ═══════════════════════════════════════════════════════════════════════════


class TestNodeMetadata:
    def test_name(self, node):
        assert node.name == "validate_sql"

    def test_is_agentic_node(self, node):
        from app.nodes.agentic import AgenticNode
        assert isinstance(node, AgenticNode)

    def test_validator_created(self, node):
        assert node.validator is not None


# ═══════════════════════════════════════════════════════════════════════════
# execute — basic
# ═══════════════════════════════════════════════════════════════════════════


class TestExecuteBasic:
    async def test_valid_sql_passes(self, node, sample_schema):
        output = await node.execute(
            NodeInput(
                query_text="SELECT * FROM users",
                context={"primary_sql": "SELECT * FROM users", "schema": sample_schema},
            )
        )
        assert output.metadata["status"] == "success"
        assert output.result["passed"] is True
        assert output.result["score"] >= 0.9

    async def test_invalid_sql_fails(self, node):
        output = await node.execute(
            NodeInput(
                query_text="NOT A VALID SQL STATEMENT",
                context={"primary_sql": "NOT A VALID SQL STATEMENT"},
            )
        )
        assert output.result["passed"] is False
        assert output.result["score"] == 0.0

    async def test_missing_table_fails(self, node, sample_schema):
        output = await node.execute(
            NodeInput(
                query_text="SELECT * FROM ghosts",
                context={"primary_sql": "SELECT * FROM ghosts", "schema": sample_schema},
            )
        )
        assert output.result["passed"] is False

    async def test_fallback_to_query_text(self, node):
        """When no primary_sql in context, uses query_text."""
        output = await node.execute(
            NodeInput(query_text="SELECT 1")
        )
        assert output.metadata["status"] == "success"
        assert output.result["passed"] is True


# ═══════════════════════════════════════════════════════════════════════════
# execute — context output
# ═══════════════════════════════════════════════════════════════════════════


class TestContextOutput:
    async def test_validation_report_in_context(self, node, sample_schema):
        output = await node.execute(
            NodeInput(
                query_text="SELECT * FROM users",
                context={"primary_sql": "SELECT * FROM users", "schema": sample_schema},
            )
        )
        assert "validation_report" in output.context
        assert "validation_passed" in output.context
        assert output.context["validation_passed"] is True

    async def test_validation_report_type(self, node, sample_schema):
        from app.models.query import ValidationReport
        output = await node.execute(
            NodeInput(
                query_text="SELECT * FROM users",
                context={"primary_sql": "SELECT * FROM users", "schema": sample_schema},
            )
        )
        assert isinstance(output.context["validation_report"], ValidationReport)


# ═══════════════════════════════════════════════════════════════════════════
# execute — config options
# ═══════════════════════════════════════════════════════════════════════════


class TestExecuteWithConfig:
    async def test_skip_syntax(self, node):
        output = await node.execute(
            NodeInput(
                query_text="NOT VALID SQL",
                context={"primary_sql": "NOT VALID SQL"},
                config={"skip_syntax": True},
            )
        )
        assert output.result["syntax_ok"] is True
        assert output.result["passed"] is True

    async def test_custom_dialect(self, node, sample_schema):
        output = await node.execute(
            NodeInput(
                query_text="SELECT NOW()",
                context={"primary_sql": "SELECT NOW()", "schema": sample_schema},
                config={"dialect": "postgres"},
            )
        )
        assert output.metadata["status"] == "success"


# ═══════════════════════════════════════════════════════════════════════════
# execute — Step 4 (performance) and Step 5 (business rules)
# ═══════════════════════════════════════════════════════════════════════════


class TestPerformanceStep:
    async def test_perf_warnings_counted(self, node, sample_schema):
        """SELECT * without WHERE/LIMIT triggers performance warnings."""
        output = await node.execute(
            NodeInput(
                query_text="SELECT * FROM users",
                context={"primary_sql": "SELECT * FROM users", "schema": sample_schema},
            )
        )
        assert output.metadata["perf_warnings_count"] >= 2
        assert output.result["passed"] is True  # warnings never block

    async def test_skip_performance_config(self, node, sample_schema):
        output = await node.execute(
            NodeInput(
                query_text="SELECT * FROM users",
                context={"primary_sql": "SELECT * FROM users", "schema": sample_schema},
                config={"skip_performance": True},
            )
        )
        assert output.metadata["perf_warnings_count"] == 0
        assert output.result["score"] == 1.0

    async def test_perf_warnings_in_report_context(self, node, sample_schema):
        output = await node.execute(
            NodeInput(
                query_text="SELECT * FROM users",
                context={"primary_sql": "SELECT * FROM users", "schema": sample_schema},
            )
        )
        report = output.context["validation_report"]
        assert report.plan_analysis is not None
        assert any("PERF_WARNING" in w for w in report.warnings)


class TestBusinessRuleStep:
    @pytest.fixture
    def limit_rule(self):
        from app.models.domain import BusinessRule
        return BusinessRule(
            id="users_need_limit",
            description="User queries must be bounded",
            pattern=r"users",
            enforce=["require_limit=true"],
        )

    async def test_business_rules_from_context(self, node, sample_schema, limit_rule):
        output = await node.execute(
            NodeInput(
                query_text="SELECT name FROM users WHERE id = 1",
                context={
                    "primary_sql": "SELECT name FROM users WHERE id = 1",
                    "schema": sample_schema,
                    "business_rules": [limit_rule],
                },
            )
        )
        assert output.metadata["rule_warnings_count"] == 1
        assert output.result["business_valid"] is False
        assert output.result["passed"] is True  # warnings never block
        assert output.context["validation_passed"] is True

    async def test_rule_satisfied_no_warning(self, node, sample_schema, limit_rule):
        sql = "SELECT name FROM users WHERE id = 1 LIMIT 10"
        output = await node.execute(
            NodeInput(
                query_text=sql,
                context={
                    "primary_sql": sql,
                    "schema": sample_schema,
                    "business_rules": [limit_rule],
                },
            )
        )
        assert output.metadata["rule_warnings_count"] == 0
        assert output.result["business_valid"] is True

    async def test_skip_rules_config(self, node, sample_schema, limit_rule):
        output = await node.execute(
            NodeInput(
                query_text="SELECT name FROM users WHERE id = 1",
                context={
                    "primary_sql": "SELECT name FROM users WHERE id = 1",
                    "schema": sample_schema,
                    "business_rules": [limit_rule],
                },
                config={"skip_rules": True},
            )
        )
        assert output.metadata["rule_warnings_count"] == 0
        assert output.result["business_valid"] is True

    async def test_no_rules_in_context(self, node, sample_schema):
        """Missing business_rules key defaults to empty list — no warnings."""
        output = await node.execute(
            NodeInput(
                query_text="SELECT name FROM users WHERE id = 1 LIMIT 10",
                context={
                    "primary_sql": "SELECT name FROM users WHERE id = 1 LIMIT 10",
                    "schema": sample_schema,
                },
            )
        )
        assert output.metadata["rule_warnings_count"] == 0
        assert output.result["business_valid"] is True


# ═══════════════════════════════════════════════════════════════════════════
# WorkflowRunner compatibility
# ═══════════════════════════════════════════════════════════════════════════


class TestWorkflowChain:
    async def test_generate_to_validate_chain(self, sample_schema):
        """Simulate: generate_sql → validate_sql."""
        from app.models.query import SQR, IntentType
        from app.nodes.generate_sql import GenerateSQLNode

        # Step 1: generate SQL
        gen_node = GenerateSQLNode()
        sqr = SQR(raw_text="查询用户", language="zh", intent=IntentType.SELECT)
        gen_input = await gen_node.setup_input({
            "query_text": "查询用户",
            "context": {"sqr": sqr},
        })
        gen_output = await gen_node.execute(gen_input)
        shared = await gen_node.update_context(gen_output, {})

        # Step 2: validate the generated SQL
        val_node = ValidateSQLNode()
        val_input = await val_node.setup_input({
            "query_text": "查询用户",
            "context": {**shared, "schema": sample_schema},
        })
        val_output = await val_node.execute(val_input)
        shared = await val_node.update_context(val_output, shared)

        assert val_output.metadata["status"] == "success"
        assert "validation_report" in shared
        assert "primary_sql" in shared

    async def test_parse_generate_validate_chain(self, sample_schema):
        """Full 3-node chain: parse_nl → generate_sql → validate_sql."""
        from app.nodes.generate_sql import GenerateSQLNode
        from app.nodes.parse_nl import ParseNLNode

        # Step 1: Parse NL
        parse_node = ParseNLNode()
        parse_input = await parse_node.setup_input({
            "query_text": "查询所有用户", "context": {}, "config": {},
        })
        parse_output = await parse_node.execute(parse_input)
        shared = await parse_node.update_context(parse_output, {})

        # Step 2: Generate SQL
        gen_node = GenerateSQLNode()
        gen_input = await gen_node.setup_input({
            "query_text": "查询所有用户",
            "context": shared,
        })
        gen_output = await gen_node.execute(gen_input)
        shared = await gen_node.update_context(gen_output, shared)

        # Step 3: Validate SQL
        val_node = ValidateSQLNode()
        val_input = await val_node.setup_input({
            "query_text": "查询所有用户",
            "context": {**shared, "schema": sample_schema},
        })
        val_output = await val_node.execute(val_input)
        shared = await val_node.update_context(val_output, shared)

        assert val_output.metadata["status"] == "success"
        assert "sqr" in shared
        assert "primary_sql" in shared
        assert "validation_report" in shared
