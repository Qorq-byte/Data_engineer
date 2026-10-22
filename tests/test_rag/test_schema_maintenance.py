"""Operator maintenance against real persistent LanceDB and SQLite FTS5."""

import asyncio
from datetime import UTC, datetime
from urllib.parse import quote

import httpx
import pytest

from app.api import create_app
from app.api.schema import refresh_schema
from app.auth.security import create_access_token
from app.db.connections import ConnectionFactory
from app.db.runtime import gate
from app.models.rag_document import Document
from app.models.rag_metric import MetricDocument
from app.models.rag_schema import SchemaDocument
from app.rag import engine
from tests.test_rag.test_engine_startup import connect
from tests.test_rag.test_engine_startup import persistent as persistent


@pytest.fixture
async def client(persistent):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app()), base_url="http://test",
        headers={"Authorization": f"Bearer {create_access_token(1, 'test')}"},
    ) as client:
        yield client


def snapshots(current, db_id):
    return (
        current.lancedb_store.snapshot_database("schema_metadata", db_id),
        current.bm25_index.snapshot_database("schema_metadata", db_id),
    )


async def test_operator_can_clear_only_persisted_orphan(client):
    await connect()
    current = await engine.initialize_rag_engine()
    healthy = snapshots(current, "target")
    response = await client.post("/api/v1/learning/rag/schema/orphan/clear")
    assert response.status_code == 200
    assert response.json()["removed"] == {"lancedb_count": 1, "bm25_count": 1}
    assert snapshots(current, "orphan") == ([], [])
    assert snapshots(current, "target") == healthy
    assert current.schema_rag.refresh_required_databases == ()


async def test_legacy_clear_cannot_ignore_database_argument(client):
    current = await engine.initialize_rag_engine()
    before = snapshots(current, "target"), snapshots(current, "orphan")
    response = await client.post("/api/v1/learning/rag/clear", json={"db_id": "orphan"})
    assert response.status_code == 422
    assert (snapshots(current, "target"), snapshots(current, "orphan")) == before


@pytest.mark.parametrize("body,query", [
    ({"scope": "typo"}, ""), ({"scope": None}, ""), ({"database_id": "orphan"}, ""),
    ({"scope": "schema", "db_id": None}, ""),
    (None, "?db_id=orphan"), (None, "?scope=schema"),
])
async def test_bulk_clear_rejects_ambiguous_parameters_before_mutation(client, body, query):
    current = await engine.initialize_rag_engine()
    before = snapshots(current, "target"), snapshots(current, "orphan")
    response = await client.post("/api/v1/learning/rag/clear" + query, json=body)
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "INVALID_RAG_CLEAR_REQUEST"
    assert (snapshots(current, "target"), snapshots(current, "orphan")) == before


@pytest.mark.parametrize("user,expected", [(None, 401), (2, 403)])
async def test_scoped_clear_requires_operator(client, user, expected):
    current = await engine.initialize_rag_engine()
    before = snapshots(current, "orphan")
    client.headers.pop("Authorization")
    if user is not None:
        client.headers["Authorization"] = f"Bearer {create_access_token(user, 'nonoperator')}"
    response = await client.post("/api/v1/learning/rag/schema/orphan/clear")
    assert response.status_code == expected
    assert snapshots(current, "orphan") == before


@pytest.mark.parametrize("single_backend", ["dense", "sparse", "both"])
async def test_scoped_counts_idempotence_and_metadata_invalidation(client, single_backend):
    current = await engine.initialize_rag_engine()
    if single_backend == "dense":
        current.bm25_index.delete("schema_metadata", "orphan.old")
    elif single_backend == "sparse":
        current.lancedb_store.delete("schema_metadata", "db_id = 'orphan'")
    refresher = current.index_refresher
    refresher._version_cache.update({"target": "keep", "orphan": "remove"})
    now = datetime.now(UTC)
    refresher._last_refresh.update({"target": now, "orphan": now})
    refresher._pending.add("target")
    history = dict(current.startup_schema_results)
    response = await client.post("/api/v1/learning/rag/schema/orphan/clear")
    assert response.status_code == 200
    assert response.json()["removed"] == {
        "lancedb_count": int(single_backend != "sparse"),
        "bm25_count": int(single_backend != "dense"),
    }
    assert refresher._version_cache == {"target": "keep"}
    assert refresher._last_refresh == {"target": now}
    assert refresher._pending == {"target"}
    assert current.startup_schema_results == history
    for db_id in ("orphan", "missing"):
        repeated = await client.post(f"/api/v1/learning/rag/schema/{db_id}/clear")
        assert repeated.status_code == 200
        assert repeated.json()["removed"] == {"lancedb_count": 0, "bm25_count": 0}


async def test_scoped_clear_preserves_other_namespaces_and_database_contents(client):
    connection = await connect()
    current = await engine.initialize_rag_engine()
    await current.metric_rag.index_metric(MetricDocument(
        "metric.keep", name="Revenue", embedding_text="revenue definition",
    ))
    await current.document_store.index_document(Document(
        "doc.keep", title="Guide", content="A preserved business guide.",
    ))
    dense_before = {
        name: current.lancedb_store.ensure_namespace(name).search().limit(None).to_list()
        for name in ("metrics", "documents")
    }
    sparse_before = {
        name: [tuple(row) for row in current.bm25_index.connect().execute(
            f"SELECT doc_id, text, metadata_json FROM fts5_{name}",
        ).fetchall()] for name in ("metrics", "documents")
    }
    response = await client.post("/api/v1/learning/rag/schema/orphan/clear")
    assert response.status_code == 200
    for name in ("metrics", "documents"):
        assert current.lancedb_store.ensure_namespace(name).search().limit(None).to_list() == (
            dense_before[name]
        )
        assert [tuple(row) for row in current.bm25_index.connect().execute(
            f"SELECT doc_id, text, metadata_json FROM fts5_{name}",
        ).fetchall()] == sparse_before[name]
    assert ConnectionFactory.get("target") is connection
    assert connection.execute("SELECT COUNT(*) FROM marker").fetchone()[0] == 0
    assert await current.schema_rag.find_relevant_tables("marker", db_id="target")


@pytest.mark.parametrize("failure", ["snapshot", "delete_before", "delete_after", "recovery"])
async def test_clear_failure_is_safe_visible_and_retryable(client, monkeypatch, failure):
    current = await engine.initialize_rag_engine()
    old_target, old_orphan = snapshots(current, "target"), snapshots(current, "orphan")
    refresher = current.index_refresher
    refresher._version_cache["orphan"] = "keep-on-failure"
    original_delete = current.bm25_index.delete_database

    def fail(*args, **kwargs):
        if failure == "delete_after":
            original_delete(*args, **kwargs)
        raise RuntimeError("private-backend-fixture")

    with monkeypatch.context() as patch:
        if failure == "snapshot":
            patch.setattr(current.bm25_index, "snapshot_database", fail)
        else:
            patch.setattr(current.bm25_index, "delete_database", fail)
        if failure == "recovery":
            patch.setattr(current.lancedb_store, "restore_database", fail)
        response = await client.post("/api/v1/learning/rag/schema/orphan/clear")
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "SCHEMA_INDEX_CLEAR_FAILED"
    assert response.json()["detail"]["restored"] == (
        None if failure == "snapshot" else failure != "recovery"
    )
    assert "private-backend-fixture" not in response.text
    assert snapshots(current, "target") == old_target
    if failure != "recovery":
        assert snapshots(current, "orphan") == old_orphan
    assert current.schema_rag.refresh_required_databases == ("orphan", "target")
    assert refresher._version_cache["orphan"] == "keep-on-failure"
    with pytest.raises(RuntimeError):
        await current.schema_rag.find_relevant_tables("sentinel", db_id="orphan")
    assert (await client.post("/api/v1/learning/rag/schema/orphan/clear")).status_code == 200
    assert current.schema_rag.refresh_required_databases == ("target",)
    assert current.schema_rag.unavailable_databases == ()
    assert "orphan" not in refresher._version_cache


async def test_unavailable_engine_is_fixed_503_and_empty_path_cannot_bulk_clear(client):
    response = await client.post("/api/v1/learning/rag/schema/orphan/clear")
    assert response.status_code == 503
    assert response.json() == {"detail": {
        "code": "RAG_UNAVAILABLE", "message": "RAG engine unavailable",
    }}
    current = await engine.initialize_rag_engine()
    before = snapshots(current, "orphan")
    assert (await client.post("/api/v1/learning/rag/schema//clear")).status_code == 404
    assert snapshots(current, "orphan") == before


async def test_quoted_id_and_restart_do_not_restore_cleared_orphan(client):
    await connect()
    current = await engine.initialize_rag_engine()
    db_id = "quoted' OR true --"
    await current.schema_rag.index_schema(SchemaDocument("quote.old", db_id))
    other = snapshots(current, "target")
    response = await client.post(f"/api/v1/learning/rag/schema/{quote(db_id, safe='')}/clear")
    assert response.status_code == 200
    assert response.json()["removed"] == {"lancedb_count": 1, "bm25_count": 1}
    assert snapshots(current, "target") == other
    assert (await client.post("/api/v1/learning/rag/schema/orphan/clear")).status_code == 200
    engine.close_rag_engine()
    reopened = await engine.initialize_rag_engine()
    assert reopened.schema_rag.refresh_required_databases == ()
    assert snapshots(reopened, "orphan") == ([], [])
    assert await reopened.schema_rag.find_relevant_tables("marker")


async def test_clear_connected_database_then_explicitly_refresh(client):
    await connect()
    current = await engine.initialize_rag_engine()
    response = await client.post("/api/v1/learning/rag/schema/target/clear")
    assert response.status_code == 200 and snapshots(current, "target") == ([], [])
    assert ConnectionFactory.get("target")
    rebuilt = await client.post("/api/v1/schema/target/refresh")
    assert rebuilt.status_code == 200 and rebuilt.json()["rag_indexed"] is True
    assert await current.schema_rag.find_relevant_tables("marker", db_id="target")


@pytest.mark.parametrize("cancel", [False, True])
async def test_clear_waits_for_earlier_full_refresh_and_can_cancel_without_mutation(
    client, monkeypatch, cancel,
):
    from app.db import schema_refresh

    await connect()
    current = await engine.initialize_rag_engine()
    before = snapshots(current, "target")
    entered, release, clear_entered = asyncio.Event(), asyncio.Event(), asyncio.Event()
    original_embed = current.embedding_generator.embed
    original_clear = schema_refresh.clear_database_schema_index

    async def hold_embedding(texts):
        entered.set()
        await release.wait()
        return await original_embed(texts)

    async def announce_clear(*args, **kwargs):
        clear_entered.set()
        return await original_clear(*args, **kwargs)

    monkeypatch.setattr(current.embedding_generator, "embed", hold_embedding)
    monkeypatch.setattr(schema_refresh, "clear_database_schema_index", announce_clear)
    refresh = asyncio.create_task(refresh_schema("target"))
    await asyncio.wait_for(entered.wait(), 10)
    clear = asyncio.create_task(client.post("/api/v1/learning/rag/schema/target/clear"))
    try:
        await asyncio.wait_for(clear_entered.wait(), 10)
        assert not clear.done()
        if cancel:
            clear.cancel()
            with pytest.raises(asyncio.CancelledError):
                await clear
            assert snapshots(current, "target") == before
    finally:
        release.set()
        assert (await asyncio.wait_for(refresh, 10))["rag_indexed"] is True
    if not cancel:
        assert (await asyncio.wait_for(clear, 10)).status_code == 200
        assert snapshots(current, "target") == ([], [])
    else:
        assert snapshots(current, "target")[0]


async def test_new_refresh_after_clear_can_rebuild_without_deadlock(client, monkeypatch):
    await connect()
    current = await engine.initialize_rag_engine()
    clear_entered, refresh_entered = asyncio.Event(), asyncio.Event()
    original_clear = current.schema_rag.clear_database

    async def announce_clear(db_id):
        clear_entered.set()
        return await original_clear(db_id)

    async def refresh_later():
        refresh_entered.set()
        return await refresh_schema("target")

    monkeypatch.setattr(current.schema_rag, "clear_database", announce_clear)
    async with gate(("schema-rag-replace", id(current.schema_rag), "target")):
        clear = asyncio.create_task(client.post("/api/v1/learning/rag/schema/target/clear"))
        await asyncio.wait_for(clear_entered.wait(), 10)
        refresh = asyncio.create_task(refresh_later())
        await asyncio.wait_for(refresh_entered.wait(), 10)
        assert not clear.done() and not refresh.done()
    assert (await asyncio.wait_for(clear, 10)).status_code == 200
    assert (await asyncio.wait_for(refresh, 10))["rag_indexed"] is True
    assert await current.schema_rag.find_relevant_tables("marker", db_id="target")


async def test_other_database_clear_does_not_wait_for_target_embedding(client, monkeypatch):
    await connect()
    current = await engine.initialize_rag_engine()
    entered, release = asyncio.Event(), asyncio.Event()
    original = current.embedding_generator.embed

    async def hold(texts):
        entered.set()
        await release.wait()
        return await original(texts)

    monkeypatch.setattr(current.embedding_generator, "embed", hold)
    refresh = asyncio.create_task(refresh_schema("target"))
    await asyncio.wait_for(entered.wait(), 10)
    try:
        response = await asyncio.wait_for(
            client.post("/api/v1/learning/rag/schema/orphan/clear"), 10,
        )
        assert response.status_code == 200 and not refresh.done()
    finally:
        release.set()
        await asyncio.wait_for(refresh, 10)


@pytest.mark.parametrize("failure", [False, True])
async def test_disconnect_reports_index_outcome_and_supports_orphan_retry(
    client, monkeypatch, failure,
):
    await connect()
    current = await engine.initialize_rag_engine()

    def fail(*args, **kwargs):
        raise RuntimeError("private-disconnect-fixture")

    with monkeypatch.context() as patch:
        if failure:
            patch.setattr(current.bm25_index, "delete_database", fail)
        response = await client.delete("/api/v1/databases/target")
    assert response.status_code == 200
    assert response.json()["status"] == "disconnected"
    assert response.json()["rag_index_cleared"] is (not failure)
    assert bool(response.json()["warnings"]) is failure
    assert "private-disconnect-fixture" not in response.text
    with pytest.raises(KeyError):
        ConnectionFactory.get("target")
    if failure:
        assert "target" in current.schema_rag.refresh_required_databases
        with pytest.raises(RuntimeError, match="source refresh"):
            await current.schema_rag.find_relevant_tables("marker", db_id="target")
        assert (await client.post("/api/v1/learning/rag/schema/target/clear")).status_code == 200
    assert snapshots(current, "target") == ([], [])
    assert "target" not in current.schema_rag.refresh_required_databases


async def test_disconnect_cancellation_after_close_keeps_index_blocked(client, monkeypatch):
    await connect()
    current = await engine.initialize_rag_engine()
    before = snapshots(current, "target")
    closed = asyncio.Event()
    original = ConnectionFactory.aclose

    async def announce_close(db_id):
        await original(db_id)
        closed.set()

    monkeypatch.setattr(ConnectionFactory, "aclose", announce_close)
    async with gate(("schema-refresh", "target")):
        disconnect = asyncio.create_task(client.delete("/api/v1/databases/target"))
        await asyncio.wait_for(closed.wait(), 10)
        disconnect.cancel()
        with pytest.raises(asyncio.CancelledError):
            await disconnect
    assert snapshots(current, "target") == before
    assert "target" in current.schema_rag.refresh_required_databases
    with pytest.raises(KeyError):
        ConnectionFactory.get("target")
    assert (await client.post("/api/v1/learning/rag/schema/target/clear")).status_code == 200


async def test_disconnect_without_rag_reports_partial_outcome(client):
    await connect()
    response = await client.delete("/api/v1/databases/target")
    assert response.status_code == 200
    assert response.json()["rag_index_cleared"] is False
    assert response.json()["warnings"]
    with pytest.raises(KeyError):
        ConnectionFactory.get("target")


async def test_valid_legacy_schema_clear_remains_supported(client):
    current = await engine.initialize_rag_engine()
    response = await client.post("/api/v1/learning/rag/clear", json={"scope": "schema"})
    assert response.status_code == 200 and response.json()["status"] == "ok"
    assert snapshots(current, "target") == ([], [])
    assert snapshots(current, "orphan") == ([], [])
