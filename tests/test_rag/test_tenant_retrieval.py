"""Use real index stores and trusted tenant contexts to exercise isolation."""

from contextlib import contextmanager

import pytest

from app.auth.tenant import reset_current_tenant_id, set_current_tenant_id
from app.cache.query_cache import QueryPairCache
from app.db.connections import ConnectionFactory, DatabaseType
from app.knowledge.retrieval.bm25_index import BM25Index
from app.knowledge.retrieval.document_store import DocumentStore
from app.knowledge.retrieval.embedding import (
    EmbeddingGenerator,
    MockEmbeddingProvider,
    OllamaEmbeddingProvider,
)
from app.knowledge.retrieval.lancedb_store import LanceDBStore
from app.knowledge.retrieval.metric_rag import MetricRAG
from app.knowledge.retrieval.schema_rag import SchemaMetadataRAG
from app.models.rag_document import Document
from app.models.rag_metric import MetricDocument
from app.models.rag_schema import SchemaDocument, SchemaIndexNotReadyError, SchemaIndexWriteError
from app.rag.index_refresher import IndexRefresher


@contextmanager
def tenant(tenant_id):
    token = set_current_tenant_id(tenant_id)
    try:
        yield
    finally:
        reset_current_tenant_id(token)


@pytest.fixture
def indexes(tmp_path):
    dense = LanceDBStore(32, str(tmp_path / "vectors"))
    sparse = BM25Index(str(tmp_path / "fts.db"))
    embedding = EmbeddingGenerator(MockEmbeddingProvider(dimension=32))
    try:
        yield dense, sparse, embedding
    finally:
        sparse.close()
        dense.close()


async def test_schema_recovery_and_markers_belong_to_one_tenant(indexes, tmp_path, monkeypatch):
    dense, sparse, embedding = indexes
    rag = SchemaMetadataRAG(*indexes, state_dir=tmp_path)
    db_id = "shared-orders"

    def doc(name):
        return SchemaDocument("shared-id", db_id, table_name=name, embedding_text=name)

    async def search(index, name):
        return [row.table_name for row in await index.find_relevant_tables(name, db_id=db_id)]

    await rag.index_schemas([doc("legacy")], replace_db_id=db_id)
    with tenant(2):
        assert await search(rag, "legacy") == []
        await rag.index_schemas([doc("invoices")], replace_db_id=db_id)
        assert await search(rag, "invoices") == ["invoices"]

        def storage_failure(*args, **kwargs):
            raise RuntimeError("synthetic storage failure")

        with monkeypatch.context() as patch:
            patch.setattr(sparse, "index", storage_failure)
            patch.setattr(sparse, "restore_database", storage_failure)
            with pytest.raises(SchemaIndexWriteError, match="recovery failed"):
                await rag.index_schemas([doc("replacement")], replace_db_id=db_id)
        assert rag.unavailable_databases == (db_id,)
        assert rag.pending_replacements() == (db_id,)
        with pytest.raises(SchemaIndexWriteError):
            await search(rag, "invoices")

    assert rag.unavailable_databases == ()
    assert rag.pending_replacements() == ()
    assert await search(rag, "legacy") == ["legacy"]
    assert len(dense.snapshot_database("schema_metadata", db_id)) == 1
    assert sparse.search("schema_metadata", "legacy")[0]["doc_id"] == "shared-id"

    dense.close()
    sparse.close()
    reopened = SchemaMetadataRAG(dense, sparse, embedding, state_dir=tmp_path)
    assert reopened.refresh_required_databases == ()
    with tenant(2):
        assert reopened.refresh_required_databases == (db_id,)
        with pytest.raises(SchemaIndexNotReadyError):
            await search(reopened, "invoices")
        await reopened.index_schemas([doc("recovered")], replace_db_id=db_id)
        assert await search(reopened, "recovered") == ["recovered"]
        assert reopened.pending_replacements() == ()
        await reopened.clear_database(db_id)
        assert await search(reopened, "recovered") == []
    assert await search(reopened, "legacy") == ["legacy"]


async def test_refresh_state_is_scoped_to_the_source_tenant(indexes):
    rag = SchemaMetadataRAG(*indexes)
    refresher = IndexRefresher(rag)
    db_id = "shared-refresh"

    try:
        for tenant_id, table in [(1, "orders"), (2, "invoices")]:
            with tenant(tenant_id):
                connection = await ConnectionFactory.create(DatabaseType.SQLITE, {}, db_id)
                connection.execute(f"CREATE TABLE {table} (total INTEGER)")
                assert refresher.get_refresh_status()["tracked_databases"] == 0
                assert refresher.needs_refresh(db_id)
                assert await refresher.refresh(db_id)
                assert not refresher.needs_refresh(db_id)
                assert refresher.get_refresh_status()["tracked_databases"] == 1
                assert {row.table_name for row in await rag.find_relevant_tables(table)} == {table}
        with tenant(2):
            refresher.invalidate_all()
            assert refresher.needs_refresh(db_id)
        assert not refresher.needs_refresh(db_id)
        assert set(refresher.get_refresh_status()["last_refresh_time"]) == {db_id}
    finally:
        for tenant_id in (1, 2):
            with tenant(tenant_id):
                await ConnectionFactory.aclose(db_id)


async def test_metric_indexes_and_document_deletion_are_scoped(indexes, monkeypatch):
    from app.api.documents import delete_document

    metric_rag = MetricRAG(*indexes)
    documents = DocumentStore(*indexes)
    monkeypatch.setattr("app.rag.get_document_store", lambda: documents)

    async def metric(name):
        await metric_rag.index_metric(MetricDocument(
            "same", domain="sales", name=name, embedding_text=name,
        ))

    await metric("revenue")
    await documents.index_document(Document("same", title="legacy", content="retail"))
    with tenant(2):
        assert await metric_rag.match_metrics("revenue", domain="sales") == []
        await metric("invoices")
        assert [row.name for row in await metric_rag.match_metrics("invoices")] == ["invoices"]
        await metric("billing")
        assert metric_rag.stats() == {"lancedb_count": 1, "bm25_count": 1}
        assert [row.name for row in await metric_rag.match_metrics("billing")] == ["billing"]
        metric_rag.clear()
        assert await metric_rag.match_metrics("billing") == []
        await documents.index_document(Document("same", title="other", content="invoices"))
        assert (await delete_document("same"))["status"] == "deleted"
        assert documents.stats() == {"lancedb_count": 0, "bm25_count": 0}

    assert [row.name for row in await metric_rag.match_metrics("revenue")] == ["revenue"]
    assert [row.title for row in await documents.search("retail")] == ["legacy"]


async def test_query_cache_isolates_exact_hits_and_persisted_semantic_hits(indexes):
    dense, _, embedding = indexes
    cache = QueryPairCache(dense, embedding, l1_max_size=1)
    query = "count orders"
    await cache.store(query, "SELECT 1")
    with tenant(2):
        assert await cache.lookup(query) is None
        assert cache.stats()["l2_row_count"] == 0
        await cache.store(query, "SELECT 2")
        assert (await cache.lookup(query)).sql_text == "SELECT 2"
        cache.clear()
        assert cache.stats()["total_lookups"] == 0
        semantic_hit = await cache.lookup(query)
        assert semantic_hit.match_level == "semantic"
        assert semantic_hit.sql_text == "SELECT 2"
    assert (await cache.lookup(query)).sql_text == "SELECT 1"
    assert cache.stats()["l1_hits"] == 1
    fresh = QueryPairCache(dense, embedding)
    assert (await fresh.lookup(query)).sql_text == "SELECT 1"


async def test_tenant_search_keeps_sparse_hits_without_network_embeddings(indexes, monkeypatch):
    _, _, embedding = indexes
    schema = SchemaMetadataRAG(*indexes)
    metrics = MetricRAG(*indexes)
    documents = DocumentStore(*indexes)
    with tenant(2):
        await schema.index_schema(SchemaDocument(
            "invoice", "reporting", table_name="invoices", embedding_text="invoices",
        ))
        await metrics.index_metric(MetricDocument(
            "invoice", name="invoices", domain="billing", embedding_text="invoices",
        ))
        await documents.index_document(Document("invoice", title="Invoice", content="invoices"))
        provider = OllamaEmbeddingProvider()

        async def unexpected_embedding(texts):
            pytest.fail("Tenant search called the shared network embedding service")

        monkeypatch.setattr(provider, "embed", unexpected_embedding)
        monkeypatch.setattr(embedding, "provider", provider)
        assert [
            row.table_name for row in await schema.find_relevant_tables("invoices")
        ] == ["invoices"]
        assert [row.name for row in await metrics.match_metrics("invoices")] == ["invoices"]
        assert [row.title for row in await documents.search("invoices")] == ["Invoice"]
