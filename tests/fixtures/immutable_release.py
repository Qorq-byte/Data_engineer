"""Build a real, synthetic read-only Schema index before any serving process starts."""

from pathlib import Path

import yaml

from app.auth.tenant import tenant_scope
from app.db.connections import ConnectionFactory, DatabaseType
from app.db.schema_extractor import SchemaExtractor
from app.harness.worker_release import ROOT, write_release
from app.knowledge.retrieval.bm25_index import BM25Index
from app.knowledge.retrieval.embedding import EmbeddingGenerator, MockEmbeddingProvider
from app.knowledge.retrieval.lancedb_store import LanceDBStore, tenant_namespace
from app.knowledge.retrieval.schema_rag import SchemaMetadataRAG
from app.rag.converters import snapshot_to_schema_docs


async def build_release(
    config, directory: Path, *, with_metrics: bool = False, code_root: Path = ROOT,
    tenant_profiles=None, subagent_catalog: Path | None = None,
):
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
    reader = {
        "host": "127.0.0.1",
        "port": config["port"],
        "user": "stage40_reader",
        "password": "stage40-disposable-reader",
        "database": "de_stage40_fixture",
    }
    default_profiles = [{"db_id": "fixture", "db_type": "mysql", "config": reader}]
    groups = {"1": default_profiles, **(tenant_profiles or {})}
    try:
        rag = SchemaMetadataRAG(dense, sparse, embedding)
        for tenant_id, profiles in groups.items():
            with tenant_scope(int(tenant_id)):
                for namespace in ("schema_metadata", "metrics", "documents"):
                    sparse.ensure_namespace(tenant_namespace(namespace))
                for profile in profiles:
                    db_type, target = DatabaseType(profile["db_type"]), profile["config"]
                    connection = await ConnectionFactory.create(db_type, target, "release-builder")
                    try:
                        snapshot = await SchemaExtractor.extract(
                            connection, db_type, target["database"],
                        )
                        await rag.index_schemas(snapshot_to_schema_docs(snapshot, profile["db_id"]))
                    finally:
                        await ConnectionFactory.aclose("release-builder")
    finally:
        sparse.close()
        dense.close()
    files = {path for path in dense_path.rglob("*") if path.is_file()}
    files.update((code_root / "app/config").rglob("*.yml"))
    files.update((code_root / "app/workflow/plans").rglob("*.yml"))
    files.add(sparse_path)
    catalog = None
    if with_metrics:
        catalog = directory / "metrics.yml"
        bindings = []
        for tenant in groups:
            lookup = subagent_catalog is not None and tenant != "1"
            dimensions = ["status", "branch"] if lookup else ["status"]
            measures = (("order_count", "*", "count"), ("id_sum", "id", "sum"))
            metrics = [
                {"name": name, "table": "orders", "column": column, "aggregation": aggregation,
                 "dimensions": list(dimensions), **({"date_column": "booked_on"} if lookup else {})}
                for name, column, aggregation in measures
            ]
            binding = {"domain": "ecommerce", "database": "fixture", "tenant_id": int(tenant),
                       "metrics": metrics}
            if lookup:
                binding["lookup"] = {
                    "fact_table": "orders", "fact_key": "branch_id",
                    "table": "branches", "key": "id", "dimensions": {"branch": "label"},
                }
            bindings.append(binding)
        data = {"version": "shared-fixture-v1", "bindings": bindings}
        if subagent_catalog is not None:
            data["format"] = "metric-catalog-v2"
        catalog.write_text(yaml.safe_dump(data), encoding="utf-8")
    bundle = directory / "release.json"
    write_release(
        groups if tenant_profiles else default_profiles,
        sorted(files),
        bundle,
        index_roots=[dense_path],
        metric_catalog=catalog,
        subagent_catalog=subagent_catalog,
    )
    return {
        "SHARED_WORKER_RELEASE": str(bundle),
        "RAG_LANCEDB_DIR": str(dense_path),
        "RAG_BM25_PATH": str(sparse_path),
        "LLM_AUDIT_PATH": str(directory / "shared-audit.sqlite3"),
        **({"METRIC_CATALOG_PATH": str(catalog)} if catalog is not None else {}),
    }
