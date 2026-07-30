"""Tests for ExecuteSQLNode EXPLAIN integration and PlanAnalysis."""

import sqlite3

import pytest

from app.models.query import PlanAnalysis
from app.nodes.base import NodeInput
from app.nodes.execute_sql import SAFE_KEYWORDS, ExecuteSQLNode

# ── Fixtures ────────────────────────────────────────────────────────────


@pytest.fixture
def node():
    return ExecuteSQLNode()


@pytest.fixture
def sqlite_conn():
    """In-memory SQLite with test schema."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE users (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            email TEXT,
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
        INSERT INTO users VALUES (1, 'Alice', 'alice@test.com', '2024-01-01');
        INSERT INTO users VALUES (2, 'Bob', 'bob@test.com', '2024-01-02');
        INSERT INTO orders VALUES (1, 1, 99.99, 'completed', '2024-02-01');
        INSERT INTO orders VALUES (2, 1, 49.99, 'pending', '2024-02-02');
    """)
    yield conn
    conn.close()


# ═══════════════════════════════════════════════════════════════════════════
# SAFE_KEYWORDS
# ═══════════════════════════════════════════════════════════════════════════


class TestSafeKeywords:
    def test_explain_is_safe(self):
        assert "EXPLAIN" in SAFE_KEYWORDS

    def test_explain_passes_read_only_check(self):
        assert not ExecuteSQLNode._is_write_statement("EXPLAIN SELECT * FROM users")
        assert not ExecuteSQLNode._is_write_statement("EXPLAIN QUERY PLAN SELECT * FROM users")


# ═══════════════════════════════════════════════════════════════════════════
# Static Plan Analysis
# ═══════════════════════════════════════════════════════════════════════════


class TestStaticPlanAnalysis:
    def test_no_where_detected(self):
        plan = ExecuteSQLNode._static_plan_analysis("SELECT * FROM users")
        assert plan.has_full_scan
        assert "seq_scan" in plan.scan_types
        assert any("WHERE" in w for w in plan.warnings)

    def test_with_where_no_full_scan(self):
        plan = ExecuteSQLNode._static_plan_analysis(
            "SELECT * FROM users WHERE id = 1"
        )
        assert not plan.has_full_scan

    def test_select_star_warning(self):
        plan = ExecuteSQLNode._static_plan_analysis(
            "SELECT * FROM users WHERE id = 1"
        )
        assert any("SELECT *" in w for w in plan.warnings)

    def test_select_columns_no_star_warning(self):
        plan = ExecuteSQLNode._static_plan_analysis(
            "SELECT id, name FROM users"
        )
        assert not any("SELECT *" in w for w in plan.warnings)

    def test_function_wrapped_column_warning(self):
        plan = ExecuteSQLNode._static_plan_analysis(
            "SELECT * FROM users WHERE UPPER(name) = 'ALICE'"
        )
        assert any("Function-wrapped" in w for w in plan.warnings)

    def test_without_from_no_full_scan(self):
        """SELECT without FROM (e.g., SELECT 1) should not flag full scan."""
        plan = ExecuteSQLNode._static_plan_analysis("SELECT 1")
        assert not plan.has_full_scan


# ═══════════════════════════════════════════════════════════════════════════
# EXPLAIN execution — SQLite
# ═══════════════════════════════════════════════════════════════════════════


class TestExplainSqlite:
    def test_explain_executes(self, node, sqlite_conn):
        result = node._execute_explain(
            sqlite_conn, "SELECT * FROM users WHERE id = 1"
        )
        assert result["row_count"] >= 1
        assert len(result["rows"]) >= 1

    def test_explain_parse_scan_table(self, node, sqlite_conn):
        result = node._execute_explain(
            sqlite_conn, "SELECT * FROM users WHERE name = 'Alice'"
        )
        plan = node._parse_explain_output(result)
        # Without index on name, SQLite should do SCAN TABLE users
        assert plan.has_full_scan or "seq_scan" in plan.scan_types

    def test_explain_parse_uses_index(self, node, sqlite_conn):
        """Query by primary key should use index."""
        result = node._execute_explain(
            sqlite_conn, "SELECT * FROM users WHERE id = 1"
        )
        plan = node._parse_explain_output(result)
        # Should use the primary key index
        has_index = (
            "index_scan" in plan.scan_types
            or "index_only_scan" in plan.scan_types
        )
        assert has_index or not plan.has_full_scan

    def test_explain_join(self, node, sqlite_conn):
        result = node._execute_explain(
            sqlite_conn,
            "SELECT u.name, o.amount FROM users u "
            "JOIN orders o ON u.id = o.user_id"
        )
        plan = node._parse_explain_output(result)
        assert isinstance(plan, PlanAnalysis)
        assert isinstance(plan.scan_types, list)
        assert isinstance(plan.join_types, list)


# ═══════════════════════════════════════════════════════════════════════════
# EXPLAIN output parsing
# ═══════════════════════════════════════════════════════════════════════════


class TestParseExplainOutput:
    def test_parses_sqlite_scan(self, node):
        """SQLite EXPLAIN QUERY PLAN format."""
        result = {
            "columns": ["id", "parent", "notused", "detail"],
            "rows": [
                [2, 0, 0, "SCAN TABLE users"],
            ],
            "row_count": 1,
            "truncated": False,
        }
        plan = node._parse_explain_output(result)
        assert plan.has_full_scan
        assert "seq_scan" in plan.scan_types
        assert any("Full table scan" in w for w in plan.warnings)

    def test_parses_sqlite_index_scan(self, node):
        result = {
            "columns": ["id", "parent", "notused", "detail"],
            "rows": [
                [2, 0, 0, "SEARCH TABLE users USING INTEGER PRIMARY KEY (rowid=?)"],
            ],
            "row_count": 1,
            "truncated": False,
        }
        plan = node._parse_explain_output(result)
        assert not plan.has_full_scan

    def test_parses_postgres_explain(self, node):
        """Simulate PostgreSQL EXPLAIN output."""
        result = {
            "columns": ["QUERY PLAN"],
            "rows": [
                ["Seq Scan on users  (cost=0.00..35.50 rows=2550 width=40)"],
            ],
            "row_count": 1,
            "truncated": False,
        }
        plan = node._parse_explain_output(result)
        assert plan.has_full_scan
        assert "seq_scan" in plan.scan_types
        assert plan.estimated_rows == 2550

    def test_parses_hash_join(self, node):
        result = {
            "columns": ["QUERY PLAN"],
            "rows": [
                ["Hash Join  (cost=1.09..37.16 rows=2550 width=80)"],
                ["  Hash Cond: (o.user_id = u.id)"],
                ["  ->  Seq Scan on orders o"],
                ["  ->  Hash"],
                ["        ->  Seq Scan on users u"],
            ],
            "row_count": 5,
            "truncated": False,
        }
        plan = node._parse_explain_output(result)
        assert "hash_join" in plan.join_types
        assert plan.has_full_scan


# ═══════════════════════════════════════════════════════════════════════════
# Node execute() with EXPLAIN config
# ═══════════════════════════════════════════════════════════════════════════


class TestExecuteWithExplain:
    async def test_execute_without_explain(self, node, sqlite_conn):
        output = await node.execute(
            NodeInput(
                query_text="SELECT * FROM users",
                context={"connection": sqlite_conn},
            )
        )
        assert output.metadata["status"] == "success"
        assert output.metadata["row_count"] >= 2

    async def test_execute_with_explain(self, node, sqlite_conn):
        output = await node.execute(
            NodeInput(
                query_text="SELECT * FROM users WHERE name = 'Alice'",
                context={"connection": sqlite_conn},
                config={"explain": True},
            )
        )
        assert output.metadata["status"] == "success"
        assert "plan" in output.metadata
        assert isinstance(output.metadata["plan"], PlanAnalysis)

    async def test_execute_with_analyze_plan(self, node, sqlite_conn):
        output = await node.execute(
            NodeInput(
                query_text="SELECT * FROM orders",
                context={"connection": sqlite_conn},
                config={"analyze_plan": True},
            )
        )
        assert output.metadata["status"] == "success"
        plan = output.metadata.get("plan")
        assert plan is not None
        assert plan.has_full_scan  # no WHERE

    async def test_plan_in_context(self, node, sqlite_conn):
        output = await node.execute(
            NodeInput(
                query_text="SELECT * FROM users WHERE id = 1",
                context={"connection": sqlite_conn},
                config={"analyze_plan": True},
            )
        )
        assert "plan_analysis" in output.context

    async def test_explain_and_analyze_both(self, node, sqlite_conn):
        """Both explain=True and analyze_plan=True — should merge."""
        output = await node.execute(
            NodeInput(
                query_text="SELECT * FROM orders WHERE user_id = 1",
                context={"connection": sqlite_conn},
                config={"explain": True, "analyze_plan": True},
            )
        )
        assert output.metadata["status"] == "success"
        plan = output.metadata.get("plan")
        assert plan is not None

    async def test_explain_no_connection(self, node):
        output = await node.execute(
            NodeInput(
                query_text="SELECT 1",
                config={"analyze_plan": True},
            )
        )
        assert output.metadata["status"] == "no_connection"
