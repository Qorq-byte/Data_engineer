"""Pure SQL-policy checks; these do not simulate or certify a Snowflake driver."""

import pytest
import sqlglot
from sqlglot import exp

from app.db.snowflake_source import SnowflakeConnection


@pytest.fixture
def policy():
    # No driver I/O: only the adapter's real AST policy is under test.
    return SnowflakeConnection(None, "DE_FIXTURE", "PUBLIC")


@pytest.mark.parametrize("sql", [
    'SELECT * FROM "de_fixture".PUBLIC.orders',
    'SELECT * FROM DE_FIXTURE."public".orders',
    'SELECT * FROM "De_Fixture"."PUBLIC".orders',
    "SELECT * FROM other.PUBLIC.orders",
    "SELECT * FROM DE_FIXTURE.other.orders",
    "SELECT * FROM orders JOIN other.orders o ON orders.id = o.id",
    "WITH x AS (SELECT * FROM other.orders) SELECT * FROM x",
    "SELECT * FROM orders UNION ALL SELECT * FROM other.orders",
    "SELECT id INTO new_orders FROM orders",
    "DELETE FROM orders",
    "SELECT 1; SELECT 2",
    "SELECT SYSTEM$CANCEL_QUERY('example')",
    "SELECT * FROM TABLE(GENERATOR(ROWCOUNT => 10))",
    "SELECT * FROM orders LIMIT -1",
    "SELECT * FROM orders LIMIT $count",
])
def test_rejects_unsafe_or_out_of_scope_sql(policy, sql):
    with pytest.raises(ValueError):
        policy._bounded_query(sql, 4)


@pytest.mark.parametrize("sql, limit", [
    ("SELECT * FROM orders", 4),
    ("SELECT * FROM de_fixture.public.orders", 4),
    ('SELECT * FROM "DE_FIXTURE"."PUBLIC".orders', 4),
    ("WITH x AS (SELECT id FROM orders) SELECT * FROM x", 4),
    ("SELECT id FROM orders UNION ALL SELECT id FROM orders", 4),
    ("SELECT * FROM orders LIMIT 2", 2),
    ("SELECT * FROM orders LIMIT 0", 0),
    ("SELECT * FROM orders LIMIT 100 OFFSET 2", 4),
])
def test_preserves_query_and_bounds_outer_results(policy, sql, limit):
    bounded = policy._bounded_query(sql, 4)
    parsed = sqlglot.parse_one(bounded, read="snowflake")
    assert isinstance(parsed, exp.Query)
    assert parsed.args["limit"].expression.this == str(limit)
    assert len(sqlglot.parse(bounded, read="snowflake")) == 1


@pytest.mark.parametrize("field", ["database", "schema", "warehouse", "role"])
@pytest.mark.parametrize("value", [None, "", "db.schema", '"quoted"', "a;b", " a", 1])
def test_connection_namespace_requires_unquoted_identifiers(field, value):
    with pytest.raises(ValueError):
        SnowflakeConnection._identifier(value, field)
