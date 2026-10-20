"""Embedding changes preserve existing records and require explicit rebuilding."""

import pytest

from app.knowledge.retrieval.lancedb_store import LanceDBStore, schema_doc_to_record
from app.models.rag_schema import SchemaDocument


def populated_store(tmp_path, embedding_id=""):
    store = LanceDBStore(4, str(tmp_path), embedding_id=embedding_id)
    store.init_namespaces()
    store.insert(
        "schema_metadata",
        [
            schema_doc_to_record(
                SchemaDocument("keep", db_id="original", table_name="orders"), [1, 0, 0, 0]
            )
        ],
    )
    return store


def test_dimension_mismatch_preserves_original_rows(tmp_path):
    original = populated_store(tmp_path)
    with pytest.raises(ValueError, match="dimension mismatch"):
        LanceDBStore(8, str(tmp_path)).init_namespaces()
    assert original.count("schema_metadata") == 1
    assert original._table_vector_dim(original.ensure_namespace("schema_metadata")) == 4


def test_same_dimension_keeps_existing_rows(tmp_path):
    populated_store(tmp_path)
    assert LanceDBStore(4, str(tmp_path)).count("schema_metadata") == 1


def test_same_dimension_different_model_is_rejected(tmp_path):
    original = populated_store(tmp_path, "model-a-v1")
    with pytest.raises(ValueError, match="version mismatch"):
        LanceDBStore(4, str(tmp_path), embedding_id="model-b-v1").connect()
    assert original.count("schema_metadata") == 1
    assert LanceDBStore(4, str(tmp_path), embedding_id="model-a-v1").count("schema_metadata") == 1


def test_legacy_populated_index_is_not_silently_adopted(tmp_path):
    original = populated_store(tmp_path)
    with pytest.raises(ValueError, match="Unversioned"):
        LanceDBStore(4, str(tmp_path), embedding_id="unknown-model").connect()
    assert original.count("schema_metadata") == 1
