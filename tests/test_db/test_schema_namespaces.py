"""Namespace provenance: live SQLite/DuckDB, offline PostgreSQL/MySQL parsing."""

import sqlite3
from dataclasses import asdict

import duckdb
import pytest

from app.core.schema_validation import check_schema_references
from app.db.schema_extractor import SchemaExtractor
from app.knowledge.schema_retriever import _build_filtered_schema
from app.models.schema import ColumnSchema, SchemaSnapshot, TableSchema
from app.rag.converters import snapshot_to_schema_docs


@pytest.mark.parametrize("dialect,namespace,catalog", [
    ("postgres", "public", None), ("mysql", "business", None),
    ("sqlite", "main", None), ("duckdb", "main", "memory"),
])
@pytest.mark.parametrize("template,expected", [
    ("SELECT id FROM {schema}.users", True),
    ("SELECT {schema}.users.id FROM {schema}.users", True),
    ("SELECT u.id FROM {schema}.users u", True),
    ("WITH x AS (SELECT id FROM {schema}.users) SELECT id FROM x", True),
    ("SELECT id FROM wrong.users", False),
    ("SELECT wrong.users.id FROM {schema}.users", False),
    ("SELECT {schema}.users.id FROM {schema}.users u", False),
    ("SELECT id FROM wrong.{schema}.users", False),
])
def test_namespace_reference_contract(dialect, namespace, catalog, template, expected):
    schema = SchemaSnapshot(dialect, "display-label", tables={
        "users": TableSchema("users", columns=[ColumnSchema("id", "INT")],
                             schema_name=namespace, catalog_name=catalog),
    })
    before = asdict(schema)
    passed, errors = check_schema_references(template.format(schema=namespace), schema, dialect)
    assert passed is expected, errors
    assert asdict(schema) == before


def test_postgres_quoted_namespace_case_and_unknown_provenance():
    table = TableSchema("Users", columns=[ColumnSchema("ID", "INT")], schema_name="Public")
    schema = SchemaSnapshot("postgresql", "label", {"Users": table})
    assert check_schema_references('SELECT "ID" FROM "Public"."Users"', schema, "postgres")[0]
    assert not check_schema_references('SELECT "ID" FROM public."Users"', schema, "postgres")[0]
    table.schema_name = None
    assert not check_schema_references('SELECT "ID" FROM "Public"."Users"', schema, "postgres")[0]


@pytest.mark.parametrize("dialect", ["sqlite", "duckdb"])
def test_case_insensitive_namespace_lookup_preserves_source_metadata(dialect):
    schema = SchemaSnapshot(dialect, "label", {"Users": TableSchema(
        "Users", columns=[ColumnSchema("ID", "INT")], schema_name="Main",
        catalog_name="Memory" if dialect == "duckdb" else None,
    )})
    assert check_schema_references('SELECT ID FROM MAIN.USERS', schema, dialect)[0]
    assert check_schema_references('SELECT "ID" FROM "Main"."Users"', schema, dialect)[0]
    if dialect == "duckdb":
        assert check_schema_references('SELECT ID FROM MEMORY.MAIN.USERS', schema, dialect)[0]


def test_sqlite_main_introspection_ignores_temp_and_attached_namesakes():
    with sqlite3.connect(":memory:") as conn:
        conn.executescript('''
            CREATE TABLE users (id INTEGER);
            INSERT INTO users VALUES (1);
            CREATE TEMP TABLE users (secret TEXT);
            ATTACH ':memory:' AS other;
            CREATE TABLE other.users (private TEXT);
            CREATE TABLE "odd'""name" (id INTEGER);
        ''')
        schema = SchemaExtractor._extract_sqlite(conn, "display-label")
        assert set(schema.tables) == {"users", 'odd\'"name'}
        assert schema.tables["users"].column_names == ["id"]
        assert schema.tables["users"].row_count_estimate == 1
        assert all(table.schema_name == "main" for table in schema.tables.values())
        sql = "SELECT main.users.id FROM main.users"
        assert check_schema_references(sql, schema, "sqlite")[0]
        assert conn.execute(sql).fetchall() == [(1,)]
        for sql in ("SELECT secret FROM temp.users", "SELECT private FROM other.users"):
            assert not check_schema_references(sql, schema, "sqlite")[0]


def test_duckdb_catalog_isolation_special_names_and_execution():
    conn = duckdb.connect(":memory:")
    try:
        conn.execute("CREATE TABLE users (id INTEGER)")
        conn.execute("INSERT INTO users VALUES (1)")
        conn.execute("ATTACH ':memory:' AS other")
        conn.execute("CREATE TABLE other.main.users (secret VARCHAR)")
        conn.execute("CREATE TABLE other.main.hidden (secret VARCHAR)")
        conn.execute('CREATE TABLE "odd\'""name" (id INTEGER)')
        schema = SchemaExtractor._extract_duckdb(conn, "display-label")
        assert set(schema.tables) == {"users", 'odd\'"name'}
        assert schema.tables["users"].column_names == ["id"]
        assert schema.tables["users"].row_count_estimate == 1
        for sql in (
            "SELECT id FROM main.users", "SELECT memory.main.users.id FROM memory.main.users",
            'SELECT id FROM memory.main."odd\'""name"',
        ):
            assert check_schema_references(sql, schema, "duckdb")[0]
            conn.execute(sql).fetchall()
        for sql in (
            "SELECT secret FROM other.main.users",
            "SELECT other.main.users.id FROM memory.main.users",
        ):
            assert not check_schema_references(sql, schema, "duckdb")[0]
        # Switching the actual current catalog changes the introspected scope;
        # a user-facing connection label must never substitute for this identity.
        conn.execute("USE other")
        other = SchemaExtractor._extract_duckdb(conn, "same-display-label")
        assert other.tables["users"].column_names == ["secret"]
        assert other.tables["users"].catalog_name == "other"
        assert set(other.tables) == {"users", "hidden"}
    finally:
        conn.close()


def test_namespace_survives_filter_prompt_and_rag_conversion():
    table = TableSchema("users", columns=[ColumnSchema("id", "INT")],
                        schema_name="main", catalog_name="source")
    snapshot = SchemaSnapshot("duckdb", "label", {"users": table})
    for candidates in ([], ["users"]):
        filtered = _build_filtered_schema(snapshot, candidates)
        assert filtered.tables["users"].schema_name == "main"
        assert filtered.tables["users"].catalog_name == "source"
        prompt = filtered.format_for_llm()
        assert "Schema: main" in prompt and "Catalog: source" in prompt
    docs = snapshot_to_schema_docs(snapshot, "connection-id", schema_name="legacy")
    assert {doc.schema_name for doc in docs} == {"main"}
    assert {doc.doc_id for doc in docs} == {
        "connection-id.main.users", "connection-id.main.users.id",
    }
    table.schema_name = None
    assert {doc.schema_name for doc in snapshot_to_schema_docs(snapshot, "db", "legacy")} == {
        "legacy",
    }
