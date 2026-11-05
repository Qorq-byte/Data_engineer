"""Qualification must reject unknown sources and columns after dependency upgrades."""

import pytest

from app.core.schema_validation import check_schema_references
from app.db.connections import DatabaseType
from app.db.schema_extractor import SchemaExtractor


@pytest.mark.parametrize("dialect", ["sqlite", "duckdb", "postgres", "mysql"])
@pytest.mark.parametrize("sql,expected", [
    ("SELECT id FROM orders", True),
    ("WITH x AS (SELECT id FROM orders) SELECT id FROM x", True),
    ("SELECT missing FROM orders", False),
    ("SELECT id FROM wrong.orders", False),
    ("SELECT t.missing FROM orders t", False),
    ("SELECT id FROM orders UNION SELECT missing FROM orders", False),
    ("SELECT phantom FROM generate_series(1,3) AS t", False),
])
async def test_qualification_safety_contract(sqlite_connection, dialect, sql, expected):
    snapshot = await SchemaExtractor.extract(sqlite_connection, DatabaseType.SQLITE, "fixture")
    original = sql
    assert check_schema_references(sql, snapshot, dialect)[0] is expected
    assert sql == original
