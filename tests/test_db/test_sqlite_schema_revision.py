"""Real SQLite DDL revision and main-source binding; no model or network calls."""

import sqlite3
from contextlib import closing

import pytest

from app.db.connections import DatabaseType
from app.db.schema_extractor import SchemaExtractor
from app.knowledge.schema_retriever import _build_filtered_schema
from app.models.schema import SchemaSnapshot
from app.nodes.base import NodeInput
from app.nodes.execute_sql import ExecuteSQLNode
from app.nodes.schema_linking import _filter_schema


@pytest.mark.parametrize("external", [False, True])
@pytest.mark.parametrize("ddl", [
    "CREATE TABLE added (value TEXT)",
    "ALTER TABLE marker ADD COLUMN added TEXT", "DROP TABLE marker",
])
async def test_cache_refreshes_main_schema_before_ttl(tmp_path, monkeypatch, external, ddl):
    from app.config.settings import settings

    monkeypatch.setattr(settings, "schema_cache_seconds", 3600)
    monkeypatch.setattr(SchemaExtractor, "_cache", {})
    path = str(tmp_path / "source.sqlite")
    with closing(sqlite3.connect(path)) as conn:
        conn.execute("CREATE TABLE marker (id INT)")
        before = await SchemaExtractor.cached_extract(conn, DatabaseType.SQLITE)
        if external:
            with closing(sqlite3.connect(path)) as writer:
                writer.execute(ddl)
        else:
            conn.execute(ddl)
        after = await SchemaExtractor.cached_extract(conn, DatabaseType.SQLITE)
        if ddl.startswith("CREATE"):
            assert "added" in after.tables
        elif ddl.startswith("ALTER"):
            assert "added" in after.tables["marker"].column_names
        else:
            assert "marker" not in after.tables
        assert after.source_schema_version != before.source_schema_version


@pytest.mark.parametrize("explain", [False, True])
async def test_stale_source_fails_before_explain_or_execution(explain):
    with closing(sqlite3.connect(":memory:")) as conn:
        conn.executescript("CREATE TABLE marker(id INT); INSERT INTO marker VALUES(1);")
        schema = await SchemaExtractor.extract(conn, DatabaseType.SQLITE)
        conn.execute("ALTER TABLE marker ADD COLUMN added TEXT")
        result = await ExecuteSQLNode().execute(NodeInput(
            "SELECT id FROM marker", context={"schema": schema, "connection": conn},
            config={"explain": explain},
        ))
        assert result.metadata["status"] == "schema_changed", result
        assert result.result is None
        assert "Schema source changed" in " ".join(result.errors)
        fresh = await SchemaExtractor.extract(conn, DatabaseType.SQLITE)
        current = await ExecuteSQLNode().execute(NodeInput(
            "SELECT id FROM marker", context={"schema": fresh, "connection": conn},
        ))
        assert current.result["rows"] == [[1]]


@pytest.mark.parametrize("sql,accepted", [
    ("SELECT id FROM marker", False),
    ("SELECT COUNT(*) FROM marker", False),
    ("WITH x AS (SELECT id FROM marker) SELECT * FROM x", False),
    ("SELECT id FROM marker WHERE 0", False),
    ("SELECT id FROM main.marker", True),
    ("SELECT COUNT(*) FROM main.marker", True),
])
async def test_preexisting_temp_cannot_shadow_main(sql, accepted):
    with closing(sqlite3.connect(":memory:")) as conn:
        conn.executescript('''
            CREATE TABLE marker (id INT);
            INSERT INTO marker VALUES (1);
            CREATE TEMP TABLE marker (id INT);
            INSERT INTO temp.marker VALUES (999);
        ''')
        # Prime SQLite's statement cache before installing any source guard.
        conn.execute(sql).fetchall()
        schema = await SchemaExtractor.extract(conn, DatabaseType.SQLITE)
        output = await ExecuteSQLNode().execute(NodeInput(
            sql, context={"schema": schema, "connection": conn}, config={"explain": True},
        ))
        assert (not output.errors) is accepted, output
        if accepted:
            assert output.result["rows"] == [[1]]
        else:
            assert output.result is None
        # A failed or successful guarded read must not leave a connection authorizer behind.
        conn.execute("CREATE TABLE cleanup (id INT)")


@pytest.mark.parametrize("filter_fn", [_filter_schema, _build_filtered_schema])
async def test_source_revision_survives_linking_projection(filter_fn):
    with closing(sqlite3.connect(":memory:")) as conn:
        conn.execute("CREATE TABLE marker (id INT)")
        schema = await SchemaExtractor.extract(conn, DatabaseType.SQLITE)
        filtered = filter_fn(schema, ["marker"])
        assert filtered.source_schema_version == schema.source_schema_version
        assert filtered.source_schema_version is not None


@pytest.mark.parametrize("legacy_schema", [False, True])
async def test_direct_and_legacy_snapshot_keep_existing_read_scope(legacy_schema):
    with closing(sqlite3.connect(":memory:")) as conn:
        conn.executescript('''
            CREATE TABLE marker (id INT);
            CREATE TEMP TABLE marker (id INT);
            INSERT INTO temp.marker VALUES (999);
        ''')
        context = {"connection": conn}
        if legacy_schema:
            context["schema"] = SchemaSnapshot("sqlite", "legacy")
        result = await ExecuteSQLNode().execute(NodeInput("SELECT id FROM marker", context=context))
        assert not result.errors
        assert result.result["rows"] == [[999]]


def test_extraction_rejects_ddl_during_introspection():
    with closing(sqlite3.connect(":memory:")) as conn:
        conn.execute("CREATE TABLE marker (id INT)")
        changed = False

        def ddl_during_introspection(sql):
            nonlocal changed
            if not changed and sql.startswith("PRAGMA main.table_info"):
                changed = True
                conn.execute("ALTER TABLE marker ADD COLUMN added TEXT")

        conn.set_trace_callback(ddl_during_introspection)
        try:
            with pytest.raises(ValueError, match="Schema source changed"):
                SchemaExtractor._extract_sqlite(conn, "source")
            assert changed
        finally:
            conn.set_trace_callback(None)


@pytest.mark.parametrize("sql", [
    "SELECT COUNT(*) FROM marker", "SELECT id FROM marker ORDER BY id",
    "SELECT 1 FROM marker WHERE 0", "SELECT COUNT(*) FROM sqlite_master",
])
async def test_optimized_reads_with_missing_database_still_allow_unambiguous_main(sql):
    with closing(sqlite3.connect(":memory:")) as conn:
        conn.executescript('''
            CREATE TABLE marker(id INT PRIMARY KEY, value TEXT);
            INSERT INTO marker VALUES (1, 'fixture');
        ''')
        expected = [list(row) for row in conn.execute(sql).fetchall()]
        schema = await SchemaExtractor.extract(conn, DatabaseType.SQLITE)
        output = await ExecuteSQLNode().execute(NodeInput(
            sql, context={"schema": schema, "connection": conn}, config={"explain": True},
        ))
        assert not output.errors
        assert output.result["rows"] == expected


@pytest.mark.parametrize("sql", [
    "SELECT COUNT(*) FROM other.marker", "SELECT id FROM other.marker",
    "SELECT 1 FROM other.marker WHERE 0",
])
async def test_attached_read_cannot_borrow_a_main_namesake(sql):
    with closing(sqlite3.connect(":memory:")) as conn:
        conn.executescript('''
            CREATE TABLE marker(id INT);
            ATTACH ':memory:' AS other;
            CREATE TABLE other.marker(id INT);
            INSERT INTO other.marker VALUES (999);
        ''')
        schema = await SchemaExtractor.extract(conn, DatabaseType.SQLITE)
        output = await ExecuteSQLNode().execute(NodeInput(
            sql, context={"schema": schema, "connection": conn}, config={"explain": True},
        ))
        assert output.errors
        assert output.result is None
