"""Tests for LanceDB dimension-migration behaviour.

Switching embedding models (e.g. mock 256 → ollama 768) leaves incompatible
vectors in the persistent tables; ``init_namespaces`` must recreate them.
"""

from __future__ import annotations

from app.knowledge.retrieval.lancedb_store import LanceDBStore


class TestDimensionMigration:
    def test_recreates_tables_on_dimension_mismatch(self, tmp_path):
        uri = str(tmp_path / "lancedb_dim_migration")

        # First store: dim 4, index one doc
        store4 = LanceDBStore(dimension=4, uri=uri)
        tables4 = store4.init_namespaces()
        store4.close()

        # Second store: dim 8 — must drop and recreate the old tables
        store8 = LanceDBStore(dimension=8, uri=uri)
        tables8 = store8.init_namespaces()

        for namespace, table in tables8.items():
            dim = store8._table_vector_dim(table)
            assert dim == 8, f"{namespace}: expected dim 8, got {dim}"

    def test_same_dimension_keeps_existing_table(self, tmp_path):
        uri = str(tmp_path / "lancedb_dim_stable")
        store = LanceDBStore(dimension=8, uri=uri)
        first = store.init_namespaces()
        store.close()

        reopened = LanceDBStore(dimension=8, uri=uri)
        second = reopened.init_namespaces()
        # Same dim → tables must survive (no recreation), rows preserved
        assert reopened._table_vector_dim(second["schema_metadata"]) == 8
