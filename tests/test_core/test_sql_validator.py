"""Tests for SQLValidator — 3-stage validation pipeline."""

import pytest

from app.core.sql_validator import SQLValidator
from app.models.schema import ColumnSchema, SchemaSnapshot, TableSchema

# ── Fixtures ────────────────────────────────────────────────────────────


@pytest.fixture
def validator():
    return SQLValidator()


@pytest.fixture
def sample_schema():
    return SchemaSnapshot(
        database_type="postgresql",
        database_name="ecommerce",
        tables={
            "users": TableSchema(
                name="users",
                columns=[
                    ColumnSchema(name="id", type="INTEGER", is_primary_key=True),
                    ColumnSchema(name="name", type="VARCHAR(100)"),
                    ColumnSchema(name="email", type="VARCHAR(200)"),
                    ColumnSchema(name="created_at", type="TIMESTAMP"),
                ],
            ),
            "orders": TableSchema(
                name="orders",
                columns=[
                    ColumnSchema(name="id", type="INTEGER", is_primary_key=True),
                    ColumnSchema(name="user_id", type="INTEGER"),
                    ColumnSchema(name="amount", type="DECIMAL(10,2)"),
                    ColumnSchema(name="status", type="VARCHAR(20)"),
                    ColumnSchema(name="created_at", type="TIMESTAMP"),
                ],
            ),
        },
    )


# ═══════════════════════════════════════════════════════════════════════════
# Step 1 — Syntax check
# ═══════════════════════════════════════════════════════════════════════════


class TestSyntaxCheck:
    async def test_valid_select(self, validator):
        report = await validator.validate("SELECT * FROM orders")
        assert report.syntax_ok
        assert len(report.syntax_errors) == 0

    async def test_valid_select_with_where(self, validator):
        report = await validator.validate(
            "SELECT id, name FROM users WHERE email LIKE '%@example.com'"
        )
        assert report.syntax_ok

    async def test_valid_cte(self, validator):
        report = await validator.validate("""
            WITH active AS (SELECT * FROM users WHERE id > 0)
            SELECT * FROM active
        """)
        assert report.syntax_ok

    async def test_valid_join(self, validator):
        report = await validator.validate(
            "SELECT u.name, o.amount FROM users u "
            "INNER JOIN orders o ON u.id = o.user_id"
        )
        assert report.syntax_ok

    async def test_empty_sql(self, validator):
        report = await validator.validate("")
        assert not report.syntax_ok
        assert len(report.syntax_errors) >= 1

    async def test_whitespace_sql(self, validator):
        report = await validator.validate("   ")
        assert not report.syntax_ok

    async def test_garbage_input(self, validator):
        report = await validator.validate("NOT A VALID SQL STATEMENT !!!")
        assert not report.syntax_ok

    async def test_dialect_specific(self):
        v = SQLValidator(dialect="postgres")
        report = await v.validate("SELECT NOW()")
        assert report.syntax_ok


# ═══════════════════════════════════════════════════════════════════════════
# Step 2 — Schema reference check
# ═══════════════════════════════════════════════════════════════════════════


class TestSchemaRefCheck:
    async def test_all_tables_found(self, validator, sample_schema):
        report = await validator.validate(
            "SELECT id, name FROM users", schema=sample_schema
        )
        assert report.schema_valid
        assert len(report.schema_errors) == 0

    async def test_table_not_found(self, validator, sample_schema):
        report = await validator.validate(
            "SELECT * FROM nonexistent_table", schema=sample_schema
        )
        assert not report.schema_valid
        assert any("nonexistent_table" in e for e in report.schema_errors)

    async def test_column_not_found(self, validator, sample_schema):
        report = await validator.validate(
            "SELECT nonexistent_col FROM users", schema=sample_schema
        )
        assert not report.schema_valid
        assert any("nonexistent_col" in e for e in report.schema_errors)

    async def test_qualified_column_found(self, validator, sample_schema):
        report = await validator.validate(
            "SELECT users.id, orders.amount FROM users "
            "INNER JOIN orders ON users.id = orders.user_id",
            schema=sample_schema,
        )
        assert report.schema_valid

    async def test_qualified_column_wrong_table(self, validator, sample_schema):
        report = await validator.validate(
            "SELECT users.nonexistent FROM users", schema=sample_schema
        )
        assert not report.schema_valid

    async def test_star_select_valid(self, validator, sample_schema):
        """SELECT * should not trigger column errors."""
        report = await validator.validate(
            "SELECT * FROM users", schema=sample_schema
        )
        assert report.schema_valid

    async def test_multiple_tables(self, validator, sample_schema):
        report = await validator.validate(
            "SELECT u.name, o.amount FROM users u JOIN orders o ON u.id = o.user_id",
            schema=sample_schema,
        )
        assert report.schema_valid

    async def test_no_schema_skips(self, validator):
        """When no schema is provided, Step 2 is skipped."""
        report = await validator.validate("SELECT * FROM anything")
        assert report.schema_valid  # default is True


# ═══════════════════════════════════════════════════════════════════════════
# Step 3 — Type compatibility check
# ═══════════════════════════════════════════════════════════════════════════


class TestTypeCheck:
    async def test_compatible_types(self, validator, sample_schema):
        """INTEGER = INTEGER should be compatible."""
        report = await validator.validate(
            "SELECT * FROM users WHERE id = 1", schema=sample_schema
        )
        assert report.type_valid

    async def test_incompatible_types_warned(self, validator, sample_schema):
        """INTEGER = VARCHAR should trigger a type warning."""
        report = await validator.validate(
            "SELECT * FROM users WHERE id = 'some_string'", schema=sample_schema
        )
        # Type warnings are non-blocking
        assert report.type_valid  # type warnings don't invalidate
        # But they should appear in warnings
        assert any("TYPE_WARNING" in w for w in report.warnings)

    async def test_numeric_comparison_compatible(self, validator, sample_schema):
        """INTEGER vs DECIMAL should be compatible (both numeric)."""
        report = await validator.validate(
            "SELECT * FROM orders WHERE id = amount", schema=sample_schema
        )
        assert report.type_valid

    async def test_join_types_compatible(self, validator, sample_schema):
        """INTEGER user_id = INTEGER users.id should be compatible."""
        report = await validator.validate(
            "SELECT * FROM users u JOIN orders o ON u.id = o.user_id",
            schema=sample_schema,
        )
        assert report.type_valid

    async def test_no_schema_skips_type_check(self, validator):
        report = await validator.validate("SELECT * FROM t WHERE a = b")
        assert report.type_valid


# ═══════════════════════════════════════════════════════════════════════════
# Full pipeline & scoring
# ═══════════════════════════════════════════════════════════════════════════


class TestFullPipeline:
    async def test_all_pass_high_score(self, validator, sample_schema):
        report = await validator.validate(
            "SELECT u.name, o.amount FROM users u "
            "INNER JOIN orders o ON u.id = o.user_id "
            "WHERE o.status = 'active'",
            schema=sample_schema,
        )
        assert report.passed
        assert report.score >= 0.9

    async def test_syntax_failure_zero_score(self, validator):
        report = await validator.validate("INVALID SQL !!!")
        assert not report.passed
        assert report.score == 0.0

    async def test_schema_failure_zero_score(self, validator, sample_schema):
        report = await validator.validate(
            "SELECT * FROM ghosts", schema=sample_schema
        )
        assert not report.passed
        assert report.score == 0.0

    async def test_warnings_reduce_score(self, validator, sample_schema):
        """Type warnings should reduce the score but not fail."""
        report = await validator.validate(
            "SELECT * FROM users WHERE id = 'text_value'",
            schema=sample_schema,
        )
        assert report.passed  # syntax + schema OK
        assert report.score < 1.0  # warning penalty

    async def test_skip_syntax(self, validator):
        report = await validator.validate(
            "NOT VALID SQL", skip_syntax=True
        )
        assert report.syntax_ok
        assert report.passed

    async def test_skip_schema(self, validator, sample_schema):
        report = await validator.validate(
            "SELECT * FROM ghosts WHERE poltergeist = 1",
            schema=sample_schema,
            skip_schema=True,
        )
        assert report.schema_valid


# ═══════════════════════════════════════════════════════════════════════════
# ValidationReport fields
# ═══════════════════════════════════════════════════════════════════════════


class TestValidationReportFields:
    async def test_all_fields_present(self, validator, sample_schema):
        report = await validator.validate(
            "SELECT * FROM orders", schema=sample_schema
        )
        assert isinstance(report.passed, bool)
        assert isinstance(report.syntax_ok, bool)
        assert isinstance(report.schema_valid, bool)
        assert isinstance(report.type_valid, bool)
        assert isinstance(report.score, float)
        assert 0.0 <= report.score <= 1.0

    async def test_errors_as_lists(self, validator):
        report = await validator.validate("GARBAGE")
        assert isinstance(report.syntax_errors, list)
        assert isinstance(report.schema_errors, list)
        assert isinstance(report.type_errors, list)
        assert isinstance(report.warnings, list)


# ═══════════════════════════════════════════════════════════════════════════
# Type compatibility helper
# ═══════════════════════════════════════════════════════════════════════════


class TestTypeCompatibility:
    def test_int_int_compatible(self):
        assert SQLValidator._types_compatible("INTEGER", "INTEGER")

    def test_int_bigint_compatible(self):
        assert SQLValidator._types_compatible("INTEGER", "BIGINT")

    def test_int_decimal_compatible(self):
        assert SQLValidator._types_compatible("INTEGER", "DECIMAL(10,2)")

    def test_varchar_text_compatible(self):
        assert SQLValidator._types_compatible("VARCHAR(100)", "TEXT")

    def test_date_timestamp_compatible(self):
        assert SQLValidator._types_compatible("DATE", "TIMESTAMP")

    def test_int_varchar_incompatible(self):
        assert not SQLValidator._types_compatible("INTEGER", "VARCHAR(100)")

    def test_bool_int_incompatible(self):
        assert not SQLValidator._types_compatible("BOOLEAN", "INTEGER")

    def test_float_decimal_compatible(self):
        assert SQLValidator._types_compatible("FLOAT", "DECIMAL(10,2)")
