"""Real SQLite/DuckDB + LanceDB/FTS5 refresh provenance and failure boundaries."""

import asyncio
import sqlite3
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.api import create_app
from app.api.schema import refresh_schema
from app.db.connections import ConnectionFactory, DatabaseType
from app.db.schema_extractor import SchemaExtractor
from app.knowledge.retrieval.bm25_index import BM25Index
from app.knowledge.retrieval.embedding import EmbeddingGenerator, MockEmbeddingProvider
from app.knowledge.retrieval.lancedb_store import LanceDBStore
from app.knowledge.retrieval.schema_rag import SchemaMetadataRAG
from app.models.rag_schema import SchemaDocument
from tests.api_client import authenticated_client


@pytest.fixture
async def environment(tmp_path, monkeypatch):
    import app.rag

    for name in ("_connections", "_infos", "_offload_connections", "_mysql_pools"):
        monkeypatch.setattr(ConnectionFactory, name, {})
    monkeypatch.setattr(SchemaExtractor, "_cache", {})
    vector = LanceDBStore(16, str(tmp_path / "lance"))
    sparse = BM25Index()
    embedding = EmbeddingGenerator(MockEmbeddingProvider(16))
    rag = SchemaMetadataRAG(vector, sparse, embedding)
    await rag.index_schemas([
        SchemaDocument("target.old", "target", embedding_text="sentinel"),
        SchemaDocument("other.keep", "other", embedding_text="untouched"),
    ])
    monkeypatch.setattr(app.rag, "get_schema_rag", lambda: rag)
    yield rag, embedding, sparse
    await ConnectionFactory.aclose_all()
    sparse.close()
    vector.close()


async def database(db_type, name="target"):
    conn = await ConnectionFactory.create(db_type, {}, name)
    conn.execute("CREATE TABLE marker(id INT)")
    return conn


@pytest.mark.parametrize("db_type", [DatabaseType.SQLITE, DatabaseType.DUCKDB])
@pytest.mark.parametrize("change", ["ddl", "reconnect", "disconnect"])
async def test_refresh_rejects_source_changed_during_embedding(
    environment, monkeypatch, db_type, change,
):
    rag, embedding, sparse = environment
    conn = await database(db_type)
    original_embed = embedding.embed
    changed = False

    async def change_before_embedding(texts):
        nonlocal changed
        if not changed:
            changed = True
            if change == "ddl":
                conn.execute("ALTER TABLE marker ADD COLUMN added TEXT")
            elif change == "reconnect":
                await database(db_type)
            else:
                await ConnectionFactory.aclose("target")
        return await original_embed(texts)

    monkeypatch.setattr(embedding, "embed", change_before_embedding)
    with pytest.raises(HTTPException) as error:
        await refresh_schema("target")
    assert error.value.status_code == 409
    assert error.value.detail["code"] == "SCHEMA_REFRESH_CONFLICT"
    assert rag.stats() == {"lancedb_count": 2, "bm25_count": 2}
    assert sparse.search("schema_metadata", "sentinel")[0]["doc_id"] == "target.old"
    assert sparse.search("schema_metadata", "untouched")[0]["doc_id"] == "other.keep"
    if change == "disconnect":
        await database(db_type)
    result = await refresh_schema("target")
    assert result["rag_indexed"] is True
    assert sparse.search("schema_metadata", "sentinel") == []


async def test_row_count_change_during_embedding_is_not_a_schema_conflict(environment, monkeypatch):
    _, embedding, _ = environment
    conn = await database(DatabaseType.SQLITE)
    original_embed = embedding.embed

    async def insert_data(texts):
        conn.execute("INSERT INTO marker VALUES (1)")
        return await original_embed(texts)

    monkeypatch.setattr(embedding, "embed", insert_data)
    assert (await refresh_schema("target"))["rag_indexed"] is True


@pytest.mark.parametrize("failure", ["exception", "bad_vectors", "cancel"])
async def test_embedding_failure_never_removes_previous_index(environment, monkeypatch, failure):
    rag, embedding, sparse = environment
    await database(DatabaseType.SQLITE)
    original_embed = embedding.embed

    async def fail(texts):
        if failure == "cancel":
            raise asyncio.CancelledError()
        if failure == "exception":
            raise RuntimeError("private-provider-fixture")
        result = await original_embed(texts)
        result.vectors.clear()
        return result

    monkeypatch.setattr(embedding, "embed", fail)
    if failure == "cancel":
        with pytest.raises(asyncio.CancelledError):
            await refresh_schema("target")
    else:
        result = await refresh_schema("target")
        assert result["rag_indexed"] is False and result["warnings"]
        assert "private-provider-fixture" not in str(result)
    assert rag.stats() == {"lancedb_count": 2, "bm25_count": 2}
    assert sparse.search("schema_metadata", "sentinel")


async def test_same_database_refreshes_capture_and_publish_in_order(environment, monkeypatch):
    rag, embedding, sparse = environment
    conn = await database(DatabaseType.DUCKDB)
    entered, resume, second_started = asyncio.Event(), asyncio.Event(), asyncio.Event()
    original = embedding.embed
    first = True

    async def pause_first(texts):
        nonlocal first
        if first:
            first = False
            entered.set()
            await resume.wait()
        return await original(texts)

    monkeypatch.setattr(embedding, "embed", pause_first)
    old = asyncio.create_task(refresh_schema("target"))
    await asyncio.wait_for(entered.wait(), 10)
    conn.execute("ALTER TABLE marker ADD COLUMN added TEXT")

    async def second():
        second_started.set()
        return await refresh_schema("target")

    new = asyncio.create_task(second())
    await asyncio.wait_for(second_started.wait(), 10)
    resume.set()
    with pytest.raises(HTTPException) as conflict:
        await old
    assert conflict.value.status_code == 409
    assert (await asyncio.wait_for(new, 10))["rag_indexed"] is True
    assert rag.stats() == {"lancedb_count": 4, "bm25_count": 4}
    assert sparse.search("schema_metadata", "added")
    assert sparse.search("schema_metadata", "sentinel") == []


async def test_other_database_can_refresh_while_first_waits_for_embedding(environment, monkeypatch):
    _, embedding, _ = environment
    await database(DatabaseType.SQLITE)
    await database(DatabaseType.SQLITE, "other")
    entered, resume = asyncio.Event(), asyncio.Event()
    original = embedding.embed
    first = True

    async def pause_first(texts):
        nonlocal first
        if first:
            first = False
            entered.set()
            await resume.wait()
        return await original(texts)

    monkeypatch.setattr(embedding, "embed", pause_first)
    task = asyncio.create_task(refresh_schema("target"))
    try:
        await asyncio.wait_for(entered.wait(), 10)
        assert (await asyncio.wait_for(refresh_schema("other"), 10))["rag_indexed"] is True
    finally:
        resume.set()
    assert (await task)["rag_indexed"] is True


async def test_cancelled_refresh_releases_gate_for_next_request(environment, monkeypatch):
    _, embedding, _ = environment
    await database(DatabaseType.SQLITE)
    entered = asyncio.Event()
    original = embedding.embed

    async def block(texts):
        entered.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(embedding, "embed", block)
    task = asyncio.create_task(refresh_schema("target"))
    await asyncio.wait_for(entered.wait(), 10)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    monkeypatch.setattr(embedding, "embed", original)
    assert (await asyncio.wait_for(refresh_schema("target"), 10))["rag_indexed"] is True


@pytest.mark.parametrize("changes", [False, True])
async def test_empty_snapshot_checks_source_before_deleting(environment, monkeypatch, changes):
    rag, _, sparse = environment
    await ConnectionFactory.create(DatabaseType.SQLITE, {}, "target")
    original = SchemaExtractor.extract
    changed = False

    async def add_after_capture(connection, *args, **kwargs):
        nonlocal changed
        snapshot = await original(connection, *args, **kwargs)
        if changes and not changed:
            changed = True
            connection.execute("CREATE TABLE added(id INT)")
        return snapshot

    monkeypatch.setattr(SchemaExtractor, "extract", staticmethod(add_after_capture))
    if changes:
        with pytest.raises(HTTPException) as conflict:
            await refresh_schema("target")
        assert conflict.value.status_code == 409
        assert rag.stats() == {"lancedb_count": 2, "bm25_count": 2}
        assert sparse.search("schema_metadata", "sentinel")
    else:
        result = await refresh_schema("target")
        assert result["rag_indexed"] is True and result["tables"] == []
        assert rag.stats() == {"lancedb_count": 1, "bm25_count": 1}
        assert sparse.search("schema_metadata", "untouched")


@pytest.mark.parametrize("empty", [False, True])
async def test_index_callback_failure_precedes_any_replacement(environment, empty):
    rag, _, sparse = environment

    async def reject():
        raise ValueError("synthetic source mismatch")

    docs = [] if empty else [SchemaDocument("target.new", "target", embedding_text="replacement")]
    with pytest.raises(ValueError, match="source mismatch"):
        await rag.index_schemas(docs, replace_db_id="target", before_replace=reject)
    assert rag.stats() == {"lancedb_count": 2, "bm25_count": 2}
    assert sparse.search("schema_metadata", "sentinel")


@pytest.mark.parametrize("entry", ["startup", "background"])
async def test_automatic_indexing_uses_same_source_guard(environment, monkeypatch, entry):
    from app import background
    from app.api.schema import _schedule_background_schema_indexing
    from app.rag.engine import _index_all_connected_schemas

    rag, embedding, sparse = environment
    conn = await database(DatabaseType.SQLITE)
    original = embedding.embed

    async def ddl(texts):
        conn.execute("ALTER TABLE marker ADD COLUMN changed TEXT")
        return await original(texts)

    monkeypatch.setattr(embedding, "embed", ddl)
    if entry == "startup":
        await _index_all_connected_schemas(SimpleNamespace(schema_rag=rag))
    else:
        tasks = []

        def capture(coroutine):
            tasks.append(asyncio.create_task(coroutine))

        monkeypatch.setattr(background, "start_background", capture)
        _schedule_background_schema_indexing("target")
        await asyncio.gather(*tasks)
    assert rag.stats() == {"lancedb_count": 2, "bm25_count": 2}
    assert sparse.search("schema_metadata", "sentinel")


@pytest.mark.parametrize("db_type", [DatabaseType.SQLITE, DatabaseType.DUCKDB])
async def test_http_refresh_conflict_has_stable_json_contract(environment, monkeypatch, db_type):
    _, embedding, _ = environment
    conn = await database(db_type)
    original = embedding.embed

    async def ddl(texts):
        conn.execute("ALTER TABLE marker ADD COLUMN changed TEXT")
        return await original(texts)

    monkeypatch.setattr(embedding, "embed", ddl)
    response = authenticated_client(create_app()).post("/api/v1/schema/target/refresh")
    assert response.status_code == 409
    assert response.json() == {"detail": {
        "code": "SCHEMA_REFRESH_CONFLICT",
        "message": "Schema source changed during refresh; retry explicitly",
    }}


async def test_direct_replacements_serialize_embedding_and_publication(environment, monkeypatch):
    rag, embedding, sparse = environment
    entered, resume, second_started = asyncio.Event(), asyncio.Event(), asyncio.Event()
    original = embedding.embed
    calls = []

    async def pause_first(texts):
        calls.append(texts)
        if len(calls) == 1:
            entered.set()
            await resume.wait()
        return await original(texts)

    monkeypatch.setattr(embedding, "embed", pause_first)
    first = asyncio.create_task(rag.index_schemas(
        [SchemaDocument("target.first", "target", embedding_text="firstvalue")],
        replace_db_id="target",
    ))
    await asyncio.wait_for(entered.wait(), 10)

    async def second():
        second_started.set()
        return await rag.index_schemas(
            [SchemaDocument("target.last", "target", embedding_text="lastvalue")],
            replace_db_id="target",
        )

    last = asyncio.create_task(second())
    await asyncio.wait_for(second_started.wait(), 10)
    assert len(calls) == 1
    resume.set()
    assert await asyncio.gather(first, last) == [1, 1]
    assert sparse.search("schema_metadata", "firstvalue") == []
    assert sparse.search("schema_metadata", "lastvalue")[0]["doc_id"] == "target.last"


async def test_duckdb_use_during_embedding_preserves_old_index(environment, monkeypatch):
    rag, embedding, sparse = environment
    conn = await database(DatabaseType.DUCKDB)
    conn.execute("ATTACH ':memory:' AS other")
    conn.execute("CREATE TABLE other.main.marker(id INT)")
    original = embedding.embed

    async def change_catalog(texts):
        conn.execute("USE other")
        return await original(texts)

    monkeypatch.setattr(embedding, "embed", change_catalog)
    with pytest.raises(HTTPException) as conflict:
        await refresh_schema("target")
    assert conflict.value.status_code == 409
    assert rag.stats() == {"lancedb_count": 2, "bm25_count": 2}
    assert sparse.search("schema_metadata", "sentinel")


async def test_partial_backend_write_is_reported_as_not_indexed(environment, monkeypatch):
    rag, _, sparse = environment
    await database(DatabaseType.SQLITE)

    def fail(*args, **kwargs):
        raise RuntimeError("private-backend-fixture")

    monkeypatch.setattr(sparse, "index", fail)
    result = await refresh_schema("target")
    assert result["rag_indexed"] is False and result["warnings"]
    assert "private-backend-fixture" not in str(result)
    assert rag.stats() == {"lancedb_count": 2, "bm25_count": 2}
    assert "previous index restored" in str(result["warnings"])
    assert sparse.search("schema_metadata", "sentinel")[0]["doc_id"] == "target.old"


def raw_indexes(rag, sparse, db_id):
    return (
        sorted(rag._lancedb.snapshot_database(rag.NAMESPACE, db_id), key=lambda r: r["doc_id"]),
        sorted(sparse.snapshot_database(rag.NAMESPACE, db_id)),
    )


@pytest.mark.parametrize("backend,method", [
    ("dense", "delete"), ("dense", "insert"),
    ("sparse", "delete_database"), ("sparse", "index"),
])
@pytest.mark.parametrize("after_write", [False, True])
async def test_replacement_restores_exact_records_after_storage_failure(
    environment, monkeypatch, backend, method, after_write,
):
    from app.models.rag_schema import SchemaIndexWriteError

    rag, _, sparse = environment
    # More than the native search default limit; preserve original vectors/timestamps/tokens.
    await rag.index_schemas([
        SchemaDocument(f"target.extra{i}", "target", embedding_text=f"订单 检索 revenue{i}")
        for i in range(25)
    ], language="zh")
    before = raw_indexes(rag, sparse, "target")
    other = raw_indexes(rag, sparse, "other")
    storage = rag._lancedb if backend == "dense" else sparse
    original = getattr(storage, method)

    def fail(*args, **kwargs):
        if after_write:
            original(*args, **kwargs)
        raise RuntimeError("private-storage-fixture")

    with monkeypatch.context() as patch:
        patch.setattr(storage, method, fail)
        with pytest.raises(SchemaIndexWriteError, match="previous index restored") as error:
            await rag.index_schemas([
                SchemaDocument("target.new", "target", embedding_text="replacement"),
            ], replace_db_id="target")
        assert error.value.restored is True
    assert raw_indexes(rag, sparse, "target") == before
    assert raw_indexes(rag, sparse, "other") == other
    assert rag.unavailable_databases == ()
    assert (await rag.find_relevant_tables("sentinel", db_id="target"))[0].doc_id == "target.old"


@pytest.mark.parametrize("empty", [False, True])
async def test_empty_and_nonempty_replace_recover_strict_delete_failure(
    environment, monkeypatch, empty,
):
    from app.models.rag_schema import SchemaIndexWriteError

    rag, _, sparse = environment
    before = raw_indexes(rag, sparse, "target")

    def fail(*args, **kwargs):
        raise RuntimeError("private-delete-fixture")

    with monkeypatch.context() as patch:
        patch.setattr(sparse, "delete_database", fail)
        with pytest.raises(SchemaIndexWriteError, match="previous index restored"):
            await rag.index_schemas(
                [] if empty else [SchemaDocument("target.new", "target")], replace_db_id="target",
            )
    assert raw_indexes(rag, sparse, "target") == before


@pytest.mark.parametrize("backend", ["dense", "sparse"])
async def test_snapshot_failure_never_mutates_either_backend(environment, monkeypatch, backend):
    rag, _, sparse = environment
    before = raw_indexes(rag, sparse, "target")

    def fail(*args, **kwargs):
        raise RuntimeError("snapshot-failed")

    with monkeypatch.context() as patch:
        patch.setattr(rag._lancedb if backend == "dense" else sparse, "snapshot_database", fail)
        with pytest.raises(RuntimeError, match="snapshot-failed"):
            await rag.index_schemas(
                [SchemaDocument("target.new", "target")], replace_db_id="target",
            )
    assert raw_indexes(rag, sparse, "target") == before
    assert rag.unavailable_databases == ()


@pytest.mark.parametrize("failed_recovery", ["dense", "sparse", "both"])
async def test_failed_recovery_blocks_unsafe_reads_until_successful_explicit_refresh(
    environment, monkeypatch, failed_recovery,
):
    from app.models.rag_schema import SchemaIndexWriteError

    rag, _, sparse = environment
    await database(DatabaseType.SQLITE)
    other = raw_indexes(rag, sparse, "other")
    attempted = []

    def fail_write(*args, **kwargs):
        raise RuntimeError("private-index-fixture")

    def restoration(backend, original):
        def restore(*args, **kwargs):
            attempted.append(backend)
            if failed_recovery in (backend, "both"):
                raise RuntimeError("private-recovery-fixture")
            return original(*args, **kwargs)
        return restore

    with monkeypatch.context() as patch:
        patch.setattr(sparse, "index", fail_write)
        patch.setattr(rag._lancedb, "restore_database", restoration(
            "dense", rag._lancedb.restore_database,
        ))
        patch.setattr(sparse, "restore_database", restoration("sparse", sparse.restore_database))
        response = await refresh_schema("target")
        assert response["rag_indexed"] is False
        assert "recovery failed" in str(response["warnings"])
        assert "private-" not in str(response)
        assert attempted == ["dense", "sparse"]
    assert rag.unavailable_databases == ("target",)
    for db_id in ("target", None, ""):
        with pytest.raises(SchemaIndexWriteError, match="retrieval unavailable"):
            await rag.find_relevant_tables("sentinel", db_id=db_id)
    assert (await rag.find_relevant_tables("untouched", db_id="other"))[0].doc_id == "other.keep"
    assert raw_indexes(rag, sparse, "other") == other
    # Upserts or successful refresh of another DB cannot certify this DB as recovered.
    await rag.index_schema(SchemaDocument("target.extra", "target"))
    await rag.index_schemas([SchemaDocument("other.keep", "other")], replace_db_id="other")
    assert rag.unavailable_databases == ("target",)
    assert (await refresh_schema("target"))["rag_indexed"] is True
    assert rag.unavailable_databases == ()
    assert await rag.find_relevant_tables("marker", db_id="target")


async def test_reader_rechecks_recovery_state_after_embedding_wait(environment, monkeypatch):
    from app.models.rag_schema import SchemaIndexWriteError

    rag, embedding, sparse = environment
    original = embedding.embed_single

    async def change_while_waiting(query):
        def fail(*args, **kwargs):
            raise RuntimeError("fixture")

        with monkeypatch.context() as patch:
            patch.setattr(sparse, "index", fail)
            patch.setattr(sparse, "restore_database", fail)
            with pytest.raises(SchemaIndexWriteError):
                await rag.index_schemas([SchemaDocument("target.new", "target")],
                                        replace_db_id="target")
        return await original(query)

    with monkeypatch.context() as patch:
        patch.setattr(embedding, "embed_single", change_while_waiting)
        with pytest.raises(SchemaIndexWriteError, match="retrieval unavailable"):
            await rag.find_relevant_tables("sentinel", db_id="target")


@pytest.mark.parametrize("backend", ["dense", "sparse"])
async def test_replacement_rejects_foreign_document_id_before_mutation(
    environment, monkeypatch, backend,
):
    rag, _, sparse = environment
    # Make a collision present in only one backend, as can happen after a legacy partial write.
    if backend == "dense":
        sparse.delete(rag.NAMESPACE, "other.keep")
    else:
        rag._lancedb.delete(rag.NAMESPACE, "db_id = 'other'")
    target, other = raw_indexes(rag, sparse, "target"), raw_indexes(rag, sparse, "other")
    with pytest.raises(ValueError, match="another database"):
        await rag.index_schemas([SchemaDocument("other.keep", "target")], replace_db_id="target")
    assert raw_indexes(rag, sparse, "target") == target
    assert raw_indexes(rag, sparse, "other") == other


async def test_replacement_rejects_duplicate_ids_before_mutation(environment):
    rag, _, sparse = environment
    before = raw_indexes(rag, sparse, "target")
    with pytest.raises(ValueError, match="must be unique"):
        await rag.index_schemas([
            SchemaDocument("target.same", "target", embedding_text="first"),
            SchemaDocument("target.same", "target", embedding_text="second"),
        ], replace_db_id="target")
    assert raw_indexes(rag, sparse, "target") == before


async def test_recovery_handles_uncommitted_sparse_inserts(environment, monkeypatch):
    from app.models.rag_schema import SchemaIndexWriteError

    rag, _, sparse = environment
    before = raw_indexes(rag, sparse, "target")

    def partially_insert(namespace, docs, **kwargs):
        sparse.connect().execute(
            "INSERT INTO fts5_schema_metadata(doc_id, text, metadata_json) VALUES (?, ?, ?)",
            (docs[0]["doc_id"], "partiallywritten", '{"db_id":"target"}'),
        )
        raise RuntimeError("partial-uncommitted-write")

    with monkeypatch.context() as patch:
        patch.setattr(sparse, "index", partially_insert)
        with pytest.raises(SchemaIndexWriteError, match="previous index restored"):
            await rag.index_schemas([SchemaDocument("target.new", "target")],
                                    replace_db_id="target")
    assert raw_indexes(rag, sparse, "target") == before
    assert not sparse.connect().in_transaction


@pytest.mark.parametrize("repair", ["replace_empty", "remove", "clear"])
async def test_repair_only_clears_unavailable_after_success(environment, monkeypatch, repair):
    from app.models.rag_schema import SchemaIndexWriteError

    rag, _, sparse = environment

    def fail(*args, **kwargs):
        raise RuntimeError("fixture")

    with monkeypatch.context() as patch:
        patch.setattr(sparse, "index", fail)
        patch.setattr(sparse, "restore_database", fail)
        with pytest.raises(SchemaIndexWriteError):
            await rag.index_schemas([SchemaDocument("target.new", "target")],
                                    replace_db_id="target")
    # A second failed replacement cannot certify a previously uncertain snapshot as healthy.
    with monkeypatch.context() as patch:
        patch.setattr(sparse, "index", fail)
        with pytest.raises(SchemaIndexWriteError) as error:
            await rag.index_schemas([SchemaDocument("target.new", "target")],
                                    replace_db_id="target")
        assert error.value.restored is False
    assert rag.unavailable_databases == ("target",)
    if repair == "replace_empty":
        await rag.index_schemas([], replace_db_id="target")
    elif repair == "remove":
        rag.remove_database("target")
    else:
        rag.clear()
    assert rag.unavailable_databases == ()
    assert raw_indexes(rag, sparse, "target") == ([], [])


async def test_quoted_database_id_recovery_and_restore_scope_validation(environment, monkeypatch):
    from app.models.rag_schema import SchemaIndexWriteError

    rag, _, sparse = environment
    db_id = "quoted' OR true --"
    await rag.index_schemas([SchemaDocument("quote.old", db_id, embedding_text="quoted sentinel")])
    before, other = raw_indexes(rag, sparse, db_id), raw_indexes(rag, sparse, "other")

    def fail(*args, **kwargs):
        raise RuntimeError("fixture")

    with monkeypatch.context() as patch:
        patch.setattr(sparse, "index", fail)
        with pytest.raises(SchemaIndexWriteError, match="previous index restored"):
            await rag.index_schemas([SchemaDocument("quote.new", db_id)], replace_db_id=db_id)
    assert raw_indexes(rag, sparse, db_id) == before
    assert raw_indexes(rag, sparse, "other") == other
    with pytest.raises(ValueError, match="specified database"):
        rag._lancedb.restore_database(rag.NAMESPACE, "other", before[0])
    with pytest.raises(ValueError, match="specified database"):
        sparse.restore_database(rag.NAMESPACE, "other", before[1])
    assert raw_indexes(rag, sparse, "other") == other


async def test_real_fts_delete_denial_is_not_reported_as_success(environment):
    rag, _, sparse = environment
    await database(DatabaseType.SQLITE)
    before = raw_indexes(rag, sparse, "target")

    def deny_delete(action, table, *args):
        if action == sqlite3.SQLITE_DELETE and table.startswith("fts5_schema_metadata"):
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK

    conn = sparse.connect()
    conn.set_authorizer(deny_delete)
    try:
        result = await refresh_schema("target")
        assert result["rag_indexed"] is False
        assert "recovery failed" in str(result["warnings"])
        assert rag.unavailable_databases == ("target",)
    finally:
        conn.set_authorizer(None)
    assert raw_indexes(rag, sparse, "target") == before
    assert (await refresh_schema("target"))["rag_indexed"] is True
    assert rag.unavailable_databases == ()


async def test_index_refresher_records_success_only_after_checked_publication(
    environment, monkeypatch,
):
    from app.rag.index_refresher import IndexRefresher

    rag, embedding, sparse = environment
    conn = await database(DatabaseType.DUCKDB)
    original = embedding.embed

    async def ddl(texts):
        conn.execute("ALTER TABLE marker ADD COLUMN changed TEXT")
        return await original(texts)

    monkeypatch.setattr(embedding, "embed", ddl)
    refresher = IndexRefresher(rag)
    assert await refresher.refresh("target") is False
    status = refresher.get_refresh_status()
    assert status["tracked_databases"] == 0
    assert status["last_refresh_time"] == {}
    assert status["pending_count"] == 0
    assert sparse.search("schema_metadata", "sentinel")
    monkeypatch.setattr(embedding, "embed", original)
    assert await refresher.refresh("target") is True
    assert refresher.get_refresh_status()["tracked_databases"] == 1
    assert "target" in refresher.get_refresh_status()["last_refresh_time"]


async def test_missing_connection_refresh_is_404(environment):
    response = authenticated_client(create_app()).post("/api/v1/schema/missing/refresh")
    assert response.status_code == 404


async def test_internal_extraction_key_error_is_500_not_missing_connection(
    environment, monkeypatch,
):
    await database(DatabaseType.SQLITE)

    async def fail(*args, **kwargs):
        raise KeyError("private-extraction-fixture")

    monkeypatch.setattr(SchemaExtractor, "extract", staticmethod(fail))
    response = authenticated_client(create_app()).post("/api/v1/schema/target/refresh")
    assert response.status_code == 500
    assert response.json() == {"detail": "Schema extraction failed"}
