"""Real persistent indexes must be verified on each engine initialization."""

import asyncio
import sqlite3
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.api import create_app
from app.api.schema import refresh_schema
from app.config.settings import settings
from app.db.connections import ConnectionFactory, DatabaseType
from app.db.schema_extractor import SchemaExtractor
from app.knowledge.retrieval.bm25_index import BM25Index
from app.knowledge.retrieval.embedding import EmbeddingGenerator, MockEmbeddingProvider
from app.knowledge.retrieval.lancedb_store import LanceDBStore
from app.knowledge.retrieval.schema_rag import SchemaMetadataRAG
from app.models.rag_schema import SchemaDocument
from app.rag import engine
from tests.api_client import authenticated_client


@pytest.fixture
async def persistent(tmp_path, monkeypatch):
    for name in ("_connections", "_infos", "_offload_connections", "_mysql_pools"):
        monkeypatch.setattr(ConnectionFactory, name, {})
    monkeypatch.setattr(SchemaExtractor, "_cache", {})
    monkeypatch.setattr(engine, "_engine", None)
    provider = MockEmbeddingProvider(16)
    monkeypatch.setattr(engine, "_create_embedding_provider", lambda: provider)
    monkeypatch.setattr(settings, "rag_lancedb_dir", str(tmp_path / "lance"))
    monkeypatch.setattr(settings, "rag_bm25_path", str(tmp_path / "sparse.db"))
    vector = LanceDBStore(
        16, settings.rag_lancedb_dir,
        embedding_id=f"MockEmbeddingProvider:{provider.model_name}:document-fulltext-v2",
    )
    sparse = BM25Index(settings.rag_bm25_path)
    rag = SchemaMetadataRAG(vector, sparse, EmbeddingGenerator(provider))
    await rag.index_schemas([
        SchemaDocument("target.old", "target", table_name="old", embedding_text="sentinel"),
        SchemaDocument("orphan.old", "orphan", table_name="old", embedding_text="orphan sentinel"),
    ])
    sparse.close()
    vector.close()
    yield provider
    engine.close_rag_engine()
    await ConnectionFactory.aclose_all()


async def connect(db_id="target"):
    conn = await ConnectionFactory.create(DatabaseType.SQLITE, {}, db_id)
    conn.execute("CREATE TABLE marker(id INT)")
    return conn


async def test_persisted_orphan_cannot_be_read_after_restart(persistent):
    rag = (await engine.initialize_rag_engine()).schema_rag
    with pytest.raises(RuntimeError, match="refresh"):
        await rag.find_relevant_tables("sentinel", db_id="orphan")


async def test_candidate_is_not_published_during_startup(persistent, monkeypatch):
    entered, release = asyncio.Event(), asyncio.Event()
    original = engine._index_all_connected_schemas

    async def hold(candidate):
        entered.set()
        await release.wait()
        return await original(candidate)

    monkeypatch.setattr(engine, "_index_all_connected_schemas", hold)
    task = asyncio.create_task(engine.initialize_rag_engine())
    try:
        await asyncio.wait_for(entered.wait(), 10)
        with pytest.raises(RuntimeError, match="not initialized"):
            engine.get_rag_engine()
    finally:
        release.set()
        await asyncio.wait_for(task, 10)


async def test_connected_source_verified_but_orphan_stays_blocked(persistent):
    await connect()
    current = await engine.initialize_rag_engine()
    rag = current.schema_rag
    assert current.startup_schema_results == {"target": True}
    assert rag.refresh_required_databases == ("orphan",)
    assert await rag.find_relevant_tables("marker", db_id="target")
    for db_id in (None, "", "orphan"):
        with pytest.raises(RuntimeError, match="source refresh"):
            await rag.find_relevant_tables("sentinel", db_id=db_id)
    # A low-level upsert cannot certify pre-existing contents.
    await rag.index_schema(SchemaDocument("orphan.extra", "orphan"))
    assert rag.refresh_required_databases == ("orphan",)
    await connect("orphan")
    assert (await refresh_schema("orphan"))["rag_indexed"] is True
    assert rag.refresh_required_databases == ()
    assert await rag.find_relevant_tables("marker")
    # Every new process/container must verify again, even after a clean previous run.
    engine.close_rag_engine()
    await ConnectionFactory.aclose("orphan")
    reopened = await engine.initialize_rag_engine()
    assert reopened.schema_rag.refresh_required_databases == ("orphan",)
    assert reopened.startup_schema_results == {"target": True}


async def test_startup_failure_is_recorded_other_sources_continue(persistent, monkeypatch, caplog):
    await connect()
    await connect("healthy")
    original = persistent.embed
    calls = 0

    async def fail_first(texts):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("private-provider-fixture")
        return await original(texts)

    monkeypatch.setattr(persistent, "embed", fail_first)
    current = await engine.initialize_rag_engine()
    assert current.startup_schema_results == {"target": False, "healthy": True}
    assert current.schema_rag.refresh_required_databases == ("orphan", "target")
    assert "Startup Schema index refresh failed" in caplog.text
    assert "private-provider-fixture" not in caplog.text
    with pytest.raises(RuntimeError, match="source refresh"):
        await current.schema_rag.find_relevant_tables("sentinel", db_id="target")
    assert await current.schema_rag.find_relevant_tables("marker", db_id="healthy")
    assert (await refresh_schema("target"))["rag_indexed"] is True
    # Historical startup result is deliberately not presented as current trust state.
    assert current.startup_schema_results["target"] is False
    assert current.schema_rag.refresh_required_databases == ("orphan",)


@pytest.mark.parametrize("phase", ["schemas", "glossary"])
async def test_startup_cancellation_closes_candidate_and_allows_retry(
    persistent, monkeypatch, phase,
):
    entered = asyncio.Event()
    candidates = []

    async def hold(candidate):
        candidates.append(candidate)
        entered.set()
        await asyncio.Event().wait()

    with monkeypatch.context() as patch:
        patch.setattr(engine, "_index_all_connected_schemas" if phase == "schemas"
                      else "_index_all_glossary_terms", hold)
        task = asyncio.create_task(engine.initialize_rag_engine())
        await asyncio.wait_for(entered.wait(), 10)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 10)
    assert candidates[0].bm25_index._conn is None
    assert candidates[0].lancedb_store._db is None
    with pytest.raises(RuntimeError, match="not initialized"):
        engine.get_rag_engine()
    assert await engine.initialize_rag_engine() is engine.get_rag_engine()


@pytest.mark.parametrize("component", ["SchemaMetadataRAG", "MetricRAG", "DocumentStore"])
async def test_partial_construction_failure_closes_both_handles(persistent, monkeypatch, component):
    original = getattr(engine, component)
    acquired = []

    def fail(**kwargs):
        original(**kwargs)
        acquired.extend([kwargs["lancedb_store"], kwargs["bm25_index"]])
        raise ValueError("construction-fixture")

    with monkeypatch.context() as patch:
        patch.setattr(engine, component, fail)
        with pytest.raises(ValueError, match="construction-fixture"):
            await engine.initialize_rag_engine()
    assert acquired[0]._db is None and acquired[1]._conn is None
    assert engine._engine is None
    assert await engine.initialize_rag_engine()


async def test_concurrent_and_repeated_initialization_builds_once(persistent, monkeypatch):
    original = engine._build_rag_engine
    entered, release = asyncio.Event(), asyncio.Event()
    calls = 0

    async def hold():
        nonlocal calls
        calls += 1
        entered.set()
        await release.wait()
        return await original()

    monkeypatch.setattr(engine, "_build_rag_engine", hold)
    first = asyncio.create_task(engine.initialize_rag_engine())
    await asyncio.wait_for(entered.wait(), 10)
    second = asyncio.create_task(engine.initialize_rag_engine())
    release.set()
    one, two = await asyncio.wait_for(asyncio.gather(first, second), 10)
    assert one is two is await engine.initialize_rag_engine()
    assert calls == 1


async def test_union_of_backend_owners_is_complete_and_not_search_limited(persistent):
    # Open actual stores independently to simulate a previous process's partial writes.
    vector = LanceDBStore(16, settings.rag_lancedb_dir)
    sparse = BM25Index(settings.rag_bm25_path)
    rag = SchemaMetadataRAG(vector, sparse, EmbeddingGenerator(persistent))
    await rag.index_schemas([
        SchemaDocument(f"db{i}.old", f"db{i}", embedding_text="old") for i in range(25)
    ])
    sparse.delete(rag.NAMESPACE, "db0.old")
    vector.delete(rag.NAMESPACE, "db_id = 'db1'")
    sparse.close()
    vector.close()
    current = await engine.initialize_rag_engine()
    assert set(current.schema_rag.refresh_required_databases) == {
        "target", "orphan", *(f"db{i}" for i in range(25)),
    }


@pytest.mark.parametrize("backend", ["dense", "sparse"])
async def test_invalid_persisted_owner_fails_initialization_without_claiming_index(
    persistent, backend,
):
    if backend == "dense":
        vector = LanceDBStore(16, settings.rag_lancedb_dir)
        table = vector.ensure_namespace("schema_metadata")
        table.update(where="db_id = 'target'", values={"db_id": ""})
        vector.close()
    else:
        sparse = BM25Index(settings.rag_bm25_path)
        sparse.connect().execute(
            "UPDATE fts5_schema_metadata SET metadata_json = '{}' WHERE doc_id = 'target.old'",
        )
        sparse.connect().commit()
        sparse.close()
    with pytest.raises(ValueError, match="invalid database ownership"):
        await engine.initialize_rag_engine()
    assert engine._engine is None


async def test_reader_rechecks_startup_gate_after_embedding(persistent, monkeypatch):
    await connect()
    rag = (await engine.initialize_rag_engine()).schema_rag
    original = rag._embedding.embed_single

    async def mark_pending(text):
        rag.require_refresh({"target"})
        return await original(text)

    monkeypatch.setattr(rag._embedding, "embed_single", mark_pending)
    with pytest.raises(RuntimeError, match="source refresh"):
        await rag.find_relevant_tables("marker", db_id="target")


async def test_operator_status_and_public_readiness_have_distinct_scope(persistent, monkeypatch):
    from app.llm import factory

    await connect()
    current = await engine.initialize_rag_engine()
    app = create_app()
    app.state.initialized = True
    app.state.components = {"auth": True, "rag": True}
    monkeypatch.setattr(factory, "get_default_router", lambda: SimpleNamespace(mock_mode=False))
    public = TestClient(app)
    assert public.get("/api/v1/learning/rag/status").status_code == 401
    operator = authenticated_client(app)
    state = operator.get("/api/v1/learning/rag/status").json()
    assert state["status"] == "degraded"
    assert state["schema_index"]["refresh_required_databases"] == ["orphan"]
    assert state["schema_index"]["startup_schema_results"] == {"target": True}
    assert "ttl_minutes" in state and "last_refresh_time" in state
    ready = public.get("/health/ready")
    assert ready.status_code == 200  # RAG is optional; keyword linking can serve SQL.
    assert ready.json()["components"]["rag"] is False
    assert ready.json()["rag"]["refresh_required_database_count"] == 1
    assert "orphan" not in ready.text and "target" not in ready.text
    current.schema_rag.remove_database("orphan")
    assert public.get("/health/ready").json()["components"]["rag"] is True
    assert operator.get("/api/v1/learning/rag/status").json()["status"] == "ok"
    # Restoration failure is live state too, not a startup boolean.
    current.schema_rag._unavailable_databases.add("target")
    failed = operator.get("/api/v1/learning/rag/status").json()
    assert failed["schema_index"]["recovery_failed_databases"] == ["target"]
    assert public.get("/health/ready").json()["rag"]["recovery_failed_database_count"] == 1
    engine.close_rag_engine()
    assert public.get("/health/ready").json()["rag"]["status"] == "unavailable"
    assert operator.get("/api/v1/learning/rag/status").json() == {
        "status": "error", "message": "RAG engine unavailable",
    }


async def test_cleanup_error_does_not_mask_failure_or_skip_other_backend(persistent, monkeypatch):
    original_close = BM25Index.close
    original_vector_close = LanceDBStore.close
    closed = []

    def close_sparse(self):
        original_close(self)
        closed.append("sparse")
        raise RuntimeError("cleanup-fixture")

    def close_vector(self):
        original_vector_close(self)
        closed.append("dense")

    async def fail(candidate):
        raise ValueError("initialization-fixture")

    with monkeypatch.context() as patch:
        patch.setattr(engine, "_index_all_connected_schemas", fail)
        patch.setattr(BM25Index, "close", close_sparse)
        patch.setattr(LanceDBStore, "close", close_vector)
        with pytest.raises(ValueError, match="initialization-fixture"):
            await engine.initialize_rag_engine()
    assert closed == ["sparse", "dense"]
    assert engine._engine is None


async def test_malformed_sparse_metadata_prevents_publication(persistent):
    sparse = BM25Index(settings.rag_bm25_path)
    sparse.connect().execute(
        "UPDATE fts5_schema_metadata SET metadata_json = 'not-json' WHERE doc_id = 'target.old'",
    )
    sparse.connect().commit()
    sparse.close()
    with pytest.raises(sqlite3.OperationalError, match="malformed JSON"):
        await engine.initialize_rag_engine()
    assert engine._engine is None


async def test_restart_cannot_bypass_previous_recovery_failure(persistent, monkeypatch):
    from app.models.rag_schema import SchemaIndexWriteError

    await connect()
    current = await engine.initialize_rag_engine()

    def fail(*args, **kwargs):
        raise RuntimeError("storage-fixture")

    with monkeypatch.context() as patch:
        patch.setattr(current.bm25_index, "index", fail)
        patch.setattr(current.bm25_index, "restore_database", fail)
        with pytest.raises(SchemaIndexWriteError):
            await current.schema_rag.index_schemas([
                SchemaDocument("target.new", "target", embedding_text="untrusted"),
            ], replace_db_id="target")
    assert current.schema_rag.unavailable_databases == ("target",)
    engine.close_rag_engine()

    async def unavailable(texts):
        raise RuntimeError("embedding-fixture")

    with monkeypatch.context() as patch:
        patch.setattr(persistent, "embed", unavailable)
        reopened = await engine.initialize_rag_engine()
    assert reopened.schema_rag.unavailable_databases == ()  # Not a persistent failure journal.
    assert reopened.schema_rag.refresh_required_databases == ("orphan", "target")
    with pytest.raises(RuntimeError, match="source refresh"):
        await reopened.schema_rag.find_relevant_tables("untrusted", db_id="target")
    assert (await refresh_schema("target"))["rag_indexed"] is True
    assert "target" not in reopened.schema_rag.refresh_required_databases
