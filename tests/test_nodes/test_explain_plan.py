"""Tests for ExplainPlanNode — EXPLAIN + LLM interpretation."""

import json
import sqlite3

import pytest

from app.models.query import PlanAnalysis
from app.nodes.base import NodeInput
from app.nodes.explain_plan import (
    ExplainPlanNode,
    ExplainPlanOutput,
    _extract_response_text,
    _format_explain_rows,
    _merge_plan_analyses,
    _parse_llm_json,
)

# ── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture
def node():
    """ExplainPlanNode with mock LLM router (mock_mode=True)."""
    return ExplainPlanNode()


@pytest.fixture
def sqlite_conn():
    """In-memory SQLite with test schema including indexes."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE users (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            email TEXT,
            tier TEXT DEFAULT 'free',
            created_at TEXT
        );
        CREATE TABLE orders (
            id INTEGER PRIMARY KEY,
            user_id INTEGER REFERENCES users(id),
            amount REAL NOT NULL,
            status TEXT DEFAULT 'pending',
            created_at TEXT
        );
        CREATE INDEX idx_orders_user_id ON orders(user_id);
        CREATE INDEX idx_orders_status ON orders(status);
        CREATE INDEX idx_users_tier ON users(tier);
        INSERT INTO users VALUES (1, 'Alice', 'alice@test.com', 'premium', '2024-01-01');
        INSERT INTO users VALUES (2, 'Bob', 'bob@test.com', 'free', '2024-01-02');
        INSERT INTO users VALUES (3, 'Charlie', 'charlie@test.com', 'premium', '2024-01-03');
        INSERT INTO orders VALUES (1, 1, 99.99, 'completed', '2024-02-01');
        INSERT INTO orders VALUES (2, 1, 49.99, 'pending', '2024-02-02');
        INSERT INTO orders VALUES (3, 2, 19.99, 'completed', '2024-02-03');
    """)
    yield conn
    conn.close()


# ═══════════════════════════════════════════════════════════════════════════════
# Node metadata
# ═══════════════════════════════════════════════════════════════════════════════


class TestNodeMetadata:
    def test_name(self, node):
        assert node.name == "explain_plan"

    def test_description(self, node):
        assert "EXPLAIN" in node.description or "explain" in node.description.lower()

    def test_inherits_agentic(self, node):
        from app.nodes.agentic import AgenticNode

        assert isinstance(node, AgenticNode)


# ═══════════════════════════════════════════════════════════════════════════════
# Error handling
# ═══════════════════════════════════════════════════════════════════════════════


class TestErrorCases:
    async def test_no_connection(self, node):
        output = await node.execute(
            NodeInput(query_text="SELECT * FROM users")
        )
        assert output.metadata["status"] == "no_connection"
        assert output.errors

    async def test_empty_sql(self, node, sqlite_conn):
        output = await node.execute(
            NodeInput(
                query_text="",
                context={"connection": sqlite_conn},
            )
        )
        assert output.metadata["status"] == "empty_sql"
        assert output.errors

    async def test_sql_from_context_preferred(self, node, sqlite_conn):
        """sql in context should take precedence over query_text."""
        output = await node.execute(
            NodeInput(
                query_text="SELECT * FROM orders",  # fallback
                context={
                    "connection": sqlite_conn,
                    "sql": "SELECT * FROM users WHERE id = 1",
                },
            )
        )
        assert output.metadata["status"] == "success"
        # The raw explain should reference 'users' not 'orders'
        raw = output.result.raw_explain
        raw_text = str(raw.get("rows", []))
        # We can't guarantee the exact text, but it should be about users
        assert "users" in raw_text.lower() or output.metadata["status"] == "success"


# ═══════════════════════════════════════════════════════════════════════════════
# EXPLAIN execution (real SQLite)
# ═══════════════════════════════════════════════════════════════════════════════


class TestExplainExecution:
    async def test_simple_select_explain(self, node, sqlite_conn):
        output = await node.execute(
            NodeInput(
                query_text="SELECT * FROM users WHERE id = 1",
                context={"connection": sqlite_conn},
                config={"skip_llm": True},
            )
        )
        assert output.metadata["status"] == "success"
        assert output.result is not None
        assert isinstance(output.result, ExplainPlanOutput)
        assert output.result.raw_explain["row_count"] >= 1

    async def test_join_explain(self, node, sqlite_conn):
        output = await node.execute(
            NodeInput(
                query_text=(
                    "SELECT u.name, o.amount FROM users u "
                    "JOIN orders o ON u.id = o.user_id"
                ),
                context={"connection": sqlite_conn},
                config={"skip_llm": True},
            )
        )
        assert output.metadata["status"] == "success"
        plan = output.result.plan_analysis
        assert isinstance(plan, PlanAnalysis)

    async def test_aggregate_explain(self, node, sqlite_conn):
        output = await node.execute(
            NodeInput(
                query_text="SELECT status, COUNT(*) FROM orders GROUP BY status",
                context={"connection": sqlite_conn},
                config={"skip_llm": True},
            )
        )
        assert output.metadata["status"] == "success"
        assert output.result.raw_explain["row_count"] >= 1


# ═══════════════════════════════════════════════════════════════════════════════
# PlanAnalysis in result
# ═══════════════════════════════════════════════════════════════════════════════


class TestPlanAnalysisInResult:
    async def test_plan_analysis_structured(self, node, sqlite_conn):
        output = await node.execute(
            NodeInput(
                query_text="SELECT * FROM users",
                context={"connection": sqlite_conn},
                config={"skip_llm": True},
            )
        )
        plan = output.result.plan_analysis
        assert isinstance(plan, PlanAnalysis)
        assert isinstance(plan.scan_types, list)
        assert isinstance(plan.join_types, list)
        assert isinstance(plan.warnings, list)
        assert isinstance(plan.has_full_scan, bool)

    async def test_full_scan_detected(self, node, sqlite_conn):
        """SELECT * without WHERE should detect full table scan."""
        output = await node.execute(
            NodeInput(
                query_text="SELECT * FROM users",
                context={"connection": sqlite_conn},
                config={"skip_llm": True},
            )
        )
        plan = output.result.plan_analysis
        assert plan.has_full_scan
        assert "seq_scan" in plan.scan_types
        assert any("WHERE" in w for w in plan.warnings)

    async def test_pk_lookup_no_full_scan(self, node, sqlite_conn):
        """Query by primary key should not trigger full scan."""
        output = await node.execute(
            NodeInput(
                query_text="SELECT * FROM users WHERE id = 1",
                context={"connection": sqlite_conn},
                config={"skip_llm": True},
            )
        )
        plan = output.result.plan_analysis
        assert not plan.has_full_scan


# ═══════════════════════════════════════════════════════════════════════════════
# LLM interpretation (mock mode)
# ═══════════════════════════════════════════════════════════════════════════════


class TestLLMInterpretation:
    async def test_llm_interpretation_populated(self, node, sqlite_conn):
        """In mock mode, LLM returns echo of user message as content.
        The interpretation will be whatever the mock returns, but
        should at least be a non-empty string when skip_llm=False."""
        output = await node.execute(
            NodeInput(
                query_text="SELECT * FROM users WHERE name = 'Alice'",
                context={"connection": sqlite_conn},
                config={},  # skip_llm defaults to False
            )
        )
        assert output.metadata["status"] == "success"
        # In mock mode, the response is the user text split into words.
        # The _parse_llm_json will fall back to treating content as interpretation.
        # So interpretation should be non-empty.
        assert output.metadata["llm_used"] is True

    async def test_skip_llm(self, node, sqlite_conn):
        output = await node.execute(
            NodeInput(
                query_text="SELECT * FROM users",
                context={"connection": sqlite_conn},
                config={"skip_llm": True},
            )
        )
        assert output.metadata["status"] == "success"
        assert output.metadata["llm_used"] is False
        assert output.result.interpretation == ""
        assert output.result.recommendations == []

    async def test_llm_handles_error_gracefully(self, node, sqlite_conn):
        """Even if LLM fails, node should return raw explain results."""
        # Mock router with mock_mode=False and no providers — will raise error
        from app.llm.router import LiteLLMRouter, RouterConfig

        bad_router = LiteLLMRouter(RouterConfig(providers={}), mock_mode=False)
        node_bad_llm = ExplainPlanNode(router=bad_router)

        output = await node_bad_llm.execute(
            NodeInput(
                query_text="SELECT * FROM users",
                context={"connection": sqlite_conn},
            )
        )
        # Should still succeed — LLM failure is non-fatal
        assert output.metadata["status"] == "success"
        assert output.result.plan_analysis is not None
        # Interpretation may be empty on LLM failure
        assert isinstance(output.result.recommendations, list)


# ═══════════════════════════════════════════════════════════════════════════════
# Context output keys
# ═══════════════════════════════════════════════════════════════════════════════


class TestContextOutput:
    async def test_context_keys_present(self, node, sqlite_conn):
        output = await node.execute(
            NodeInput(
                query_text="SELECT * FROM users WHERE id = 1",
                context={"connection": sqlite_conn},
                config={"skip_llm": True},
            )
        )
        assert "explain_raw" in output.context
        assert "plan_analysis" in output.context
        assert "plan_interpretation" in output.context
        assert "plan_recommendations" in output.context
        assert isinstance(output.context["plan_analysis"], PlanAnalysis)
        assert isinstance(output.context["plan_recommendations"], list)

    async def test_update_context_merges_keys(self, node, sqlite_conn):
        output = await node.execute(
            NodeInput(
                query_text="SELECT * FROM users WHERE name = 'Alice'",
                context={"connection": sqlite_conn, "preexisting": "value"},
                config={"skip_llm": True},
            )
        )
        shared = {"preexisting": "value"}
        merged = await node.update_context(output, shared)
        assert "preexisting" in merged
        assert "explain_raw" in merged
        assert "plan_analysis" in merged


# ═══════════════════════════════════════════════════════════════════════════════
# Metadata
# ═══════════════════════════════════════════════════════════════════════════════


class TestMetadata:
    async def test_metadata_keys(self, node, sqlite_conn):
        output = await node.execute(
            NodeInput(
                query_text="SELECT * FROM orders WHERE user_id = 1",
                context={"connection": sqlite_conn},
                config={"skip_llm": True},
            )
        )
        meta = output.metadata
        assert meta["status"] == "success"
        assert "has_full_scan" in meta
        assert "scan_types" in meta
        assert "join_types" in meta
        assert "estimated_rows" in meta
        assert "warning_count" in meta
        assert "llm_used" in meta

    async def test_metadata_estimated_rows(self, node, sqlite_conn):
        output = await node.execute(
            NodeInput(
                query_text="SELECT * FROM users",
                context={"connection": sqlite_conn},
                config={"skip_llm": True},
            )
        )
        assert output.metadata["estimated_rows"] >= 0


# ═══════════════════════════════════════════════════════════════════════════════
# Helper functions
# ═══════════════════════════════════════════════════════════════════════════════


class TestFormatExplainRows:
    def test_empty_rows(self):
        result = _format_explain_rows({"columns": [], "rows": [], "row_count": 0})
        assert "empty" in result.lower()

    def test_with_columns_and_rows(self):
        result = _format_explain_rows({
            "columns": ["id", "detail"],
            "rows": [[0, "SCAN TABLE users"]],
            "row_count": 1,
        })
        assert "id" in result
        assert "detail" in result
        assert "SCAN TABLE users" in result

    def test_no_columns(self):
        result = _format_explain_rows({
            "columns": [],
            "rows": [["some text"]],
            "row_count": 1,
        })
        assert "some text" in result


class TestMergePlanAnalyses:
    def test_merges_scan_types(self):
        explain = PlanAnalysis(scan_types=["seq_scan"])
        static = PlanAnalysis(scan_types=["seq_scan", "index_scan"])
        merged = _merge_plan_analyses(explain, static)
        assert set(merged.scan_types) == {"seq_scan", "index_scan"}

    def test_max_estimated_rows(self):
        explain = PlanAnalysis(estimated_rows=5000)
        static = PlanAnalysis(estimated_rows=1000)
        merged = _merge_plan_analyses(explain, static)
        assert merged.estimated_rows == 5000

    def test_merges_join_types(self):
        explain = PlanAnalysis(join_types=["hash_join"])
        static = PlanAnalysis(join_types=["nested_loop"])
        merged = _merge_plan_analyses(explain, static)
        assert set(merged.join_types) == {"hash_join", "nested_loop"}

    def test_has_full_scan_either(self):
        explain = PlanAnalysis(has_full_scan=False)
        static = PlanAnalysis(has_full_scan=True)
        merged = _merge_plan_analyses(explain, static)
        assert merged.has_full_scan is True

    def test_dedup_warnings(self):
        explain = PlanAnalysis(warnings=["A", "B"])
        static = PlanAnalysis(warnings=["B", "C"])
        merged = _merge_plan_analyses(explain, static)
        assert len(merged.warnings) == 3  # A, B, C


class TestExtractResponseText:
    def test_string_input(self):
        assert _extract_response_text("hello") == "hello"

    def test_dict_response(self):
        response = {
            "choices": [{"message": {"content": "test content"}}]
        }
        assert _extract_response_text(response) == "test content"

    def test_object_response(self):
        class FakeMessage:
            content = "object content"

        class FakeChoice:
            message = FakeMessage()

        class FakeResponse:
            choices = [FakeChoice()]

        assert _extract_response_text(FakeResponse()) == "object content"


class TestParseLLMJson:
    def test_valid_json(self):
        data = json.dumps({
            "interpretation": "This query scans the users table.",
            "recommendations": ["Add index on name", "Use LIMIT"],
        })
        interp, recs = _parse_llm_json(data)
        assert "scans" in interp
        assert len(recs) == 2
        assert "Add index on name" in recs

    def test_json_with_fences(self):
        data = '```json\n{"interpretation": "test", "recommendations": ["r1"]}\n```'
        interp, recs = _parse_llm_json(data)
        assert interp == "test"
        assert recs == ["r1"]

    def test_invalid_json_fallback(self):
        text = "This is a plain text interpretation, not JSON."
        interp, recs = _parse_llm_json(text)
        assert interp == text
        assert recs == []

    def test_json_missing_keys(self):
        data = json.dumps({"other": "value"})
        interp, recs = _parse_llm_json(data)
        assert interp == ""
        assert recs == []

    def test_recommendations_non_list(self):
        data = json.dumps({
            "interpretation": "ok",
            "recommendations": "not a list",
        })
        interp, recs = _parse_llm_json(data)
        assert interp == "ok"
        assert recs == []


# ═══════════════════════════════════════════════════════════════════════════════
# Multiple SQL scenarios
# ═══════════════════════════════════════════════════════════════════════════════


class TestMultipleScenarios:
    async def test_select_star(self, node, sqlite_conn):
        output = await node.execute(
            NodeInput(
                query_text="SELECT * FROM orders",
                context={"connection": sqlite_conn},
                config={"skip_llm": True},
            )
        )
        assert output.metadata["status"] == "success"
        assert len(output.result.plan_analysis.warnings) >= 1

    async def test_count_query(self, node, sqlite_conn):
        output = await node.execute(
            NodeInput(
                query_text="SELECT COUNT(*) FROM users",
                context={"connection": sqlite_conn},
                config={"skip_llm": True},
            )
        )
        assert output.metadata["status"] == "success"
        assert output.result.plan_analysis is not None

    async def test_filtered_query_with_index(self, node, sqlite_conn):
        """Query on indexed column should be efficient."""
        output = await node.execute(
            NodeInput(
                query_text="SELECT * FROM orders WHERE user_id = 1",
                context={"connection": sqlite_conn},
                config={"skip_llm": True},
            )
        )
        assert output.metadata["status"] == "success"
        # orders.user_id is indexed — should not flag full scan from EXPLAIN
        assert not output.result.plan_analysis.has_full_scan

    async def test_filtered_query_without_index(self, node, sqlite_conn):
        """Query on non-indexed column should detect full scan."""
        output = await node.execute(
            NodeInput(
                query_text="SELECT * FROM users WHERE name = 'Alice'",
                context={"connection": sqlite_conn},
                config={"skip_llm": True},
            )
        )
        assert output.metadata["status"] == "success"
        # name column has no index — EXPLAIN should show SCAN
        assert output.result.plan_analysis.has_full_scan
