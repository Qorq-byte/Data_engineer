"""Validation must only build a sqlglot mapping for the tables a statement names.

The previous implementation fed every table in the snapshot to ``MappingSchema``
and rendered one identifier per column, which cost ~0.5 s per request against 1k
tables while the statement touched a single table. These tests pin the reduced
scope and the behaviour that must not change with it.
"""

from __future__ import annotations

import pytest
import sqlglot
from sqlglot.schema import MappingSchema

from app.core import schema_validation
from app.core.schema_validation import check_schema_references
from app.models.schema import ColumnSchema, SchemaSnapshot, TableSchema


def _snapshot(count: int, dialect: str = "sqlite") -> SchemaSnapshot:
    tables = {
        f"table_{index}": TableSchema(
            f"table_{index}",
            columns=[ColumnSchema("id", "INT"), ColumnSchema("amount", "DECIMAL")],
            schema_name="main" if dialect == "sqlite" else "public",
        )
        for index in range(count)
    }
    tables["orders"] = TableSchema(
        "orders",
        columns=[ColumnSchema("id", "INT"), ColumnSchema("user_id", "INT")],
        schema_name="main" if dialect == "sqlite" else "public",
    )
    return SchemaSnapshot(dialect, "fixture", tables)


def test_mapping_is_built_only_for_referenced_tables(monkeypatch: pytest.MonkeyPatch) -> None:
    added: list[str] = []
    original = MappingSchema.add_table

    def counting_add_table(self, table, schema, **kwargs):  # noqa: ANN001, ANN202
        added.append(table.name)
        return original(self, table, schema, **kwargs)

    monkeypatch.setattr(MappingSchema, "add_table", counting_add_table)

    passed, errors = check_schema_references(
        "SELECT id FROM orders WHERE user_id = 1", _snapshot(200), "sqlite"
    )

    assert passed, errors
    assert added == ["orders"]


def test_unknown_table_still_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    passed, errors = check_schema_references("SELECT id FROM missing_table", _snapshot(5), "sqlite")

    assert not passed
    assert "not found in schema" in errors[0]


def test_join_builds_mapping_for_both_sources(monkeypatch: pytest.MonkeyPatch) -> None:
    added: list[str] = []
    original = MappingSchema.add_table

    def counting_add_table(self, table, schema, **kwargs):  # noqa: ANN001, ANN202
        added.append(table.name)
        return original(self, table, schema, **kwargs)

    monkeypatch.setattr(MappingSchema, "add_table", counting_add_table)
    schema = _snapshot(10)
    schema.tables["users"] = TableSchema(
        "users", columns=[ColumnSchema("id", "INT")], schema_name="main"
    )

    passed, errors = check_schema_references(
        "SELECT o.id FROM orders o JOIN users u ON u.id = o.user_id", schema, "sqlite"
    )

    assert passed, errors
    assert sorted(added) == ["orders", "users"]


def test_qualified_reference_resolves_case_insensitively() -> None:
    # SQLite/DuckDB resolve MAIN.ORDERS against "orders"; the reduced scope must
    # still find it even though the statement never spells the name exactly.
    schema = _snapshot(3)
    passed, errors = check_schema_references("SELECT id FROM MAIN.ORDERS", schema, "sqlite")

    assert passed, errors


def test_parse_failure_is_reported_as_schema_error() -> None:
    passed, errors = check_schema_references("SELECT FROM WHERE", _snapshot(2), "sqlite")

    assert not passed
    assert errors[0].startswith("SCHEMA_ERROR:")


def test_sqlglot_parse_guard_still_covers_unparsable_input(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
        raise sqlglot.errors.ParseError("unexpected token")

    monkeypatch.setattr(schema_validation.sqlglot, "parse_one", boom)

    passed, errors = check_schema_references("SELECT 1", _snapshot(2), "sqlite")

    assert not passed
    assert "unexpected token" in errors[0]
