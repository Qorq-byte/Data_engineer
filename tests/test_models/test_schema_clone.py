"""SchemaSnapshot.clone() must equal copy.deepcopy and stay isolated from the cache.

The extraction cache hands out one copy per request. ``copy.deepcopy`` cost
1.3-2.7 s per call on a 10k-table snapshot, so ``clone()`` rebuilds the tree by
hand instead — these tests pin the two properties that make the swap safe:
same values as deepcopy, and no shared mutable state.
"""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime

import pytest

from app.models.schema import (
    ColumnSchema,
    DuckDBSourceState,
    ForeignKey,
    IndexInfo,
    SchemaSnapshot,
    TableSchema,
)


def _rich_snapshot() -> SchemaSnapshot:
    users = TableSchema(
        "users",
        comment="用户表",
        columns=[
            ColumnSchema(
                "id",
                "INT",
                nullable=False,
                is_primary_key=True,
                default=0,
                enum_values=["a", "b"],
                sample_values=[1, 2, {"nested": [3]}],
            ),
            ColumnSchema(
                "phone",
                "VARCHAR",
                is_pii=True,
                pii_type="phone",
                comment="手机号",
            ),
        ],
        row_count_estimate=42,
        indexes=[IndexInfo("users_id_idx", ["id"], is_unique=True)],
        foreign_keys=[ForeignKey("fk_org", "org_id", "orgs", "id")],
        schema_name="public",
        catalog_name="catalog",
    )
    return SchemaSnapshot(
        "postgresql",
        "shop",
        {"users": users},
        created_at=datetime(2026, 1, 2, 3, 4, 5),
        version=3,
        source_schema_version=7,
        duckdb_source_state=DuckDBSourceState("memory", 1, "main", 2, "main", (("a", "b"),)),
    )


def test_clone_matches_deepcopy() -> None:
    import copy

    original = _rich_snapshot()

    assert asdict(original.clone()) == asdict(copy.deepcopy(original))


def test_clone_is_isolated_from_the_original() -> None:
    original = _rich_snapshot()
    clone = original.clone()

    clone.database_name = "other"
    clone.tables["extra"] = TableSchema("extra")
    table = clone.tables["users"]
    table.name = "renamed"
    table.columns[0].name = "mutated"
    table.columns[0].enum_values.append("c")
    table.columns[0].sample_values.append("x")
    table.columns[0].sample_values[2]["nested"].append(4)
    table.indexes[0].columns.append("other")
    table.foreign_keys[0].ref_table = "elsewhere"

    assert original.database_name == "shop"
    assert set(original.tables) == {"users"}
    assert original.tables["users"].name == "users"
    column = original.tables["users"].columns[0]
    assert column.name == "id"
    assert column.enum_values == ["a", "b"]
    assert column.sample_values == [1, 2, {"nested": [3]}]
    assert original.tables["users"].indexes[0].columns == ["id"]
    assert original.tables["users"].foreign_keys[0].ref_table == "orgs"


def test_clone_shares_immutable_fields_only() -> None:
    original = _rich_snapshot()
    clone = original.clone()

    assert clone.created_at is original.created_at
    assert clone.duckdb_source_state is original.duckdb_source_state
    assert clone.tables["users"] is not original.tables["users"]


def test_clone_of_empty_snapshot() -> None:
    original = SchemaSnapshot("sqlite", "", {})

    clone = original.clone()

    assert clone.tables == {}
    assert asdict(clone) == asdict(original)


@pytest.mark.anyio
async def test_cached_extract_hands_out_independent_copies() -> None:
    from app.db.connections import ConnectionFactory, DatabaseType
    from app.db.schema_extractor import SchemaExtractor

    connection = await ConnectionFactory.create(
        DatabaseType.SQLITE, {"path": ":memory:"}, "clone-fixture"
    )
    try:
        connection.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT)")
        first = await SchemaExtractor.cached_extract(connection, DatabaseType.SQLITE, "memory")
        first.tables["users"].columns[0].name = "mutated"
        first.tables["injected"] = TableSchema("injected")

        second = await SchemaExtractor.cached_extract(connection, DatabaseType.SQLITE, "memory")

        assert second.tables["users"].columns[0].name == "id"
        assert "injected" not in second.tables
    finally:
        SchemaExtractor.invalidate(connection)
        await ConnectionFactory.aclose("clone-fixture")
