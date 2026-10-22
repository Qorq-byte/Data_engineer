"""Real LanceDB/FTS5 regressions: replacement, full text and scope before top K."""

from types import SimpleNamespace

import pytest

from app.knowledge.retrieval.bm25_index import BM25Index
from app.knowledge.retrieval.document_store import DocumentStore
from app.knowledge.retrieval.embedding import EmbeddingGenerator, MockEmbeddingProvider
from app.knowledge.retrieval.lancedb_store import LanceDBStore
from app.knowledge.retrieval.rrf_fusion import RRFFusion
from app.knowledge.retrieval.schema_rag import SchemaMetadataRAG
from app.models.rag_document import Document
from app.models.rag_schema import SchemaDocument


@pytest.fixture
def backends(tmp_path):
    vector = LanceDBStore(16, str(tmp_path / "lance"))
    sparse = BM25Index()
    embedding = EmbeddingGenerator(MockEmbeddingProvider(16))
    yield vector, sparse, embedding
    sparse.close()
    vector.close()


async def test_document_shrinking_removes_both_old_chunk_sets(backends):
    vector, sparse, embedding = backends
    store = DocumentStore(vector, sparse, embedding, max_chunk_tokens=40, overlap_tokens=5)
    await store.index_document(Document("quote'parent", content="obsolete " * 100))
    assert store.stats()["bm25_count"] > 1
    await store.index_document(Document("quote'parent", content="fresh body"))
    assert store.stats() == {"lancedb_count": 1, "bm25_count": 1}
    assert sparse.search("documents", "obsolete") == []


async def test_full_body_and_scope_survive_distractors(backends):
    vector, sparse, embedding = backends
    store = DocumentStore(vector, sparse, embedding, RRFFusion(alpha=0))
    await store.index_documents(
        [Document(f"noise-{i}", domain="elsewhere", content="needle") for i in range(35)]
        + [
            Document(
                "target",
                domain="allowed'quoted",
                title="General guide",
                content="Heading\n" + "ordinary words " * 20 + "needle bodytail",
            )
        ]
    )
    hits = await store.search("needle", top_k=1, domain="allowed'quoted")
    assert len(hits) == 1
    assert hits[0].parent_doc_id == "target"
    assert hits[0].sparse_rank == 1
    assert "bodytail" in hits[0].content


async def test_instance_chunk_limits_and_validation(backends):
    vector, sparse, embedding = backends
    with pytest.raises(ValueError):
        DocumentStore(vector, sparse, embedding, max_chunk_tokens=10, overlap_tokens=10)
    store = DocumentStore(vector, sparse, embedding, max_chunk_tokens=10, overlap_tokens=2)
    chunks = store._chunk_document(Document("small", content="a" * 130))
    assert len(chunks) > 1
    assert all(len(chunk.content) <= 40 for chunk in chunks)
    assert chunks[0].content[-8:] == chunks[1].content[:8]


async def test_database_snapshot_replacement_and_empty_schema(backends):
    vector, sparse, embedding = backends
    rag = SchemaMetadataRAG(vector, sparse, embedding)
    await rag.index_schemas(
        [
            SchemaDocument("a.old", "a", embedding_text="obsolete"),
            SchemaDocument("b.keep", "b", embedding_text="keep"),
        ]
    )
    await rag.index_schemas(
        [SchemaDocument("a.new", "a", embedding_text="fresh")], replace_db_id="a"
    )
    assert rag.stats() == {"lancedb_count": 2, "bm25_count": 2}
    assert sparse.search("schema_metadata", "obsolete") == []
    await rag.index_schemas([], replace_db_id="a")
    assert rag.stats() == {"lancedb_count": 1, "bm25_count": 1}
    assert sparse.search("schema_metadata", "keep")[0]["doc_id"] == "b.keep"


async def test_schema_sparse_filter_precedes_limit(backends):
    vector, sparse, embedding = backends
    rag = SchemaMetadataRAG(vector, sparse, embedding, RRFFusion(alpha=0))
    docs = [
        SchemaDocument(f"noise{i}", "other", table_name="noise", embedding_text="sales")
        for i in range(35)
    ]
    docs.append(SchemaDocument("right", "target", table_name="orders", embedding_text="sales"))
    await rag.index_schemas(docs)
    hits = await rag.find_relevant_tables("sales", top_k=1, db_id="target")
    assert len(hits) == 1
    assert hits[0].doc_id == "right"
    assert hits[0].sparse_rank == 1


async def test_bad_embedding_batch_preserves_existing_document(backends):
    vector, sparse, embedding = backends
    store = DocumentStore(vector, sparse, embedding)
    await store.index_document(Document("keep", content="original"))

    async def missing_vectors(texts):
        return SimpleNamespace(vectors=[])

    store._embedding = SimpleNamespace(embed=missing_vectors)
    with pytest.raises(ValueError, match="batch count/dimension mismatch"):
        await store.index_document(Document("keep", content="replacement"))
    assert store.stats() == {"lancedb_count": 1, "bm25_count": 1}
    assert sparse.search("documents", "original")[0]["doc_id"] == "keep#0"
