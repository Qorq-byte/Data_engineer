"""Tests for ReflectionNode — execution analysis and revision decision engine.

Covers error classification (syntax/schema/type/execution/timeout/empty_result),
context merge, registry integration, edge cases, and WorkflowRunner integration.
"""

from __future__ import annotations

import pytest

from app.nodes.base import NodeInput, NodeOutput
from app.nodes.reflection import (
    ErrorCategory,
    ReflectionNode,
    ReflectionOutput,
    _build_fix_hints,
    _build_suggested_approach,
    _classify_error,
)
from app.nodes.registry import NodeRegistry

# ── Helpers ────────────────────────────────────────────────────────────


def _make_node(auto_revise: bool = True) -> ReflectionNode:
    return ReflectionNode()


def _make_input(
    query_text: str = "SELECT * FROM users",
    sql: str = "SELECT * FROM users",
    last_execution: dict | None = None,
    *,
    auto_revise: bool = True,
    ctx_errors: list | None = None,
) -> NodeInput:
    context: dict = {
        "query_text": query_text,
        "sql": sql,
        "last_execution": last_execution,
    }
    if ctx_errors is not None:
        context["errors"] = ctx_errors
    return NodeInput(
        query_text=query_text,
        config={"auto_revise": auto_revise},
        context=context,
    )


def _successful_exec() -> dict:
    return {
        "columns": ["id", "name"],
        "rows": [[1, "Alice"], [2, "Bob"]],
        "row_count": 2,
        "truncated": False,
        "status": "success",
    }


def _empty_exec() -> dict:
    return {
        "columns": ["id", "name"],
        "rows": [],
        "row_count": 0,
        "truncated": False,
        "status": "success",
    }


def _error_exec(error_msg: str) -> dict:
    return {
        "columns": [],
        "rows": [],
        "row_count": 0,
        "truncated": False,
        "status": "execution_error",
        "error": error_msg,
    }


# ── Error classification tests ────────────────────────────────────────


class TestClassifyError:
    """Unit tests for the keyword-based error classifier."""

    # Syntax errors
    def test_syntax_error_keyword(self):
        cat, strat = _classify_error("syntax error near SELECT")
        assert cat == ErrorCategory.SYNTAX
        assert strat == "fix_syntax"

    def test_parse_error(self):
        cat, strat = _classify_error("Parse error: unexpected token")
        assert cat == ErrorCategory.SYNTAX

    def test_sqlite_operational_error(self):
        cat, strat = _classify_error('sqlite3.OperationalError: near "FROM"')
        assert cat == ErrorCategory.SYNTAX

    def test_syntax_error_lowercase(self):
        cat, strat = _classify_error("SQLITE3.OPERATIONALERROR: near")
        assert cat == ErrorCategory.SYNTAX

    def test_missing_keyword(self):
        cat, strat = _classify_error("Missing keyword in statement")
        assert cat == ErrorCategory.SYNTAX

    def test_malformed_sql(self):
        cat, strat = _classify_error("Malformed SQL statement")
        assert cat == ErrorCategory.SYNTAX

    # Schema errors
    def test_no_such_table(self):
        cat, strat = _classify_error("no such table: orders")
        assert cat == ErrorCategory.SCHEMA
        assert strat == "fix_schema"

    def test_no_such_column(self):
        cat, strat = _classify_error("no such column: users.email")
        assert cat == ErrorCategory.SCHEMA

    def test_column_not_found(self):
        cat, strat = _classify_error("column 'status' not found")
        assert cat == ErrorCategory.SCHEMA

    def test_table_not_found(self):
        cat, strat = _classify_error("table 'products' not found")
        assert cat == ErrorCategory.SCHEMA

    def test_relation_does_not_exist(self):
        cat, strat = _classify_error("relation 'public.orders' does not exist")
        assert cat == ErrorCategory.SCHEMA

    def test_ambiguous_column(self):
        cat, strat = _classify_error("ambiguous column name: id")
        assert cat == ErrorCategory.SCHEMA

    def test_column_reference_ambiguous(self):
        cat, strat = _classify_error("column reference 'id' is ambiguous")
        assert cat == ErrorCategory.SCHEMA

    # Type errors
    def test_type_error(self):
        cat, strat = _classify_error("type mismatch in comparison")
        assert cat == ErrorCategory.TYPE

    def test_cast_error(self):
        cat, strat = _classify_error("cannot cast integer to text")
        assert cat == ErrorCategory.TYPE

    def test_incompatible_types(self):
        cat, strat = _classify_error("incompatible types in WHERE clause")
        assert cat == ErrorCategory.TYPE

    def test_cannot_compare(self):
        cat, strat = _classify_error("cannot compare integer and text")
        assert cat == ErrorCategory.TYPE

    def test_datatype_mismatch(self):
        cat, strat = _classify_error("datatype mismatch: expected numeric")
        assert cat == ErrorCategory.TYPE

    # Timeout
    def test_timeout(self):
        cat, strat = _classify_error("query timeout after 30000ms")
        assert cat == ErrorCategory.TIMEOUT

    def test_timed_out(self):
        cat, strat = _classify_error("statement timed out")
        assert cat == ErrorCategory.TIMEOUT

    # Execution errors
    def test_execution_error(self):
        cat, strat = _classify_error("SQL execution error: division by zero")
        assert cat == ErrorCategory.EXECUTION

    def test_division_by_zero(self):
        cat, strat = _classify_error("division by zero")
        assert cat == ErrorCategory.EXECUTION

    def test_null_value_error(self):
        cat, strat = _classify_error("null value in column")
        assert cat == ErrorCategory.EXECUTION

    def test_constraint_violation(self):
        cat, strat = _classify_error("constraint violation")
        assert cat == ErrorCategory.EXECUTION

    def test_out_of_range(self):
        cat, strat = _classify_error("value out of range")
        assert cat == ErrorCategory.EXECUTION

    def test_overflow(self):
        cat, strat = _classify_error("integer overflow")
        assert cat == ErrorCategory.EXECUTION

    def test_permission_denied(self):
        cat, strat = _classify_error("permission denied for table users")
        assert cat == ErrorCategory.EXECUTION

    def test_access_denied(self):
        cat, strat = _classify_error("access denied")
        assert cat == ErrorCategory.EXECUTION

    def test_write_blocked(self):
        cat, strat = _classify_error("Write operation blocked")
        assert cat == ErrorCategory.EXECUTION

    def test_read_only_blocked(self):
        cat, strat = _classify_error("read_only mode is enabled")
        assert cat == ErrorCategory.EXECUTION

    # Unknown errors
    def test_unknown_error(self):
        cat, strat = _classify_error("something went terribly wrong")
        assert cat == "unknown"
        assert strat == "rewrite"

    # Case insensitivity
    def test_case_insensitive_match(self):
        cat, strat = _classify_error("SYNTAX ERROR near SELECT")
        assert cat == ErrorCategory.SYNTAX

    def test_mixed_case_match(self):
        cat, strat = _classify_error("No SuCh TaBlE: users")
        assert cat == ErrorCategory.SCHEMA


# ── Fix hints tests ────────────────────────────────────────────────────


class TestBuildFixHints:
    def test_syntax_hints(self):
        hints = _build_fix_hints(ErrorCategory.SYNTAX, [])
        assert len(hints) >= 3
        assert any("syntax" in h.lower() for h in hints)
        assert any("identifier" in h.lower() for h in hints)

    def test_schema_hints(self):
        hints = _build_fix_hints(ErrorCategory.SCHEMA, [])
        assert len(hints) >= 3
        assert any("table" in h.lower() for h in hints)
        assert any("column" in h.lower() for h in hints)

    def test_type_hints(self):
        hints = _build_fix_hints(ErrorCategory.TYPE, [])
        assert len(hints) >= 3
        assert any("cast" in h.lower() for h in hints)

    def test_timeout_hints(self):
        hints = _build_fix_hints(ErrorCategory.TIMEOUT, [])
        assert len(hints) >= 3
        assert any("where" in h.lower() for h in hints)
        assert any("limit" in h.lower() for h in hints)

    def test_empty_result_hints(self):
        hints = _build_fix_hints(ErrorCategory.EMPTY_RESULT, [])
        assert len(hints) >= 2
        assert any("filter" in h.lower() for h in hints)

    def test_execution_hints(self):
        hints = _build_fix_hints(ErrorCategory.EXECUTION, [])
        assert len(hints) >= 3
        assert any("null" in h.lower() or "coalesce" in h.lower() for h in hints)

    def test_unknown_hints(self):
        hints = _build_fix_hints("unknown", [])
        assert len(hints) >= 1
        assert any("review" in h.lower() for h in hints)


# ── Suggested approach tests ───────────────────────────────────────────


class TestBuildSuggestedApproach:
    def test_syntax_approach(self):
        approach = _build_suggested_approach(ErrorCategory.SYNTAX, "fix_syntax", [])
        assert "syntax" in approach.lower()

    def test_schema_approach(self):
        approach = _build_suggested_approach(ErrorCategory.SCHEMA, "fix_schema", [])
        assert "table" in approach.lower() or "column" in approach.lower()

    def test_type_approach(self):
        approach = _build_suggested_approach(ErrorCategory.TYPE, "add_cast", [])
        assert "cast" in approach.lower()

    def test_timeout_approach(self):
        approach = _build_suggested_approach(ErrorCategory.TIMEOUT, "optimize", [])
        assert "optimize" in approach.lower() or "limit" in approach.lower()

    def test_empty_result_approach(self):
        approach = _build_suggested_approach(ErrorCategory.EMPTY_RESULT, "broaden_filters", [])
        assert "filter" in approach.lower() or "broaden" in approach.lower()

    def test_execution_approach(self):
        approach = _build_suggested_approach(ErrorCategory.EXECUTION, "rewrite", [])
        assert (
            "runtime" in approach.lower()
            or "null" in approach.lower()
            or "edge" in approach.lower()
        )

    def test_unknown_approach(self):
        approach = _build_suggested_approach("unknown", "rewrite", [])
        assert "rewrite" in approach.lower()


# ── ReflectionNode execute tests ───────────────────────────────────────


class TestReflectionNodeExecute:
    """Test the main execute() method across all scenarios."""

    @pytest.fixture
    def node(self) -> ReflectionNode:
        return _make_node()

    # ── Successful execution ───────────────────────────────────────

    @pytest.mark.asyncio
    async def test_successful_execution(self, node: ReflectionNode):
        inp = _make_input(last_execution=_successful_exec())
        output = await node.execute(inp)

        assert isinstance(output.result, ReflectionOutput)
        assert output.result.execution_success is True
        assert output.result.needs_revision is False
        assert output.result.error_category == ErrorCategory.NONE
        assert output.metadata["needs_revision"] is False
        assert output.metadata["status"] == "success"

    @pytest.mark.asyncio
    async def test_successful_context_keys(self, node: ReflectionNode):
        inp = _make_input(last_execution=_successful_exec())
        output = await node.execute(inp)

        assert output.context["needs_revision"] is False
        assert output.context["reflection"] is output.result
        assert output.context["fix_hints"] == []

    @pytest.mark.asyncio
    async def test_success_with_truncated_results(self, node: ReflectionNode):
        exec_result = {
            "columns": ["id"],
            "rows": [[1]],
            "row_count": 100,
            "truncated": True,
            "status": "success",
        }
        inp = _make_input(last_execution=exec_result)
        output = await node.execute(inp)

        assert output.result.execution_success is True
        assert output.result.truncated is True
        assert output.result.needs_revision is False

    # ── Empty result ───────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_empty_result_needs_revision(self, node: ReflectionNode):
        inp = _make_input(last_execution=_empty_exec())
        output = await node.execute(inp)

        assert output.result.error_category == ErrorCategory.EMPTY_RESULT
        assert output.result.needs_revision is True
        assert output.result.fix_strategy == "broaden_filters"
        assert output.metadata["needs_revision"] is True
        assert output.metadata["status"] == "empty_result"

    @pytest.mark.asyncio
    async def test_empty_result_auto_revise_disabled(self):
        node = _make_node()
        inp = _make_input(last_execution=_empty_exec(), auto_revise=False)
        output = await node.execute(inp)

        assert output.result.error_category == ErrorCategory.EMPTY_RESULT
        assert output.result.needs_revision is False
        assert output.context["needs_revision"] is False

    @pytest.mark.asyncio
    async def test_empty_result_context_keys(self, node: ReflectionNode):
        inp = _make_input(last_execution=_empty_exec())
        output = await node.execute(inp)

        assert len(output.context["fix_hints"]) >= 2
        assert "filter" in output.context["error_analysis"].lower()
        assert output.context["needs_revision"] is True

    # ── Execution error — syntax ───────────────────────────────────

    @pytest.mark.asyncio
    async def test_syntax_error(self, node: ReflectionNode):
        inp = _make_input(last_execution=_error_exec("syntax error near SELECT"))
        output = await node.execute(inp)

        assert output.result.error_category == ErrorCategory.SYNTAX
        assert output.result.needs_revision is True
        assert output.result.fix_strategy == "fix_syntax"

    @pytest.mark.asyncio
    async def test_syntax_error_context(self, node: ReflectionNode):
        inp = _make_input(last_execution=_error_exec("syntax error near FROM"))
        output = await node.execute(inp)

        assert "syntax" in output.context["error_analysis"].lower()
        hints = output.context["fix_hints"]
        assert any("syntax" in h.lower() for h in hints)

    # ── Execution error — schema ───────────────────────────────────

    @pytest.mark.asyncio
    async def test_schema_error(self, node: ReflectionNode):
        inp = _make_input(last_execution=_error_exec("no such table: orders"))
        output = await node.execute(inp)

        assert output.result.error_category == ErrorCategory.SCHEMA
        assert output.result.needs_revision is True
        assert output.result.fix_strategy == "fix_schema"

    @pytest.mark.asyncio
    async def test_schema_error_column(self, node: ReflectionNode):
        inp = _make_input(last_execution=_error_exec("no such column: users.email_addr"))
        output = await node.execute(inp)

        assert output.result.error_category == ErrorCategory.SCHEMA

    # ── Execution error — type ─────────────────────────────────────

    @pytest.mark.asyncio
    async def test_type_error(self, node: ReflectionNode):
        inp = _make_input(last_execution=_error_exec("type mismatch: integer vs text"))
        output = await node.execute(inp)

        assert output.result.error_category == ErrorCategory.TYPE
        assert output.result.fix_strategy == "add_cast"

    # ── Execution error — timeout ──────────────────────────────────

    @pytest.mark.asyncio
    async def test_timeout_error(self, node: ReflectionNode):
        inp = _make_input(last_execution=_error_exec("query timeout after 30000ms"))
        output = await node.execute(inp)

        assert output.result.error_category == ErrorCategory.TIMEOUT
        assert output.result.fix_strategy == "optimize"

    # ── Execution error — execution ────────────────────────────────

    @pytest.mark.asyncio
    async def test_execution_error_division_by_zero(self, node: ReflectionNode):
        inp = _make_input(last_execution=_error_exec("division by zero"))
        output = await node.execute(inp)

        assert output.result.error_category == ErrorCategory.EXECUTION
        assert output.result.needs_revision is True

    @pytest.mark.asyncio
    async def test_auto_revise_disabled_on_error(self):
        node = _make_node()
        inp = _make_input(
            last_execution=_error_exec("no such table: orders"),
            auto_revise=False,
        )
        output = await node.execute(inp)

        assert output.result.needs_revision is False
        assert output.context["needs_revision"] is False

    # ── Context errors (from validate_sql, etc.) ────────────────────

    @pytest.mark.asyncio
    async def test_context_errors_list(self, node: ReflectionNode):
        inp = _make_input(
            last_execution=_successful_exec(),
            ctx_errors=["SCHEMA_ERROR: Table 'orders' not found"],
        )
        output = await node.execute(inp)

        assert output.result.error_category == ErrorCategory.SCHEMA
        assert output.result.needs_revision is True

    @pytest.mark.asyncio
    async def test_context_errors_string(self, node: ReflectionNode):
        inp = _make_input(
            last_execution=_successful_exec(),
            ctx_errors="SYNTAX_ERROR: unexpected token",
        )
        output = await node.execute(inp)

        assert output.result.error_category == ErrorCategory.SYNTAX

    @pytest.mark.asyncio
    async def test_context_errors_and_execution_error_combined(self, node: ReflectionNode):
        inp = _make_input(
            last_execution=_error_exec("no such table: orders"),
            ctx_errors=["TYPE_WARNING: comparing int and text"],
        )
        output = await node.execute(inp)

        # First known error should be used for classification
        assert output.result.error_category in (ErrorCategory.TYPE, ErrorCategory.SCHEMA)
        assert len(output.result.error_details) >= 2

    # ── No execution result ────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_no_execution_result(self, node: ReflectionNode):
        inp = _make_input(last_execution=None)
        output = await node.execute(inp)

        assert output.result.needs_revision is True
        assert output.result.error_category == "unknown"
        assert output.metadata["status"] == "no_execution"
        assert "no execution result" in output.result.error_summary.lower()

    @pytest.mark.asyncio
    async def test_no_execution_result_hints(self, node: ReflectionNode):
        inp = _make_input(last_execution=None)
        output = await node.execute(inp)

        assert len(output.context["fix_hints"]) >= 2
        assert output.context["needs_revision"] is True

    # ── Error priority: first known error wins ─────────────────────

    @pytest.mark.asyncio
    async def test_multiple_errors_first_known_wins(self, node: ReflectionNode):
        """When there are multiple errors, the first known category is used."""
        inp = _make_input(
            last_execution=_error_exec(""),
            ctx_errors=[
                "something weird",
                "no such table: products",
                "syntax error near SELECT",
            ],
        )
        # First error is "unknown", second is "schema" → schema wins
        output = await node.execute(inp)
        assert output.result.error_category == ErrorCategory.SCHEMA

    @pytest.mark.asyncio
    async def test_all_unknown_errors(self, node: ReflectionNode):
        inp = _make_input(
            last_execution=_error_exec(""),
            ctx_errors=["something weird", "another odd thing"],
        )
        output = await node.execute(inp)
        assert output.result.error_category == "unknown"
        assert output.result.needs_revision is True

    # ── Execution result with error field ──────────────────────────

    @pytest.mark.asyncio
    async def test_execution_error_field(self, node: ReflectionNode):
        """The error field in last_execution is also classified."""
        inp = _make_input(
            last_execution={
                "columns": [],
                "rows": [],
                "row_count": 0,
                "status": "execution_error",
                "error": "division by zero in aggregate",
            },
        )
        output = await node.execute(inp)
        assert output.result.error_category == ErrorCategory.EXECUTION

    @pytest.mark.asyncio
    async def test_blocked_status(self, node: ReflectionNode):
        inp = _make_input(
            last_execution={
                "columns": [],
                "rows": [],
                "row_count": 0,
                "status": "blocked",
                "error": "Write operation blocked",
            },
        )
        output = await node.execute(inp)
        assert output.result.error_category == ErrorCategory.EXECUTION
        assert output.result.needs_revision is True

    # ── Metadata completeness ──────────────────────────────────────

    @pytest.mark.asyncio
    async def test_metadata_contains_all_keys_on_error(self, node: ReflectionNode):
        inp = _make_input(last_execution=_error_exec("syntax error"))
        output = await node.execute(inp)

        assert "status" in output.metadata
        assert "needs_revision" in output.metadata
        assert "error_category" in output.metadata
        assert "error_count" in output.metadata
        assert "fix_strategy" in output.metadata

    @pytest.mark.asyncio
    async def test_result_is_reflection_output(self, node: ReflectionNode):
        inp = _make_input(last_execution=_successful_exec())
        output = await node.execute(inp)

        assert isinstance(output.result, ReflectionOutput)
        assert output.result.sql == "SELECT * FROM users"
        assert output.result.original_query == "SELECT * FROM users"

    # ── Row count tracking ─────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_row_count_preserved(self, node: ReflectionNode):
        inp = _make_input(last_execution=_successful_exec())
        output = await node.execute(inp)

        assert output.result.row_count == 2
        assert output.result.has_results is True
        assert output.result.truncated is False

    @pytest.mark.asyncio
    async def test_has_results_false_for_zero_rows(self, node: ReflectionNode):
        inp = _make_input(last_execution=_empty_exec())
        output = await node.execute(inp)

        assert output.result.row_count == 0
        assert output.result.has_results is False


# ── update_context tests ───────────────────────────────────────────────


class TestReflectionNodeUpdateContext:
    @pytest.fixture
    def node(self) -> ReflectionNode:
        return _make_node()

    @pytest.mark.asyncio
    async def test_merge_preserves_original_context(self, node: ReflectionNode):
        shared = {"query_text": "hello", "custom_key": "value"}
        output = NodeOutput(
            result=None,
            context={"needs_revision": True, "fix_hints": ["hint1"]},
        )
        merged = await node.update_context(output, shared)

        assert merged["query_text"] == "hello"
        assert merged["custom_key"] == "value"
        assert merged["needs_revision"] is True
        assert merged["fix_hints"] == ["hint1"]

    @pytest.mark.asyncio
    async def test_merge_adds_fix_hints_if_missing(self, node: ReflectionNode):
        shared: dict = {}
        output = NodeOutput(result=None, context={"needs_revision": False})
        merged = await node.update_context(output, shared)

        assert merged["fix_hints"] == []

    @pytest.mark.asyncio
    async def test_merge_does_not_overwrite_existing_fix_hints(self, node: ReflectionNode):
        shared = {"fix_hints": ["existing hint"]}
        output = NodeOutput(
            result=None, context={"needs_revision": True, "fix_hints": ["new hint"]}
        )
        merged = await node.update_context(output, shared)

        assert merged["fix_hints"] == ["new hint"]

    @pytest.mark.asyncio
    async def test_full_context_flow(self, node: ReflectionNode):
        """Simulate a full context flow through the reflection node."""
        inp = _make_input(last_execution=_error_exec("no such table: orders"))
        exec_output = await node.execute(inp)

        shared = {"query_text": "find orders", "sql": "SELECT * FROM orders"}
        merged = await node.update_context(exec_output, shared)

        assert merged["needs_revision"] is True
        assert "reflection" in merged
        assert isinstance(merged["reflection"], ReflectionOutput)
        assert len(merged["fix_hints"]) >= 3
        assert merged["error_analysis"]


# ── Node metadata tests ────────────────────────────────────────────────


class TestReflectionNodeMetadata:
    def test_name(self):
        node = _make_node()
        assert node.name == "reflection"

    def test_description(self):
        node = _make_node()
        assert "analyse" in node.description.lower() or "analyze" in node.description.lower()

    def test_not_agentic_node(self):
        node = _make_node()
        from app.nodes.agentic import AgenticNode
        assert not isinstance(node, AgenticNode)


# ── Registry integration tests ─────────────────────────────────────────


class TestReflectionNodeRegistry:
    def test_register_and_get(self):
        registry = NodeRegistry()
        registry.reset()
        node = _make_node()
        registry.register(node)

        retrieved = registry.get("reflection")
        assert retrieved is node
        assert retrieved.name == "reflection"

    def test_registry_list_includes_reflection(self):
        registry = NodeRegistry()
        registry.reset()
        registry.register(_make_node())

        assert "reflection" in registry.list_all()

    def test_duplicate_registration_raises(self):
        registry = NodeRegistry()
        registry.reset()
        registry.register(_make_node())

        with pytest.raises(ValueError, match="already registered"):
            registry.register(_make_node())

    def test_unregister(self):
        registry = NodeRegistry()
        registry.reset()
        registry.register(_make_node())
        registry.unregister("reflection")

        assert "reflection" not in registry.list_all()

    def test_contains_check(self):
        registry = NodeRegistry()
        registry.reset()
        registry.register(_make_node())

        assert "reflection" in registry
        assert "nonexistent" not in registry


# ── Edge cases ─────────────────────────────────────────────────────────


class TestReflectionNodeEdgeCases:
    @pytest.fixture
    def node(self) -> ReflectionNode:
        return _make_node()

    @pytest.mark.asyncio
    async def test_empty_sql_and_query(self, node: ReflectionNode):
        inp = _make_input(query_text="", sql="", last_execution=_successful_exec())
        output = await node.execute(inp)

        assert output.result.execution_success is True
        assert output.result.sql == ""
        assert output.result.original_query == ""

    @pytest.mark.asyncio
    async def test_query_text_falls_back_to_input(self, node: ReflectionNode):
        """When context query_text is empty, falls back to input.query_text."""
        context: dict = {
            "sql": "SELECT 1",
            "last_execution": _successful_exec(),
        }
        inp = NodeInput(query_text="fallback query", config={}, context=context)
        output = await node.execute(inp)

        assert output.result.original_query == "fallback query"

    @pytest.mark.asyncio
    async def test_non_dict_execution_result(self, node: ReflectionNode):
        """last_execution that is not a dict should be treated as no result."""
        inp = _make_input(last_execution="not a dict")  # type: ignore
        output = await node.execute(inp)

        # Non-dict is coerced to None → treated as "no execution result"
        assert output.result.needs_revision is True
        assert output.metadata["status"] == "no_execution"

    @pytest.mark.asyncio
    async def test_execution_with_status_error_no_error_message(self, node: ReflectionNode):
        inp = _make_input(
            last_execution={
                "columns": [],
                "rows": [],
                "row_count": 0,
                "status": "error",
            },
        )
        output = await node.execute(inp)

        assert output.result.needs_revision is True
        assert output.result.error_category != ErrorCategory.NONE

    @pytest.mark.asyncio
    async def test_large_row_count(self, node: ReflectionNode):
        exec_result = {
            "columns": ["id"],
            "rows": [[i] for i in range(1000)],
            "row_count": 5000,
            "truncated": True,
            "status": "success",
        }
        inp = _make_input(last_execution=exec_result)
        output = await node.execute(inp)

        assert output.result.row_count == 5000
        assert output.result.has_results is True
        assert output.result.truncated is True

    @pytest.mark.asyncio
    async def test_context_errors_empty_list(self, node: ReflectionNode):
        inp = _make_input(
            last_execution=_successful_exec(),
            ctx_errors=[],
        )
        output = await node.execute(inp)

        assert output.result.execution_success is True
        assert output.result.needs_revision is False

    @pytest.mark.asyncio
    async def test_reflection_output_str_representation(self, node: ReflectionNode):
        inp = _make_input(last_execution=_error_exec("syntax error"))
        output = await node.execute(inp)

        # ReflectionOutput should be repr-able
        r = repr(output.result)
        assert "ReflectionOutput" in r

    @pytest.mark.asyncio
    async def test_error_details_limited_in_summary(self, node: ReflectionNode):
        """Error summary should contain at most 3 error messages."""
        errors = [f"error_{i}" for i in range(10)]
        inp = _make_input(
            last_execution=_successful_exec(),
            ctx_errors=errors,
        )
        output = await node.execute(inp)

        # Summary uses "; ".join(errors[:3])
        summary = output.result.error_summary
        assert summary.count("error_") <= 3  # at most 3 in summary


# ── WorkflowRunner integration (simulated) ─────────────────────────────


class TestReflectionWorkflowRunnerIntegration:
    """Simulate how WorkflowRunner consumes the reflection output."""

    @pytest.fixture
    def node(self) -> ReflectionNode:
        return _make_node()

    @pytest.mark.asyncio
    async def test_needs_revision_triggers_revise_branch(self, node: ReflectionNode):
        """When needs_revision=True, WorkflowRunner should branch to 'revise'."""
        inp = _make_input(last_execution=_error_exec("no such table: orders"))
        output = await node.execute(inp)

        # The runner checks: if node_def.id == "reflect" and evaluation.needs_revision
        needs_revision = output.metadata.get("needs_revision", False)
        assert needs_revision is True, (
            "WorkflowRunner would NOT branch to revise — this is a bug"
        )

    @pytest.mark.asyncio
    async def test_passed_does_not_trigger_revise(self, node: ReflectionNode):
        """When execution succeeds, no revision branch is triggered."""
        inp = _make_input(last_execution=_successful_exec())
        output = await node.execute(inp)

        needs_revision = output.metadata.get("needs_revision", False)
        assert needs_revision is False

    @pytest.mark.asyncio
    async def test_reflection_output_available_for_revise_node(self, node: ReflectionNode):
        """The revise node (generate_sql in self_heal mode) needs error analysis."""
        inp = _make_input(last_execution=_error_exec("type mismatch"))
        output = await node.execute(inp)

        # These keys must be in context for the revise node
        assert "error_analysis" in output.context
        assert "fix_hints" in output.context
        assert "reflection" in output.context
        assert isinstance(output.context["reflection"], ReflectionOutput)

    @pytest.mark.asyncio
    async def test_fix_hints_actionable_for_llm(self, node: ReflectionNode):
        """Fix hints should be clear enough for the LLM to use in self_heal mode."""
        inp = _make_input(last_execution=_error_exec("no such column: users.email"))
        output = await node.execute(inp)

        hints = output.context["fix_hints"]
        assert len(hints) > 0
        # Each hint should be a non-empty string
        for hint in hints:
            assert isinstance(hint, str)
            assert len(hint) > 5
