"""Real index instances must use the weights acknowledged by settings APIs."""

import asyncio
import copy
import json
import sqlite3

import pytest

from app.api import create_app
from app.api import settings as api_settings
from app.config.settings import settings
from app.db.connections import ConnectionFactory
from app.knowledge.retrieval.embedding import MockEmbeddingProvider
from app.models.rag_metric import MetricDocument
from app.models.rag_schema import SchemaDocument
from app.rag import engine
from tests.api_client import authenticated_client


@pytest.fixture
async def active(tmp_path, monkeypatch):
    monkeypatch.setattr(
        api_settings, "_settings_store", copy.deepcopy(api_settings._DEFAULT_SETTINGS)
    )
    monkeypatch.setattr(api_settings, "_get_store", lambda: None)
    monkeypatch.setattr(engine, "_engine", None)
    for name in ("_connections", "_infos", "_offload_connections", "_mysql_pools"):
        monkeypatch.setattr(ConnectionFactory, name, {})
    monkeypatch.setattr(engine, "_create_embedding_provider", lambda: MockEmbeddingProvider(16))
    monkeypatch.setattr(settings, "rag_lancedb_dir", str(tmp_path / "lance"))
    monkeypatch.setattr(settings, "rag_bm25_path", str(tmp_path / "sparse.db"))
    current = await engine.initialize_rag_engine()
    yield current
    engine.close_rag_engine()


async def test_existing_engine_changes_after_acknowledged_save(active):
    response = await api_settings.update_rag_settings(
        api_settings.RagSettingsUpdate(vector_weight=0.2, bm25_weight=0.8)
    )
    assert response["status"] == "updated"
    assert [
        active.schema_rag.rrf_alpha,
        active.metric_rag.rrf_alpha,
        active.document_store.rrf_alpha,
    ] == [0.2, 0.2, 0.2]


def test_unwired_parameters_are_rejected():
    client = authenticated_client(create_app())
    response = client.put("/api/v1/settings/rag", json={"top_k": 4})
    assert response.status_code == 422


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"vector_weight": None},
        {"vector_weight": True},
        {"vector_weight": "0.3"},
        {"vector_weight": -0.1},
        {"vector_weight": 1.1},
        {"bm25_weight": 0.8, "vector_weight": 0.8},
        {"similarity_threshold": 0.5},
        {"embedding_model": "other"},
        {"unexpected": 1},
    ],
)
def test_invalid_contract_cannot_change_configuration(payload, monkeypatch):
    baseline = copy.deepcopy(api_settings._DEFAULT_SETTINGS)
    monkeypatch.setattr(api_settings, "_settings_store", baseline)
    monkeypatch.setattr(api_settings, "_get_store", lambda: None)
    before = copy.deepcopy(baseline)
    response = authenticated_client(create_app()).put("/api/v1/settings/rag", json=payload)
    assert response.status_code == 422
    assert baseline == before


@pytest.mark.parametrize(
    "payload,expected",
    [
        ({"bm25_weight": 0.9}, 0.1),
        ({"vector_weight": 0}, 0),
        ({"vector_weight": 1}, 1),
    ],
)
async def test_complement_and_explicit_volatile_acknowledgement(active, payload, expected):
    response = await api_settings.update_rag_settings(api_settings.RagSettingsUpdate(**payload))
    assert response["vector_weight"] == pytest.approx(expected)
    assert response["bm25_weight"] == pytest.approx(1 - expected)
    assert response["persistence"] == "memory_only"
    assert response["runtime_applied"] is True
    assert response["runtime"]["matches_configured"] is True
    assert response["runtime"]["embedding"]["provider"] == "MockEmbeddingProvider"
    assert response["runtime"]["embedding"]["dimension"] == 16


class SQLiteSettings:
    """Real SQLite adapter to the settings store protocol, not a MySQL integration claim."""

    def __init__(self, path):
        self.connection = sqlite3.connect(path)
        self.connection.execute(
            "CREATE TABLE IF NOT EXISTS settings(section TEXT PRIMARY KEY, value TEXT)"
        )
        self.connection.commit()

    async def get_setting(self, section):
        row = self.connection.execute(
            "SELECT value FROM settings WHERE section=?", (section,)
        ).fetchone()
        return json.loads(row[0]) if row else None

    async def get_settings(self):
        return {
            section: json.loads(value)
            for section, value in self.connection.execute("SELECT section,value FROM settings")
        }

    async def save_setting(self, section, config):
        self.connection.execute(
            "INSERT OR REPLACE INTO settings VALUES(?,?)", (section, json.dumps(config))
        )
        self.connection.commit()


@pytest.fixture
def store(tmp_path, monkeypatch):
    adapter = SQLiteSettings(tmp_path / "settings.sqlite")
    monkeypatch.setattr(api_settings, "_get_store", lambda: adapter)
    yield adapter
    adapter.connection.close()


async def test_real_settings_persist_and_reload_without_reapplying_on_get(active, store):
    response = await api_settings.update_rag_settings(
        api_settings.RagSettingsUpdate(vector_weight=0.2)
    )
    assert response["persistence"] == "persisted"
    assert (await store.get_setting("rag"))["vector_weight"] == 0.2
    await store.save_setting("rag", {"vector_weight": 0.9, "top_k": 37})
    read = await api_settings.get_rag_settings()
    assert read["vector_weight"] == 0.9
    assert read["runtime"]["weights"]["schema"]["vector_weight"] == 0.2
    assert read["runtime"]["matches_configured"] is False
    assert "top_k" not in read
    assert "top_k" in read["legacy_ignored_fields"]
    engine.close_rag_engine()
    api_settings._settings_store.clear()
    await api_settings._load_settings_from_db()
    restarted = await engine.initialize_rag_engine()
    assert restarted.schema_rag.rrf_alpha == 0.9


async def test_native_settings_write_denial_keeps_runtime_and_memory(active, store):
    await store.save_setting("rag", {"vector_weight": 0.7})
    before = copy.deepcopy(api_settings._settings_store["rag"])
    store.connection.set_authorizer(
        lambda action, *args: (
            sqlite3.SQLITE_DENY if action == sqlite3.SQLITE_INSERT else sqlite3.SQLITE_OK
        )
    )
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as error:
        await api_settings.update_rag_settings(api_settings.RagSettingsUpdate(vector_weight=0.2))
    assert error.value.status_code == 503
    assert error.value.detail["persistence"] == "unconfirmed"
    assert api_settings._settings_store["rag"] == before
    assert active.schema_rag.rrf_alpha == 0.7


async def test_native_settings_read_denial_is_not_hidden_by_defaults(active, store):
    store.connection.set_authorizer(
        lambda action, *args: (
            sqlite3.SQLITE_DENY if action == sqlite3.SQLITE_READ else sqlite3.SQLITE_OK
        )
    )
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as error:
        await api_settings.get_rag_settings()
    assert error.value.status_code == 503
    assert error.value.detail["code"] == "RAG_SETTINGS_READ_FAILED"
    assert active.schema_rag.rrf_alpha == 0.7


async def test_pending_configuration_is_used_by_initialization(active):
    engine.close_rag_engine()
    response = await api_settings.update_rag_settings(
        api_settings.RagSettingsUpdate(vector_weight=0.4)
    )
    assert response["runtime_applied"] is False
    assert response["runtime"]["embedding"] is None
    assert (await engine.initialize_rag_engine()).document_store.rrf_alpha == 0.4


@pytest.mark.parametrize("component", ["schema", "metrics", "documents"])
async def test_search_captures_fusion_before_embedding_await(active, component):
    from app.models.rag_document import Document

    await active.schema_rag.index_schema(
        SchemaDocument("scope.one", "scope", table_name="one", embedding_text="needle marker")
    )
    await active.metric_rag.index_metric(
        MetricDocument("metric.one", name="needle marker", embedding_text="needle marker")
    )
    await active.document_store.index_document(
        Document("doc.one", title="needle marker", content="needle marker")
    )
    invoke = {
        "schema": lambda: active.schema_rag.find_relevant_tables("needle", db_id="scope"),
        "metrics": lambda: active.metric_rag.match_metrics("needle"),
        "documents": lambda: active.document_store.search("needle"),
    }[component]
    baseline = await invoke()
    assert baseline
    entered, release = asyncio.Event(), asyncio.Event()
    original = active.embedding_generator.provider.embed

    async def delayed(texts):
        entered.set()
        await release.wait()
        return await original(texts)

    active.embedding_generator.provider.embed = delayed
    pending = asyncio.create_task(invoke())
    try:
        await asyncio.wait_for(entered.wait(), 10)
        await api_settings.update_rag_settings(api_settings.RagSettingsUpdate(vector_weight=0.1))
    finally:
        release.set()
    result = await asyncio.wait_for(pending, 10)
    assert [(row.doc_id, row.rrf_score) for row in result] == [
        (row.doc_id, row.rrf_score) for row in baseline
    ]
    assert active.schema_rag.rrf_alpha == 0.1


async def test_cancel_during_persistence_does_not_publish_runtime(active, store):
    entered, release = asyncio.Event(), asyncio.Event()
    original = store.save_setting

    async def delayed(section, config):
        entered.set()
        await release.wait()
        await original(section, config)

    store.save_setting = delayed
    task = asyncio.create_task(
        api_settings.update_rag_settings(api_settings.RagSettingsUpdate(vector_weight=0.2))
    )
    await asyncio.wait_for(entered.wait(), 10)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert active.schema_rag.rrf_alpha == 0.7
    assert (await api_settings.get_rag_settings())["vector_weight"] == 0.7


async def test_concurrent_writes_publish_in_persistence_order(active, store):
    entered, release = asyncio.Event(), asyncio.Event()
    original = store.save_setting
    observed = []

    async def delayed(section, config):
        observed.append(config["vector_weight"])
        if len(observed) == 1:
            entered.set()
            await release.wait()
        await original(section, config)

    store.save_setting = delayed
    first = asyncio.create_task(
        api_settings.update_rag_settings(api_settings.RagSettingsUpdate(vector_weight=0.2))
    )
    await asyncio.wait_for(entered.wait(), 10)
    second = asyncio.create_task(
        api_settings.update_rag_settings(api_settings.RagSettingsUpdate(vector_weight=0.9))
    )
    await asyncio.sleep(0)
    assert observed == [0.2]
    release.set()
    await asyncio.wait_for(asyncio.gather(first, second), 10)
    assert observed == [0.2, 0.9]
    assert active.schema_rag.rrf_alpha == 0.9
    assert (await store.get_setting("rag"))["vector_weight"] == 0.9


async def test_cancel_after_real_commit_reports_drift_on_explicit_read(active, store):
    committed = asyncio.Event()
    original = store.save_setting

    async def commit_then_pause(section, config):
        await original(section, config)
        committed.set()
        await asyncio.Event().wait()

    store.save_setting = commit_then_pause
    task = asyncio.create_task(
        api_settings.update_rag_settings(api_settings.RagSettingsUpdate(vector_weight=0.2))
    )
    await asyncio.wait_for(committed.wait(), 10)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    read = await api_settings.get_rag_settings()
    assert read["vector_weight"] == 0.2
    assert read["runtime"]["weights"]["schema"]["vector_weight"] == 0.7
    assert read["runtime"]["matches_configured"] is False


async def test_storage_timeout_does_not_publish_and_releases_gate(active, store, monkeypatch):
    monkeypatch.setattr(api_settings, "_RAG_SETTINGS_TIMEOUT_SECONDS", 0.02)

    async def never_finish(section, config):
        await asyncio.Event().wait()

    store.save_setting = never_finish
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as error:
        await api_settings.update_rag_settings(api_settings.RagSettingsUpdate(vector_weight=0.2))
    assert error.value.status_code == 503
    assert active.schema_rag.rrf_alpha == 0.7
    assert (await api_settings.get_rag_settings())["vector_weight"] == 0.7


async def test_settings_wait_for_engine_candidate_before_acknowledgement(active, monkeypatch):
    engine.close_rag_engine()
    entered, release = asyncio.Event(), asyncio.Event()
    original = engine._index_all_connected_schemas

    async def hold(candidate):
        entered.set()
        await release.wait()
        return await original(candidate)

    monkeypatch.setattr(engine, "_index_all_connected_schemas", hold)
    startup = asyncio.create_task(engine.initialize_rag_engine())
    await asyncio.wait_for(entered.wait(), 10)
    save = asyncio.create_task(
        api_settings.update_rag_settings(api_settings.RagSettingsUpdate(vector_weight=0.2))
    )
    await asyncio.sleep(0)
    assert not save.done()
    release.set()
    _, response = await asyncio.wait_for(asyncio.gather(startup, save), 10)
    assert response["runtime_applied"] is True
    assert engine.get_rag_engine().schema_rag.rrf_alpha == 0.2


async def test_actual_sparse_and_dense_rankings_change_after_save(active):
    from app.knowledge.retrieval.embedding import EmbeddingResult

    original_provider = active.embedding_generator.provider

    async def controlled_vectors(texts):
        # Synthetic orthogonal vectors force disagreement; both indexes and RRF are real.
        return EmbeddingResult(
            vectors=[
                [0.0, 1.0] + [0.0] * 14 if "sparse-only" in text else [1.0, 0.0] + [0.0] * 14
                for text in texts
            ],
            model=original_provider.model_name,
            dimension=16,
        )

    original_provider.embed = controlled_vectors
    await active.schema_rag.index_schemas(
        [
            SchemaDocument("rank.a", "rank", table_name="a", embedding_text="needle sparse-only"),
            SchemaDocument("rank.b", "rank", table_name="b", embedding_text="other dense document"),
        ]
    )
    await api_settings.update_rag_settings(api_settings.RagSettingsUpdate(vector_weight=0))
    sparse = await active.schema_rag.find_relevant_tables("needle", db_id="rank", top_k=1)
    await api_settings.update_rag_settings(api_settings.RagSettingsUpdate(vector_weight=1))
    dense = await active.schema_rag.find_relevant_tables("needle", db_id="rank", top_k=1)
    assert [row.doc_id for row in sparse] == ["rank.a"]
    assert [row.doc_id for row in dense] == ["rank.b"]


@pytest.mark.parametrize("value", [True, "0.3", float("nan"), float("inf"), -1, 2])
def test_immutable_weight_value_rejects_invalid_scalars(value):
    from app.models.rag_settings import FusionWeights

    with pytest.raises(ValueError):
        FusionWeights(value)


async def test_legacy_invalid_weight_is_visible_and_does_not_claim_matches(active):
    api_settings._settings_store["rag"]["vector_weight"] = "invalid"
    response = await api_settings.get_rag_settings()
    assert response["configuration_valid"] is False
    assert response["runtime"]["matches_configured"] is False
    assert response["vector_weight"] == 0.7


async def test_aggregate_settings_exposes_actual_rag_runtime(active):
    response = await api_settings.get_all_settings()
    rag = response["settings"]["rag"]
    assert rag["runtime"]["embedding"]["dimension"] == 16
    assert rag["runtime"]["matches_configured"] is True
    assert "top_k" not in rag


async def test_default_documents_are_not_aliased_by_edits(active):
    before = copy.deepcopy(api_settings._DEFAULT_SETTINGS)
    api_settings._settings_store.clear()
    await api_settings._load_settings_from_db()
    await api_settings.update_rag_settings(api_settings.RagSettingsUpdate(vector_weight=0.2))
    assert before == api_settings._DEFAULT_SETTINGS


def test_rag_configuration_keeps_existing_administrator_boundary():
    from fastapi.testclient import TestClient

    from app.auth.security import create_access_token

    client = TestClient(create_app())
    assert client.get("/api/v1/settings/rag").status_code == 401
    token = create_access_token(900000, "synthetic-unprivileged")
    headers = {"Authorization": f"Bearer {token}"}
    assert client.get("/api/v1/settings/rag", headers=headers).status_code == 403
    assert (
        client.put("/api/v1/settings/rag", headers=headers, json={"vector_weight": 0.2}).status_code
        == 403
    )
