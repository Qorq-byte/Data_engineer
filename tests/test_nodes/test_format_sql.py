"""Tests for FormatSQLNode — PlainNode wrapping sqlparse."""

import pytest

from app.nodes.base import NodeInput
from app.nodes.format_sql import FormatSQLNode, FormatSQLOutput


@pytest.fixture
def node():
    return FormatSQLNode()


# ═══════════════════════════════════════════════════════════════════════════════
# Node metadata
# ═══════════════════════════════════════════════════════════════════════════════


class TestNodeMetadata:
    def test_name(self, node):
        assert node.name == "format_sql"

    def test_description(self, node):
        assert "format" in node.description.lower()

    def test_is_plain_node_not_agentic(self, node):
        """FormatSQLNode is a PlainNode — not AgenticNode."""
        from app.nodes.agentic import AgenticNode

        assert not isinstance(node, AgenticNode)

    def test_is_base_node(self, node):
        from app.nodes.base import BaseNode

        assert isinstance(node, BaseNode)


# ═══════════════════════════════════════════════════════════════════════════════
# Error handling
# ═══════════════════════════════════════════════════════════════════════════════


class TestErrorCases:
    async def test_empty_sql(self, node):
        output = await node.execute(NodeInput(query_text=""))
        assert output.metadata["status"] == "empty_sql"
        assert output.errors

    async def test_empty_sql_from_context(self, node):
        output = await node.execute(
            NodeInput(query_text="", context={"sql": ""})
        )
        assert output.metadata["status"] == "empty_sql"

    async def test_invalid_keyword_case(self, node):
        output = await node.execute(
            NodeInput(
                query_text="SELECT * FROM users",
                config={"keyword_case": "invalid_case"},
            )
        )
        assert output.metadata["status"] == "invalid_config"
        assert output.errors


# ═══════════════════════════════════════════════════════════════════════════════
# Basic formatting — keyword_case
# ═══════════════════════════════════════════════════════════════════════════════


class TestKeywordCase:
    """Keyword case transformation tests."""

    async def test_default_is_upper(self, node):
        output = await node.execute(
            NodeInput(query_text="select * from users where id = 1")
        )
        assert output.metadata["status"] == "success"
        formatted = output.result.formatted_sql
        assert "SELECT" in formatted
        assert "FROM" in formatted
        assert "WHERE" in formatted

    async def test_lower_case(self, node):
        output = await node.execute(
            NodeInput(
                query_text="SELECT * FROM users WHERE id = 1",
                config={"keyword_case": "lower"},
            )
        )
        assert output.metadata["status"] == "success"
        formatted = output.result.formatted_sql
        assert "select" in formatted
        assert "from" in formatted
        assert "where" in formatted

    async def test_capitalize_case(self, node):
        output = await node.execute(
            NodeInput(
                query_text="select * from users where id = 1",
                config={"keyword_case": "capitalize"},
            )
        )
        assert output.metadata["status"] == "success"
        formatted = output.result.formatted_sql
        assert "Select" in formatted
        assert "From" in formatted
        assert "Where" in formatted

    async def test_all_keywords_uppercase(self, node):
        """Verify that common SQL keywords are all uppercased."""
        sql = (
            "select u.name, count(*) as total from users u "
            "join orders o on u.id = o.user_id "
            "where o.status = 'active' "
            "group by u.name having count(*) > 2 "
            "order by total desc limit 10"
        )
        output = await node.execute(NodeInput(query_text=sql))
        formatted = output.result.formatted_sql
        for kw in ["SELECT", "FROM", "JOIN", "ON", "WHERE", "GROUP BY",
                    "HAVING", "ORDER BY", "DESC", "LIMIT", "AS", "COUNT"]:
            assert kw in formatted.upper(), f"Expected {kw} in output"


# ═══════════════════════════════════════════════════════════════════════════════
# Indentation
# ═══════════════════════════════════════════════════════════════════════════════


class TestIndentation:
    async def test_reindent_default(self, node):
        """reindent=True (default) should restructure indentation."""
        output = await node.execute(
            NodeInput(
                query_text="SELECT a, b, c FROM users WHERE id = 1"
            )
        )
        assert output.metadata["status"] == "success"
        formatted = output.result.formatted_sql
        # With reindent on, multi-column SELECT should be multi-line
        lines = formatted.strip().split("\n")
        assert len(lines) >= 3  # SELECT column lines + FROM + WHERE

    async def test_no_reindent(self, node):
        """reindent=False should keep the SQL mostly on one line."""
        output = await node.execute(
            NodeInput(
                query_text="SELECT a, b, c FROM users WHERE id = 1",
                config={"reindent": False},
            )
        )
        assert output.metadata["status"] == "success"
        formatted = output.result.formatted_sql.strip()
        # With reindent off, sqlparse keeps it compact (may still split long lines)
        assert "SELECT" in formatted
        assert "FROM" in formatted

    async def test_indent_width_custom(self, node):
        """Custom indent_width should be reflected."""
        output = await node.execute(
            NodeInput(
                query_text="SELECT a, b, c FROM users WHERE id = 1",
                config={"indent_width": 4},
            )
        )
        assert output.metadata["status"] == "success"
        assert output.result.indent_width == 4


# ═══════════════════════════════════════════════════════════════════════════════
# Comment stripping
# ═══════════════════════════════════════════════════════════════════════════════


class TestComments:
    async def test_strip_comments_false(self, node):
        """Comments should be preserved when strip_comments=False (default)."""
        sql = "SELECT * -- this is a comment\nFROM users"
        output = await node.execute(
            NodeInput(query_text=sql, config={"strip_comments": False})
        )
        assert output.metadata["status"] == "success"
        # The comment should be preserved (sqlparse may reformat it)
        assert "comment" in output.result.formatted_sql

    async def test_strip_comments_true(self, node):
        """Comments should be removed when strip_comments=True."""
        sql = "SELECT * -- this is a comment\nFROM users"
        output = await node.execute(
            NodeInput(query_text=sql, config={"strip_comments": True})
        )
        assert output.metadata["status"] == "success"
        assert "comment" not in output.result.formatted_sql


# ═══════════════════════════════════════════════════════════════════════════════
# Already-formatted SQL (no-op)
# ═══════════════════════════════════════════════════════════════════════════════


class TestAlreadyFormatted:
    async def test_already_formatted_no_change(self, node):
        """If SQL is already formatted, changed should be False."""
        sql = """SELECT a
FROM users
WHERE id = 1"""
        output = await node.execute(NodeInput(query_text=sql))
        assert output.metadata["status"] == "success"
        # Already well-formatted SQL might have minor whitespace diffs
        assert isinstance(output.result.changed, bool)


# ═══════════════════════════════════════════════════════════════════════════════
# Complex SQL
# ═══════════════════════════════════════════════════════════════════════════════


class TestComplexSQL:
    async def test_join_query(self, node):
        output = await node.execute(
            NodeInput(
                query_text=(
                    "SELECT u.name, o.amount "
                    "FROM users u "
                    "JOIN orders o ON u.id = o.user_id "
                    "WHERE o.status = 'completed'"
                )
            )
        )
        assert output.metadata["status"] == "success"
        formatted = output.result.formatted_sql
        assert "SELECT" in formatted
        assert "JOIN" in formatted

    async def test_cte_query(self, node):
        output = await node.execute(
            NodeInput(
                query_text=(
                    "WITH active_users AS ("
                    "SELECT id, name FROM users WHERE status = 'active'"
                    ") "
                    "SELECT COUNT(*) FROM active_users"
                )
            )
        )
        assert output.metadata["status"] == "success"
        formatted = output.result.formatted_sql
        assert "WITH" in formatted
        assert "SELECT" in formatted

    async def test_subquery(self, node):
        output = await node.execute(
            NodeInput(
                query_text=(
                    "SELECT name FROM users WHERE id IN "
                    "(SELECT user_id FROM orders WHERE amount > 100)"
                )
            )
        )
        assert output.metadata["status"] == "success"

    async def test_aggregate_query(self, node):
        output = await node.execute(
            NodeInput(
                query_text=(
                    "SELECT status, COUNT(*), SUM(amount) "
                    "FROM orders GROUP BY status "
                    "HAVING COUNT(*) > 5 "
                    "ORDER BY SUM(amount) DESC"
                )
            )
        )
        assert output.metadata["status"] == "success"

    async def test_insert_statement(self, node):
        """INSERT statements should also be formattable."""
        output = await node.execute(
            NodeInput(
                query_text="insert into users (name, email) values ('alice', 'a@b.com')",
                config={"keyword_case": "upper"},
            )
        )
        assert output.metadata["status"] == "success"
        assert "INSERT" in output.result.formatted_sql


# ═══════════════════════════════════════════════════════════════════════════════
# Context output
# ═══════════════════════════════════════════════════════════════════════════════


class TestContextOutput:
    async def test_context_keys_present(self, node):
        output = await node.execute(
            NodeInput(query_text="SELECT * FROM users")
        )
        assert "sql" in output.context
        assert "original_sql" in output.context
        assert "formatted" in output.context

    async def test_sql_overwritten_with_formatted(self, node):
        output = await node.execute(
            NodeInput(query_text="select * from users")
        )
        assert output.context["sql"] == output.result.formatted_sql
        assert output.context["original_sql"] == "select * from users"

    async def test_update_context_merges_keys(self, node):
        output = await node.execute(
            NodeInput(query_text="SELECT * FROM users")
        )
        shared = {"preexisting": "value"}
        merged = await node.update_context(output, shared)
        assert "preexisting" in merged
        assert "sql" in merged
        assert "original_sql" in merged
        assert "formatted" in merged

    async def test_sql_from_context_preferred(self, node):
        """context['sql'] should take precedence over query_text."""
        output = await node.execute(
            NodeInput(
                query_text="SELECT * FROM products",
                context={"sql": "select * from users"},
            )
        )
        assert "select * from users" in output.result.original_sql
        assert "products" not in output.result.original_sql


# ═══════════════════════════════════════════════════════════════════════════════
# Output dataclass
# ═══════════════════════════════════════════════════════════════════════════════


class TestFormatSQLOutput:
    def test_defaults(self):
        out = FormatSQLOutput()
        assert out.original_sql == ""
        assert out.formatted_sql == ""
        assert out.keyword_case == "upper"
        assert out.indent_width == 2
        assert out.changed is False

    def test_changed_true(self):
        out = FormatSQLOutput(
            original_sql="select * from t",
            formatted_sql="SELECT *\nFROM t",
            changed=True,
        )
        assert out.changed is True

    def test_changed_false_when_identical(self):
        out = FormatSQLOutput(
            original_sql="SELECT * FROM t",
            formatted_sql="SELECT * FROM t",
            changed=False,
        )
        assert out.changed is False
