"""Build a real, synthetic read-only Schema index before any serving process starts."""

from pathlib import Path

from app.db.connections import ConnectionFactory, DatabaseType
from app.db.schema_extractor import SchemaExtractor
from app.harness.worker_release import ROOT, write_release
from app.knowledge.retrieval.bm25_index import BM25Index
from app.knowledge.retrieval.embedding import EmbeddingGenerator, MockEmbeddingProvider
from app.knowledge.retrieval.lancedb_store import LanceDBStore
from app.knowledge.retrieval.schema_rag import SchemaMetadataRAG
from app.rag.converters import snapshot_to_schema_docs


async def build_release(config, directory: Path):
    dense_path, sparse_path = directory / "lance", directory / "sparse.sqlite3"
    provider = MockEmbeddingProvider()
    embedding = EmbeddingGenerator(provider)
    dense = LanceDBStore(
        embedding.dimension,
        str(dense_path),
        embedding_id=(f"{type(provider).__name__}:{provider.model_name}:document-fulltext-v2"),
    )
    sparse = BM25Index(str(sparse_path))
    dense.init_namespaces()
    for namespace in ("schema_metadata", "metrics", "documents"):
        sparse.ensure_namespace(namespace)
    reader = {
        "host": "127.0.0.1",
        "port": config["port"],
        "user": "stage40_reader",
        "password": "stage40-disposable-reader",
        "database": "de_stage40_fixture",
    }
    connection = await ConnectionFactory.create(DatabaseType.MYSQL, reader, "release-builder")
    try:
        snapshot = await SchemaExtractor.extract(connection, DatabaseType.MYSQL, "fixture")
        rag = SchemaMetadataRAG(dense, sparse, embedding)
        await rag.index_schemas(snapshot_to_schema_docs(snapshot, "fixture"))
    finally:
        await ConnectionFactory.aclose("release-builder")
        sparse.close()
        dense.close()
    files = {path for path in dense_path.rglob("*") if path.is_file()}
    files.update((ROOT / "app/config").rglob("*.yml"))
    files.update((ROOT / "app/workflow/plans").rglob("*.yml"))
    files.add(sparse_path)
    bundle = directory / "release.json"
    write_release(
        [{"db_id": "fixture", "db_type": "mysql", "config": reader}],
        sorted(files),
        bundle,
        index_roots=[dense_path],
    )
    return {
        "SHARED_WORKER_RELEASE": str(bundle),
        "RAG_LANCEDB_DIR": str(dense_path),
        "RAG_BM25_PATH": str(sparse_path),
        "LLM_AUDIT_PATH": str(directory / "shared-audit.sqlite3"),
    }
