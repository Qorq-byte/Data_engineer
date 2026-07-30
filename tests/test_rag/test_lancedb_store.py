"""Tests for LanceDBStore — connection, CRUD, and namespace management.

Uses temporary directories (``tmp_path``) for isolated LanceDB instances.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime

import pytest

from app.knowledge.retrieval.lancedb_store import (
    ALL_NAMESPACES,
    NAMESPACE_DOCUMENTS,
    NAMESPACE_METRICS,
    NAMESPACE_SCHEMA_METADATA,
    VECTOR_COL,
    LanceDBStore,
    document_to_record,
    metric_doc_to_record,
    schema_doc_to_record,
)

# ── Helpers ────────────────────────────────────────────────────────────


def _make_vector(dim: int, seed: int = 42) -> list[float]:
    """Create a deterministic unit vector of given dimension."""
    vec = []
    for i in range(dim):
        val = ((seed * 31 + i * 7) % 256) / 127.5 - 1.0
        vec.append(val)
    norm = math.sqrt(sum(v * v for v in vec))
    return [v / norm for v in vec]


# ── LanceDBStore tests ─────────────────────────────────────────────────


class TestLanceDBStore:
    """Core LanceDBStore integration tests."""

    DIM = 128  # small dimension for fast tests

    @pytest.fixture
    def store(self, tmp_path) -> LanceDBStore:
        uri = str(tmp_path / "lancedb_test")
        return LanceDBStore(dimension=self.DIM, uri=uri)

    # ── Construction ───────────────────────────────────────────────────

    def test_default_uri(self):
        store = LanceDBStore(dimension=256)
        assert ".data_engineer" in store.uri
        assert store.dimension == 256

    def test_custom_uri(self, tmp_path):
        uri = str(tmp_path / "custom_lancedb")
        store = LanceDBStore(dimension=128, uri=uri)
        assert store.uri == uri
        assert store.dimension == 128

    # ── Connection ─────────────────────────────────────────────────────

    def test_connect_creates_directory(self, store: LanceDBStore, tmp_path):
        store.connect()
        db_dir = tmp_path / "lancedb_test"
        assert db_dir.exists()
        assert db_dir.is_dir()

    def test_connect_is_idempotent(self, store: LanceDBStore):
        db1 = store.connect()
        db2 = store.connect()
        assert db1 is db2

    def test_close_resets_connection(self, store: LanceDBStore):
        store.connect()
        assert store._db is not None
        store.close()
        assert store._db is None

    # ── Namespace initialisation ───────────────────────────────────────

    def test_init_namespaces_creates_all_three(self, store: LanceDBStore):
        store.connect()
        tables = store.init_namespaces()
        assert set(tables.keys()) == ALL_NAMESPACES

    def test_init_namespaces_is_idempotent(self, store: LanceDBStore):
        store.connect()
        t1 = store.init_namespaces()
        t2 = store.init_namespaces()
        # Should not raise; tables are already open
        assert set(t1.keys()) == set(t2.keys())

    def test_init_namespaces_overwrite(self, store: LanceDBStore):
        store.connect()
        store.init_namespaces()
        # Overwrite should also not raise
        tables = store.init_namespaces(overwrite=True)
        assert set(tables.keys()) == ALL_NAMESPACES

    def test_ensure_namespace_creates_on_demand(self, store: LanceDBStore):
        store.connect()
        table = store.ensure_namespace(NAMESPACE_SCHEMA_METADATA)
        assert table is not None
        assert store.count(NAMESPACE_SCHEMA_METADATA) == 0

    def test_ensure_namespace_unknown_raises(self, store: LanceDBStore):
        store.connect()
        with pytest.raises(ValueError, match="Unknown namespace"):
            store.ensure_namespace("bogus_namespace")

    # ── Insert ─────────────────────────────────────────────────────────

    def test_insert_single_record(self, store: LanceDBStore):
        store.connect()
        store.ensure_namespace(NAMESPACE_SCHEMA_METADATA)
        record = _make_schema_record("doc_1", _make_vector(self.DIM, 1))
        count = store.insert(NAMESPACE_SCHEMA_METADATA, [record])
        assert count == 1
        assert store.count(NAMESPACE_SCHEMA_METADATA) == 1

    def test_insert_multiple_records(self, store: LanceDBStore):
        store.connect()
        store.ensure_namespace(NAMESPACE_METRICS)
        records = [
            _make_metric_record(f"metric_{i}", _make_vector(self.DIM, i))
            for i in range(5)
        ]
        count = store.insert(NAMESPACE_METRICS, records)
        assert count == 5
        assert store.count(NAMESPACE_METRICS) == 5

    def test_insert_empty_list(self, store: LanceDBStore):
        store.connect()
        store.ensure_namespace(NAMESPACE_SCHEMA_METADATA)
        count = store.insert(NAMESPACE_SCHEMA_METADATA, [])
        assert count == 0

    # ── Search ─────────────────────────────────────────────────────────

    def test_search_returns_results(self, store: LanceDBStore):
        store.connect()
        store.ensure_namespace(NAMESPACE_DOCUMENTS)
        # Insert 3 documents
        for i in range(3):
            record = _make_document_record(f"doc_{i}", _make_vector(self.DIM, i))
            store.insert(NAMESPACE_DOCUMENTS, [record])

        # Search with vector similar to doc_0
        results = store.search(
            NAMESPACE_DOCUMENTS,
            _make_vector(self.DIM, 0),
            top_k=2,
        )
        assert len(results) == 2
        # doc_0 should be the closest (distance ≈ 0)
        assert results[0]["doc_id"] == "doc_0"

    def test_search_respects_top_k(self, store: LanceDBStore):
        store.connect()
        store.ensure_namespace(NAMESPACE_DOCUMENTS)
        for i in range(10):
            record = _make_document_record(f"doc_{i}", _make_vector(self.DIM, i))
            store.insert(NAMESPACE_DOCUMENTS, [record])

        results = store.search(
            NAMESPACE_DOCUMENTS,
            _make_vector(self.DIM, 0),
            top_k=3,
        )
        assert len(results) == 3

    def test_search_with_filter(self, store: LanceDBStore):
        store.connect()
        store.ensure_namespace(NAMESPACE_SCHEMA_METADATA)
        for i in range(5):
            record = _make_schema_record(
                f"doc_{i}", _make_vector(self.DIM, i), db_id="db_a" if i < 3 else "db_b"
            )
            store.insert(NAMESPACE_SCHEMA_METADATA, [record])

        results = store.search(
            NAMESPACE_SCHEMA_METADATA,
            _make_vector(self.DIM, 0),
            top_k=10,
            filter_expr="db_id = 'db_a'",
        )
        assert len(results) == 3
        for r in results:
            assert r["db_id"] == "db_a"

    def test_search_empty_namespace(self, store: LanceDBStore):
        store.connect()
        store.ensure_namespace(NAMESPACE_SCHEMA_METADATA)
        results = store.search(
            NAMESPACE_SCHEMA_METADATA,
            _make_vector(self.DIM, 0),
            top_k=5,
        )
        assert results == []

    # ── Delete ─────────────────────────────────────────────────────────

    def test_delete_matching_records(self, store: LanceDBStore):
        store.connect()
        store.ensure_namespace(NAMESPACE_DOCUMENTS)
        for i in range(5):
            record = _make_document_record(f"doc_{i}", _make_vector(self.DIM, i))
            store.insert(NAMESPACE_DOCUMENTS, [record])
        assert store.count(NAMESPACE_DOCUMENTS) == 5

        deleted = store.delete(NAMESPACE_DOCUMENTS, "doc_id = 'doc_0'")
        assert deleted == 1
        assert store.count(NAMESPACE_DOCUMENTS) == 4

    def test_delete_no_match(self, store: LanceDBStore):
        store.connect()
        store.ensure_namespace(NAMESPACE_DOCUMENTS)
        store.insert(NAMESPACE_DOCUMENTS, [
            _make_document_record("doc_0", _make_vector(self.DIM, 0))
        ])
        deleted = store.delete(NAMESPACE_DOCUMENTS, "doc_id = 'nonexistent'")
        assert deleted == 0

    # ── Drop ───────────────────────────────────────────────────────────

    def test_drop_namespace(self, store: LanceDBStore):
        store.connect()
        store.ensure_namespace(NAMESPACE_SCHEMA_METADATA)
        store.insert(NAMESPACE_SCHEMA_METADATA, [
            _make_schema_record("doc_1", _make_vector(self.DIM, 1))
        ])
        store.drop_namespace(NAMESPACE_SCHEMA_METADATA)
        # After drop, the namespace should not exist
        namespaces = store.list_namespaces()
        assert NAMESPACE_SCHEMA_METADATA not in namespaces

    def test_drop_unknown_namespace_raises(self, store: LanceDBStore):
        store.connect()
        with pytest.raises(ValueError, match="Unknown namespace"):
            store.drop_namespace("bogus")

    # ── List / Stats ───────────────────────────────────────────────────

    def test_list_namespaces_initially_empty(self, store: LanceDBStore):
        store.connect()
        assert store.list_namespaces() == []

    def test_list_namespaces_after_init(self, store: LanceDBStore):
        store.connect()
        store.init_namespaces()
        # All three should be present
        ns = store.list_namespaces()
        for name in ALL_NAMESPACES:
            assert name in ns

    def test_namespace_stats(self, store: LanceDBStore):
        store.connect()
        store.init_namespaces()
        store.insert(NAMESPACE_SCHEMA_METADATA, [
            _make_schema_record("s1", _make_vector(self.DIM, 1)),
            _make_schema_record("s2", _make_vector(self.DIM, 2)),
        ])
        store.insert(NAMESPACE_METRICS, [
            _make_metric_record("m1", _make_vector(self.DIM, 3)),
        ])
        stats = store.namespace_stats()
        assert stats.get(NAMESPACE_SCHEMA_METADATA) == 2
        assert stats.get(NAMESPACE_METRICS) == 1
        assert stats.get(NAMESPACE_DOCUMENTS) == 0


class TestLanceDBStoreCrossNamespace:
    """Cross-namespace isolation tests."""

    DIM = 64

    @pytest.fixture
    def store(self, tmp_path) -> LanceDBStore:
        uri = str(tmp_path / "lancedb_cross")
        store = LanceDBStore(dimension=self.DIM, uri=uri)
        store.connect()
        store.init_namespaces()
        return store

    def test_namespaces_are_isolated(self, store: LanceDBStore):
        """Inserting into one namespace does not affect others."""
        store.insert(NAMESPACE_SCHEMA_METADATA, [
            _make_schema_record("s1", _make_vector(self.DIM, 1)),
            _make_schema_record("s2", _make_vector(self.DIM, 2)),
        ])
        store.insert(NAMESPACE_METRICS, [
            _make_metric_record("m1", _make_vector(self.DIM, 3)),
        ])

        assert store.count(NAMESPACE_SCHEMA_METADATA) == 2
        assert store.count(NAMESPACE_METRICS) == 1
        assert store.count(NAMESPACE_DOCUMENTS) == 0


# ── Record conversion helpers ──────────────────────────────────────────


class TestRecordConverters:
    """Tests for document → LanceDB record conversion helpers."""

    DIM = 128

    def test_schema_doc_to_record(self):
        from app.models.rag_schema import SchemaDocument

        doc = SchemaDocument(
            doc_id="test_db.public.users.id",
            db_id="test_db",
            schema_name="public",
            table_name="users",
            column_name="id",
            data_type="INTEGER",
            comment="Primary key",
            is_primary_key=True,
            is_foreign_key=False,
            embedding_text="users.id column",
        )
        vec = _make_vector(self.DIM, 1)
        record = schema_doc_to_record(doc, vec)

        assert record["doc_id"] == "test_db.public.users.id"
        assert record["db_id"] == "test_db"
        assert record["table_name"] == "users"
        assert record["column_name"] == "id"
        assert record["is_primary_key"] is True
        assert record["is_foreign_key"] is False
        assert len(record[VECTOR_COL]) == self.DIM
        assert record[VECTOR_COL] == [float(v) for v in vec]

    def test_schema_doc_to_record_table_level(self):
        from app.models.rag_schema import SchemaDocument

        doc = SchemaDocument(
            doc_id="test_db.public.orders",
            db_id="test_db",
            table_name="orders",
            column_name=None,
            comment="Order table",
        )
        vec = _make_vector(self.DIM, 2)
        record = schema_doc_to_record(doc, vec)
        assert record["column_name"] == ""
        assert record["data_type"] == ""

    def test_metric_doc_to_record(self):
        from app.models.rag_metric import MetricDocument

        doc = MetricDocument(
            doc_id="metric_revenue",
            domain="ecommerce",
            name="revenue",
            name_zh="营收",
            description="Total revenue",
            formula="SUM(amount)",
            dimensions=["date", "category"],
        )
        vec = _make_vector(self.DIM, 3)
        record = metric_doc_to_record(doc, vec)

        assert record["doc_id"] == "metric_revenue"
        assert record["domain"] == "ecommerce"
        assert record["name"] == "revenue"
        assert record["name_zh"] == "营收"
        assert len(record[VECTOR_COL]) == self.DIM

    def test_document_to_record(self):
        from app.models.rag_document import Document

        doc = Document(
            doc_id="doc_guide_1",
            title="User Guide",
            content="How to query the database.",
            content_type="user_guide",
            source="builtin",
            chunk_index=0,
            language="en",
        )
        vec = _make_vector(self.DIM, 4)
        record = document_to_record(doc, vec)

        assert record["doc_id"] == "doc_guide_1"
        assert record["title"] == "User Guide"
        assert record["content_type"] == "user_guide"
        assert record["chunk_index"] == 0
        assert record["language"] == "en"
        assert len(record[VECTOR_COL]) == self.DIM


# ── Record factory helpers ─────────────────────────────────────────────


def _make_schema_record(
    doc_id: str, vector: list[float], db_id: str = "test_db"
) -> dict:
    """Build a minimal schema_metadata record dict."""
    now = datetime.now(UTC)
    return {
        "doc_id": doc_id,
        "db_id": db_id,
        "schema_name": "public",
        "table_name": "users",
        "column_name": "id",
        "data_type": "INTEGER",
        "comment": "test column",
        "is_primary_key": False,
        "is_foreign_key": False,
        "fk_references_json": "[]",
        "enum_values_json": "[]",
        "sample_values_json": "[]",
        "table_row_count": 100,
        "embedding_text": f"text for {doc_id}",
        "tags_json": "[]",
        "updated_at": now,
        VECTOR_COL: [float(v) for v in vector],
    }


def _make_metric_record(doc_id: str, vector: list[float]) -> dict:
    """Build a minimal metrics record dict."""
    now = datetime.now(UTC)
    return {
        "doc_id": doc_id,
        "domain": "ecommerce",
        "name": "revenue",
        "name_zh": "营收",
        "aliases_json": "[]",
        "description": "test metric",
        "formula": "SUM(amount)",
        "metric_type": "derived",
        "aggregation": "sum",
        "dimensions_json": "[]",
        "time_grain": "day",
        "precision": 2,
        "sql_template": "",
        "embedding_text": f"text for {doc_id}",
        "updated_at": now,
        "version": 1,
        VECTOR_COL: [float(v) for v in vector],
    }


def _make_document_record(doc_id: str, vector: list[float]) -> dict:
    """Build a minimal documents record dict."""
    now = datetime.now(UTC)
    return {
        "doc_id": doc_id,
        "domain": "",
        "title": f"Title {doc_id}",
        "content": "Some content here.",
        "content_type": "user_guide",
        "source": "builtin",
        "chunk_index": 0,
        "parent_doc_id": "",
        "embedding_text": f"text for {doc_id}",
        "keywords_json": "[]",
        "language": "en",
        "created_at": now,
        "updated_at": now,
        VECTOR_COL: [float(v) for v in vector],
    }
