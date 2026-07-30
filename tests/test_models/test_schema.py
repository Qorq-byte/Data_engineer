"""Tests for schema data models."""


from app.models.schema import (
    ColumnSchema,
    ForeignKey,
    SchemaSnapshot,
    TableSchema,
)


def test_column_schema_defaults():
    col = ColumnSchema(name="id", type="INTEGER", is_primary_key=True)
    assert col.name == "id"
    assert col.type == "INTEGER"
    assert col.nullable is True
    assert col.is_primary_key is True
    assert col.is_foreign_key is False
    assert col.references is None


def test_table_schema_helpers():
    table = TableSchema(
        name="orders",
        comment="Order records",
        columns=[
            ColumnSchema(name="id", type="INTEGER", is_primary_key=True),
            ColumnSchema(name="user_id", type="INTEGER", is_foreign_key=True,
                         references=("users", "id")),
            ColumnSchema(name="amount", type="REAL"),
            ColumnSchema(name="note", type="TEXT", nullable=True),
        ],
        row_count_estimate=10000,
    )

    assert table.column_names == ["id", "user_id", "amount", "note"]
    assert table.primary_keys == ["id"]
    assert table.nullable_columns == ["id", "user_id", "amount", "note"]

    assert table.get_column("id") is not None
    assert table.get_column("nonexistent") is None


def test_schema_snapshot_format_for_llm():
    snapshot = SchemaSnapshot(
        database_type="sqlite",
        database_name="test_db",
        tables={
            "users": TableSchema(
                name="users",
                comment="User accounts",
                columns=[
                    ColumnSchema(name="id", type="INTEGER", is_primary_key=True),
                    ColumnSchema(name="name", type="TEXT"),
                ],
            ),
        },
    )

    text = snapshot.format_for_llm()
    assert "test_db" in text
    assert "sqlite" in text
    assert "users" in text
    assert "id" in text
    assert "INTEGER" in text
    assert "PK" in text


def test_foreign_key_model():
    fk = ForeignKey(
        name="fk_orders_user",
        column="user_id",
        ref_table="users",
        ref_column="id",
    )
    assert fk.column == "user_id"
    assert fk.ref_table == "users"
    assert fk.ref_column == "id"
