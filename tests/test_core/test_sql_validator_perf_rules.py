"""Tests for SQLValidator Steps 4–5 — performance analysis + business rules.

Uses the real SQLite e-commerce fixture schema (users / products / orders)
extracted via ``SchemaExtractor`` — no mocks.
"""

import pytest

from app.core.sql_validator import SQLValidator
from app.db.connections import DatabaseType
from app.db.schema_extractor import SchemaExtractor
from app.models.domain import BusinessRule

# ── Fixtures ────────────────────────────────────────────────────────────


@pytest.fixture
def validator():
    return SQLValidator()


@pytest.fixture
async def sqlite_schema(sqlite_connection):
    """Real SchemaSnapshot extracted from the SQLite test database."""
    return await SchemaExtractor.extract(
        sqlite_connection, DatabaseType.SQLITE, "ecommerce_test"
    )


def perf_warnings(report):
    return [w for w in report.warnings if w.startswith("PERF_WARNING")]


def rule_warnings(report):
    return [w for w in report.warnings if w.startswith("RULE_WARNING")]


# ═══════════════════════════════════════════════════════════════════════════
# Step 4 — Performance analysis
# ═══════════════════════════════════════════════════════════════════════════


class TestFullTableScan:
    async def test_no_where_warns_full_table_scan(self, validator, sqlite_schema):
        report = await validator.validate(
            "SELECT name FROM users", schema=sqlite_schema
        )
        assert any("full table scan" in w for w in perf_warnings(report))

    async def test_where_clause_no_scan_warning(self, validator, sqlite_schema):
        report = await validator.validate(
            "SELECT name FROM users WHERE id = 1 LIMIT 10", schema=sqlite_schema
        )
        assert not any("full table scan" in w for w in perf_warnings(report))

    async def test_no_from_no_scan_warning(self, validator):
        report = await validator.validate("SELECT 1")
        assert not any("full table scan" in w for w in perf_warnings(report))

    async def test_plan_analysis_flags_full_scan(self, validator, sqlite_schema):
        report = await validator.validate(
            "SELECT name FROM users", schema=sqlite_schema
        )
        assert report.plan_analysis is not None
        assert report.plan_analysis.has_full_scan
        assert report.plan_analysis.warnings


class TestSelectStar:
    async def test_select_star_warns(self, validator, sqlite_schema):
        report = await validator.validate(
            "SELECT * FROM users WHERE id = 1 LIMIT 10", schema=sqlite_schema
        )
        assert any("SELECT *" in w for w in perf_warnings(report))

    async def test_qualified_star_warns(self, validator, sqlite_schema):
        report = await validator.validate(
            "SELECT u.* FROM users u WHERE u.id = 1 LIMIT 10",
            schema=sqlite_schema,
        )
        assert any("SELECT *" in w for w in perf_warnings(report))

    async def test_explicit_columns_no_star_warning(self, validator, sqlite_schema):
        report = await validator.validate(
            "SELECT id, name FROM users WHERE id = 1 LIMIT 10",
            schema=sqlite_schema,
        )
        assert not any("SELECT *" in w for w in perf_warnings(report))


class TestLeadingWildcardLike:
    async def test_leading_wildcard_warns(self, validator, sqlite_schema):
        report = await validator.validate(
            "SELECT name FROM users WHERE name LIKE '%son' LIMIT 10",
            schema=sqlite_schema,
        )
        assert any("index" in w for w in perf_warnings(report))

    async def test_trailing_wildcard_ok(self, validator, sqlite_schema):
        report = await validator.validate(
            "SELECT name FROM users WHERE name LIKE 'A%' LIMIT 10",
            schema=sqlite_schema,
        )
        assert not any("index" in w for w in perf_warnings(report))


class TestUnboundedResultSet:
    async def test_no_limit_warns(self, validator, sqlite_schema):
        report = await validator.validate(
            "SELECT name FROM users WHERE id > 0", schema=sqlite_schema
        )
        assert any("LIMIT" in w for w in perf_warnings(report))

    async def test_limit_present_ok(self, validator, sqlite_schema):
        report = await validator.validate(
            "SELECT name FROM users WHERE id > 0 LIMIT 100",
            schema=sqlite_schema,
        )
        assert not any("LIMIT" in w for w in perf_warnings(report))

    async def test_aggregate_without_limit_ok(self, validator, sqlite_schema):
        report = await validator.validate(
            "SELECT COUNT(*) FROM orders WHERE status = 'completed'",
            schema=sqlite_schema,
        )
        assert not any("LIMIT" in w for w in perf_warnings(report))

    async def test_group_by_without_limit_ok(self, validator, sqlite_schema):
        report = await validator.validate(
            "SELECT status, SUM(amount) FROM orders WHERE amount > 0 GROUP BY status",
            schema=sqlite_schema,
        )
        assert not any("LIMIT" in w for w in perf_warnings(report))


class TestCartesianJoin:
    async def test_join_without_on_warns(self, validator, sqlite_schema):
        report = await validator.validate(
            "SELECT u.name, o.amount FROM users u JOIN orders o "
            "WHERE u.id = 1 LIMIT 10",
            schema=sqlite_schema,
        )
        assert any("cartesian" in w for w in perf_warnings(report))

    async def test_join_with_on_ok(self, validator, sqlite_schema):
        report = await validator.validate(
            "SELECT u.name, o.amount FROM users u "
            "JOIN orders o ON u.id = o.user_id WHERE u.id = 1 LIMIT 10",
            schema=sqlite_schema,
        )
        assert not any("cartesian" in w for w in perf_warnings(report))

    async def test_join_with_using_ok(self, validator, sqlite_schema):
        report = await validator.validate(
            "SELECT name FROM users JOIN orders USING (id) "
            "WHERE name = 'Alice' LIMIT 10",
            schema=sqlite_schema,
        )
        assert not any("cartesian" in w for w in perf_warnings(report))

    async def test_cross_join_warns(self, validator, sqlite_schema):
        report = await validator.validate(
            "SELECT u.name FROM users u CROSS JOIN orders o "
            "WHERE u.id = 1 LIMIT 10",
            schema=sqlite_schema,
        )
        assert any("cartesian" in w for w in perf_warnings(report))

    async def test_natural_join_ok(self, validator, sqlite_schema):
        report = await validator.validate(
            "SELECT name FROM users NATURAL JOIN orders "
            "WHERE name = 'Alice' LIMIT 10",
            schema=sqlite_schema,
        )
        assert not any("cartesian" in w for w in perf_warnings(report))


class TestPerformanceStepBehavior:
    async def test_perf_warnings_do_not_affect_passed(self, validator, sqlite_schema):
        report = await validator.validate(
            "SELECT * FROM users", schema=sqlite_schema
        )
        assert len(perf_warnings(report)) >= 2  # full scan + star + no limit
        assert report.passed  # warnings never block

    async def test_perf_warnings_reduce_score_slightly(
        self, validator, sqlite_schema
    ):
        report = await validator.validate(
            "SELECT name FROM users WHERE id = 1", schema=sqlite_schema
        )
        assert report.passed
        assert 0.9 <= report.score < 1.0  # one soft warning (no LIMIT)

    async def test_skip_performance(self, validator, sqlite_schema):
        report = await validator.validate(
            "SELECT * FROM users", schema=sqlite_schema, skip_performance=True
        )
        assert not perf_warnings(report)
        assert report.plan_analysis is None
        assert report.score == 1.0

    async def test_unparseable_sql_no_perf_warnings(self, validator):
        report = await validator.validate("NOT A VALID SQL !!!")
        assert not perf_warnings(report)
        assert not report.passed  # syntax step handles the failure


# ═══════════════════════════════════════════════════════════════════════════
# Step 5 — Business rule validation
# ═══════════════════════════════════════════════════════════════════════════


class TestRuleTriggering:
    async def test_pattern_match_triggers_warning(self, validator, sqlite_schema):
        rule = BusinessRule(
            id="no_refund_col",
            description="Avoid raw refund_amount; use net amount instead",
            pattern=r"refund_amount",
        )
        report = await validator.validate(
            "SELECT refund_amount FROM orders WHERE id = 1 LIMIT 10",
            schema=sqlite_schema,
            rules=[rule],
        )
        matched = rule_warnings(report)
        assert len(matched) == 1
        assert "no_refund_col" in matched[0]

    async def test_pattern_no_match_no_warning(self, validator, sqlite_schema):
        rule = BusinessRule(
            id="no_refund_col",
            description="Avoid raw refund_amount",
            pattern=r"refund_amount",
        )
        report = await validator.validate(
            "SELECT amount FROM orders WHERE id = 1 LIMIT 10",
            schema=sqlite_schema,
            rules=[rule],
        )
        assert not rule_warnings(report)
        assert report.business_valid


class TestEnforcementDirectives:
    async def test_require_limit_violated(self, validator, sqlite_schema):
        rule = BusinessRule(
            id="orders_need_limit",
            description="Order queries must be bounded",
            pattern=r"orders",
            enforce=["require_limit=true"],
        )
        report = await validator.validate(
            "SELECT amount FROM orders WHERE id = 1",
            schema=sqlite_schema,
            rules=[rule],
        )
        assert any("LIMIT" in w for w in rule_warnings(report))

    async def test_require_limit_satisfied(self, validator, sqlite_schema):
        rule = BusinessRule(
            id="orders_need_limit",
            description="Order queries must be bounded",
            pattern=r"orders",
            enforce=["require_limit=true"],
        )
        report = await validator.validate(
            "SELECT amount FROM orders WHERE id = 1 LIMIT 10",
            schema=sqlite_schema,
            rules=[rule],
        )
        assert not rule_warnings(report)

    async def test_require_where_violated(self, validator, sqlite_schema):
        rule = BusinessRule(
            id="users_need_filter",
            description="User queries must be filtered",
            pattern=r"users",
            enforce=["require_where=true"],
        )
        report = await validator.validate(
            "SELECT name FROM users LIMIT 10",
            schema=sqlite_schema,
            rules=[rule],
        )
        assert any("WHERE" in w for w in rule_warnings(report))

    async def test_forbid_violated(self, validator, sqlite_schema):
        rule = BusinessRule(
            id="no_email_export",
            description="Email must not be selected",
            enforce=["forbid=email"],  # no pattern → applies to all SQL
        )
        report = await validator.validate(
            "SELECT email FROM users WHERE id = 1 LIMIT 10",
            schema=sqlite_schema,
            rules=[rule],
        )
        assert any("email" in w for w in rule_warnings(report))

    async def test_require_expression_violated(self, validator, sqlite_schema):
        rule = BusinessRule(
            id="orders_by_status",
            description="Order queries should filter by status",
            pattern=r"orders",
            enforce=["require=status"],
        )
        report = await validator.validate(
            "SELECT amount FROM orders WHERE id = 1 LIMIT 10",
            schema=sqlite_schema,
            rules=[rule],
        )
        assert any("status" in w for w in rule_warnings(report))

    async def test_precision_requires_round(self, validator, sqlite_schema):
        rule = BusinessRule(
            id="amount_precision",
            description="Amounts must be rounded to 2 decimals",
            pattern=r"amount",
            enforce=["precision=2"],
        )
        violated = await validator.validate(
            "SELECT amount FROM orders WHERE id = 1 LIMIT 10",
            schema=sqlite_schema,
            rules=[rule],
        )
        satisfied = await validator.validate(
            "SELECT ROUND(amount, 2) FROM orders WHERE id = 1 LIMIT 10",
            schema=sqlite_schema,
            rules=[rule],
        )
        assert any("ROUND" in w for w in rule_warnings(violated))
        assert not rule_warnings(satisfied)

    async def test_require_time_range(self, validator, sqlite_schema):
        rule = BusinessRule(
            id="orders_time_bounded",
            description="Order queries must have a time range",
            pattern=r"orders",
            enforce=["require_time_range=true"],
        )
        violated = await validator.validate(
            "SELECT amount FROM orders WHERE id = 1 LIMIT 10",
            schema=sqlite_schema,
            rules=[rule],
        )
        satisfied = await validator.validate(
            "SELECT amount FROM orders "
            "WHERE created_at >= '2026-07-01' LIMIT 10",
            schema=sqlite_schema,
            rules=[rule],
        )
        assert any("Time range" in w for w in rule_warnings(violated))
        assert not rule_warnings(satisfied)

    async def test_unknown_directive_ignored(self, validator, sqlite_schema):
        rule = BusinessRule(
            id="tz_rule",
            description="Timezone guidance for generation",
            pattern=r"orders",
            enforce=["timezone=Asia/Shanghai"],
        )
        report = await validator.validate(
            "SELECT amount FROM orders WHERE id = 1 LIMIT 10",
            schema=sqlite_schema,
            rules=[rule],
        )
        assert not rule_warnings(report)
        assert report.business_valid


class TestRuleStepBehavior:
    async def test_rule_warnings_do_not_affect_passed(self, validator, sqlite_schema):
        rule = BusinessRule(
            id="orders_need_limit",
            description="Order queries must be bounded",
            pattern=r"orders",
            enforce=["require_limit=true"],
        )
        report = await validator.validate(
            "SELECT amount FROM orders WHERE id = 1",
            schema=sqlite_schema,
            rules=[rule],
        )
        assert rule_warnings(report)
        assert not report.business_valid
        assert report.passed  # warnings never block
        assert report.business_warnings == rule_warnings(report)

    async def test_skip_rules(self, validator, sqlite_schema):
        rule = BusinessRule(
            id="orders_need_limit",
            description="Order queries must be bounded",
            pattern=r"orders",
            enforce=["require_limit=true"],
        )
        report = await validator.validate(
            "SELECT amount FROM orders WHERE id = 1",
            schema=sqlite_schema,
            rules=[rule],
            skip_rules=True,
        )
        assert not rule_warnings(report)
        assert report.business_valid
        assert report.business_warnings == []

    async def test_legacy_business_rules_param(self, validator, sqlite_schema):
        rule = BusinessRule(
            id="orders_need_limit",
            description="Order queries must be bounded",
            pattern=r"orders",
            enforce=["require_limit=true"],
        )
        report = await validator.validate(
            "SELECT amount FROM orders WHERE id = 1",
            sqlite_schema,
            [rule],  # positional business_rules (legacy)
        )
        assert rule_warnings(report)

    async def test_no_rules_business_valid(self, validator, sqlite_schema):
        report = await validator.validate(
            "SELECT amount FROM orders WHERE id = 1 LIMIT 10",
            schema=sqlite_schema,
        )
        assert report.business_valid
        assert report.business_warnings == []

    async def test_invalid_rule_pattern_ignored(self, validator, sqlite_schema):
        rule = BusinessRule(
            id="broken_regex",
            description="Rule with invalid regex",
            pattern=r"orders(",  # invalid regex
        )
        report = await validator.validate(
            "SELECT amount FROM orders WHERE id = 1 LIMIT 10",
            schema=sqlite_schema,
            rules=[rule],
        )
        assert not rule_warnings(report)
        assert report.passed
