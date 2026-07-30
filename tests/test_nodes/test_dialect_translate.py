"""Tests for DialectTranslateNode — PlainNode wrapping DialectAdapter."""

import pytest

from app.nodes.base import NodeInput
from app.nodes.dialect_translate import (
    DialectTranslateNode,
    DialectTranslateOutput,
    _auto_detect_dialect,
    _normalize_dialect,
    _to_sqlglot_dialect,
)


@pytest.fixture
def node():
    return DialectTranslateNode()


# ═══════════════════════════════════════════════════════════════════════════════
# Node metadata
# ═══════════════════════════════════════════════════════════════════════════════


class TestNodeMetadata:
    def test_name(self, node):
        assert node.name == "dialect_translate"

    def test_description(self, node):
        assert "dialect" in node.description.lower()

    def test_is_plain_node_not_agentic(self, node):
        """DialectTranslateNode is a PlainNode — not AgenticNode."""
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
        output = await node.execute(
            NodeInput(
                query_text="",
                config={"target_dialect": "mysql"},
            )
        )
        assert output.metadata["status"] == "empty_sql"
        assert output.errors

    async def test_no_target_dialect(self, node):
        output = await node.execute(
            NodeInput(
                query_text="SELECT * FROM users",
            )
        )
        assert output.metadata["status"] == "skipped"
        assert "no target" in output.metadata["reason"]

    async def test_invalid_source_dialect(self, node):
        output = await node.execute(
            NodeInput(
                query_text="SELECT * FROM users",
                config={
                    "source_dialect": "nonexistent_db",
                    "target_dialect": "mysql",
                },
            )
        )
        assert output.metadata["status"] == "invalid_dialect"
        assert output.errors


# ═══════════════════════════════════════════════════════════════════════════════
# Translation — same dialect (no-op)
# ═══════════════════════════════════════════════════════════════════════════════


class TestSameDialectNoOp:
    async def test_postgres_to_postgres(self, node):
        output = await node.execute(
            NodeInput(
                query_text="SELECT * FROM users",
                config={
                    "source_dialect": "postgresql",
                    "target_dialect": "postgresql",
                },
            )
        )
        assert output.metadata["status"] == "skipped"
        assert output.result.translated is False
        assert output.result.translated_sql == output.result.original_sql

    async def test_sqlglot_name_also_skipped(self, node):
        """Using sqlglot dialect name 'postgres' should also be recognized."""
        output = await node.execute(
            NodeInput(
                query_text="SELECT * FROM users",
                config={
                    "source_dialect": "postgres",
                    "target_dialect": "postgresql",
                },
            )
        )
        assert output.metadata["status"] == "skipped"


# ═══════════════════════════════════════════════════════════════════════════════
# Translation — different dialects
# ═══════════════════════════════════════════════════════════════════════════════


class TestTranslateDifferentDialects:
    async def test_postgres_to_mysql(self, node):
        """PostgreSQL ILIKE → MySQL LIKE."""
        output = await node.execute(
            NodeInput(
                query_text="SELECT * FROM users WHERE name ILIKE '%john%'",
                config={
                    "source_dialect": "postgresql",
                    "target_dialect": "mysql",
                },
            )
        )
        assert output.metadata["status"] == "success"
        assert output.result.translated is True
        # ILIKE should be converted to LIKE (or similar)
        translated = output.result.translated_sql.upper()
        assert "ILIKE" not in translated

    async def test_postgres_to_sqlite(self, node):
        """PostgreSQL NOW() → SQLite datetime('now')."""
        output = await node.execute(
            NodeInput(
                query_text="SELECT NOW()",
                config={
                    "source_dialect": "postgresql",
                    "target_dialect": "sqlite",
                },
            )
        )
        assert output.metadata["status"] == "success"
        assert output.result.translated is True

    async def test_mysql_to_postgres(self, node):
        """MySQL backtick → PostgreSQL double-quote."""
        output = await node.execute(
            NodeInput(
                query_text="SELECT `users`.`name` FROM `users`",
                config={
                    "source_dialect": "mysql",
                    "target_dialect": "postgresql",
                },
            )
        )
        assert output.metadata["status"] == "success"
        translated = output.result.translated_sql
        # Backticks should be removed or converted
        assert "`" not in translated

    async def test_sqlite_to_postgres(self, node):
        """SQLite strftime → PostgreSQL DATE_TRUNC."""
        output = await node.execute(
            NodeInput(
                query_text="SELECT strftime('%Y', created_at) FROM users",
                config={
                    "source_dialect": "sqlite",
                    "target_dialect": "postgresql",
                },
            )
        )
        assert output.metadata["status"] == "success"
        assert output.result.translated is True

    async def test_sql_from_context_preferred(self, node):
        """context['sql'] should take precedence over query_text."""
        output = await node.execute(
            NodeInput(
                query_text="SELECT * FROM products",
                context={"sql": "SELECT * FROM users"},
                config={
                    "source_dialect": "postgresql",
                    "target_dialect": "mysql",
                },
            )
        )
        assert output.metadata["status"] == "success"
        assert "users" in output.result.original_sql
        assert "products" not in output.result.original_sql


# ═══════════════════════════════════════════════════════════════════════════════
# Translation — auto-detect source
# ═══════════════════════════════════════════════════════════════════════════════


class TestAutoDetectSource:
    async def test_auto_detect_postgres_style(self, node):
        output = await node.execute(
            NodeInput(
                query_text="SELECT * FROM users WHERE name ILIKE '%john%'",
                config={
                    "source_dialect": "auto",
                    "target_dialect": "mysql",
                },
            )
        )
        assert output.metadata["status"] in ("success", "skipped")

    async def test_auto_is_default(self, node):
        """When source_dialect is omitted, auto-detect is used."""
        output = await node.execute(
            NodeInput(
                query_text="SELECT * FROM users WHERE name ILIKE '%test%'",
                config={"target_dialect": "sqlite"},
            )
        )
        assert output.metadata["status"] in ("success", "skipped")


# ═══════════════════════════════════════════════════════════════════════════════
# Context output
# ═══════════════════════════════════════════════════════════════════════════════


class TestContextOutput:
    async def test_context_keys_present(self, node):
        output = await node.execute(
            NodeInput(
                query_text="SELECT * FROM users",
                config={
                    "source_dialect": "postgresql",
                    "target_dialect": "sqlite",
                },
            )
        )
        assert "sql" in output.context
        assert "original_sql" in output.context
        assert "source_dialect" in output.context
        assert "target_dialect" in output.context
        assert "translated" in output.context

    async def test_sql_overwritten_with_translated(self, node):
        output = await node.execute(
            NodeInput(
                query_text="SELECT NOW()",
                config={
                    "source_dialect": "postgresql",
                    "target_dialect": "sqlite",
                },
            )
        )
        if output.metadata["status"] == "success" and output.result.translated:
            assert output.context["sql"] == output.result.translated_sql
            assert output.context["sql"] != output.context["original_sql"]

    async def test_update_context_merges_keys(self, node):
        output = await node.execute(
            NodeInput(
                query_text="SELECT * FROM users",
                config={
                    "source_dialect": "postgresql",
                    "target_dialect": "sqlite",
                },
            )
        )
        shared = {"preexisting": "value"}
        merged = await node.update_context(output, shared)
        assert "preexisting" in merged
        assert "sql" in merged
        assert "original_sql" in merged


# ═══════════════════════════════════════════════════════════════════════════════
# All supported dialect pairs
# ═══════════════════════════════════════════════════════════════════════════════


class TestAllDialectPairs:
    """Smoke-test translation between all fully-supported dialects."""

    FULLY_SUPPORTED = ["postgresql", "mysql", "sqlite", "duckdb"]

    @pytest.mark.parametrize("source", FULLY_SUPPORTED)
    @pytest.mark.parametrize("target", FULLY_SUPPORTED)
    async def test_pair(self, node, source, target):
        output = await node.execute(
            NodeInput(
                query_text="SELECT * FROM users WHERE id = 1",
                config={"source_dialect": source, "target_dialect": target},
            )
        )
        # Should never error for valid dialect pairs
        if source == target:
            assert output.metadata["status"] == "skipped"
        else:
            assert output.metadata["status"] in ("success", "skipped")
            assert output.result is not None
            assert isinstance(output.result, DialectTranslateOutput)

    async def test_non_fully_supported_dialect(self, node):
        """Even stub dialects should work (they have dialect names)."""
        output = await node.execute(
            NodeInput(
                query_text="SELECT * FROM users",
                config={"source_dialect": "snowflake", "target_dialect": "postgresql"},
            )
        )
        assert output.metadata["status"] in ("success", "skipped")
        assert output.result is not None


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
                ),
                config={
                    "source_dialect": "postgresql",
                    "target_dialect": "mysql",
                },
            )
        )
        assert output.metadata["status"] == "success"

    async def test_cte_query(self, node):
        output = await node.execute(
            NodeInput(
                query_text=(
                    "WITH active_users AS ("
                    "  SELECT id, name FROM users WHERE status = 'active'"
                    ") "
                    "SELECT COUNT(*) FROM active_users"
                ),
                config={
                    "source_dialect": "postgresql",
                    "target_dialect": "sqlite",
                },
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
                ),
                config={
                    "source_dialect": "postgresql",
                    "target_dialect": "mysql",
                },
            )
        )
        assert output.metadata["status"] == "success"


# ═══════════════════════════════════════════════════════════════════════════════
# Helper functions
# ═══════════════════════════════════════════════════════════════════════════════


class TestAutoDetectDialect:
    def test_postgres_style_ilike(self):
        result = _auto_detect_dialect("SELECT * FROM users WHERE name ILIKE '%john%'")
        assert result == "postgresql"

    def test_postgres_style_cast(self):
        result = _auto_detect_dialect("SELECT 1::text")
        assert result == "postgresql"

    def test_mysql_backtick(self):
        result = _auto_detect_dialect("SELECT `users`.`name` FROM `users`")
        assert result == "mysql"

    def test_sqlite_functions(self):
        result = _auto_detect_dialect("SELECT strftime('%Y', created_at) FROM users")
        assert result == "sqlite"

    def test_default_is_postgresql(self):
        result = _auto_detect_dialect("SELECT * FROM users")
        assert result == "postgresql"


class TestNormalizeDialect:
    def test_postgres_to_postgresql(self):
        assert _normalize_dialect("postgres") == "postgresql"

    def test_postgresql_unchanged(self):
        assert _normalize_dialect("postgresql") == "postgresql"

    def test_mixed_case(self):
        assert _normalize_dialect("PostgreSQL") == "postgresql"

    def test_unknown_passthrough(self):
        assert _normalize_dialect("custom_db") == "custom_db"


class TestToSqlglotDialect:
    def test_postgresql_to_postgres(self):
        assert _to_sqlglot_dialect("postgresql") == "postgres"

    def test_mysql_unchanged(self):
        assert _to_sqlglot_dialect("mysql") == "mysql"

    def test_unknown_passthrough(self):
        assert _to_sqlglot_dialect("custom_sql") == "custom_sql"
