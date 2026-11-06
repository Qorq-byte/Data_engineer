"""Persisted LLM startup failures cannot be hidden by an otherwise ready process."""

import copy
import sqlite3
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI

from app.api import _lifespan, health
from app.api import settings as api_settings
from app.db.connections import ConnectionFactory, DatabaseType
from app.llm import factory
from app.llm.router import LiteLLMRouter, RouterConfig
from app.security.crypto import encrypt_secret
from tests.test_rag.test_effective_settings import SQLiteSettings


@pytest.fixture
async def startup(tmp_path, monkeypatch):
    from app import background, bootstrap
    from app.auth import database as auth_database
    from app.rag import engine
    from app.storage import mysql_store
    from app.subagent.manager import subagent_manager

    async def available():
        return None

    # Isolate unrelated startup services. The settings adapter and query
    # connection are real SQLite; auth availability is a deliberate fixture.
    monkeypatch.setattr(bootstrap, "initialize_catalogs", lambda: None)
    monkeypatch.setattr(mysql_store, "init_db", available)
    monkeypatch.setattr(mysql_store, "get_store", lambda: None)
    monkeypatch.setattr(auth_database, "init_auth_db", available)
    monkeypatch.setattr(auth_database, "close_auth_pool", available)
    monkeypatch.setattr(background, "stop_background", available)
    monkeypatch.setattr(subagent_manager, "deactivate_all", available)
    schema_rag = SimpleNamespace(refresh_required_databases=set(), unavailable_databases=set())
    monkeypatch.setattr(engine, "get_rag_engine", lambda: SimpleNamespace(schema_rag=schema_rag))
    monkeypatch.setattr(engine, "close_rag_engine", lambda: None)
    for name in ("_connections", "_infos", "_offload_connections", "_mysql_pools"):
        monkeypatch.setattr(ConnectionFactory, name, {})
    await ConnectionFactory.create(DatabaseType.SQLITE, {}, "readiness-fixture")
    adapter = SQLiteSettings(tmp_path / "settings.sqlite")
    config = {
        "default_model": "fixture-model", "max_tokens": 777,
        "providers": {"fixture": {
            "enabled": True, "models": ["fixture-model"],
            "api_key_enc": encrypt_secret("synthetic-startup-key"),
        }},
    }
    await adapter.save_setting("llm", config)
    monkeypatch.setattr(api_settings, "_get_store", lambda: adapter)
    monkeypatch.setattr(
        api_settings, "_settings_store", copy.deepcopy(api_settings._DEFAULT_SETTINGS)
    )
    monkeypatch.setattr(factory, "_default_router", None)
    monkeypatch.setattr(factory, "_mock_forced", lambda: False)
    app = FastAPI(lifespan=_lifespan)
    app.include_router(health.router)
    app.include_router(api_settings.router, prefix="/api/v1")
    yield app, adapter
    await ConnectionFactory.aclose_all()
    adapter.connection.close()


def deny_settings_reads(adapter):
    adapter.connection.set_authorizer(
        lambda action, *args: (
            sqlite3.SQLITE_DENY if action == sqlite3.SQLITE_READ else sqlite3.SQLITE_OK
        )
    )


@pytest.mark.parametrize("cached", [False, True])
async def test_reload_failure_cannot_reuse_stale_or_build_environment_router(
    startup, monkeypatch, cached
):
    app, adapter = startup
    if cached:
        factory.publish_llm_router(LiteLLMRouter(RouterConfig(), mock_mode=False))

    def unexpected_environment_fallback():
        pytest.fail("Failed persisted reload must not build an environment fallback")

    monkeypatch.setattr(factory, "build_router", unexpected_environment_fallback)
    deny_settings_reads(adapter)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://fixture"
        ) as client,
    ):
        response = await client.get("/health/ready")
        assert response.status_code == 503
        result = response.json()
        assert result["initialized"] is True
        assert result["components"]["auth"] is True
        assert result["connected_database_count"] == 1
        assert result["components"]["llm"] is False
        assert app.state.components["llm"] is False
        assert factory.current_llm_router().mock_mode is True


@pytest.mark.parametrize("recovery", ["reload", "put"])
async def test_successful_configuration_recovers_live_readiness(startup, recovery):
    app, adapter = startup
    deny_settings_reads(adapter)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://fixture"
        ) as client,
    ):
        assert (await client.get("/health/ready")).status_code == 503
        adapter.connection.set_authorizer(None)
        if recovery == "reload":
            await api_settings.reload_llm_router_from_db()
        else:
            update = await client.put("/api/v1/settings/llm", json={"max_tokens": 888})
            assert update.status_code == 200
            assert update.json()["persistence"] == "persisted"
            assert factory.current_llm_router().config.max_output_tokens == 888
        response = await client.get("/health/ready")
        assert response.status_code == 200
        assert response.json()["components"]["llm"] is True
        assert factory.current_llm_router().config.default_model == "fixture-model"


async def test_successful_startup_reloads_persisted_router(startup):
    app, _ = startup
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://fixture"
        ) as client,
    ):
        assert (await client.get("/health/ready")).status_code == 200
        assert app.state.components["llm"] is True
        assert factory.current_llm_router().config.default_model == "fixture-model"
        assert factory.current_llm_router().config.max_output_tokens == 777
