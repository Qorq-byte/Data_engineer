"""LLM updates must not acknowledge failed persistence or partially mutate state."""

import asyncio
import copy
import sqlite3

import pytest
from fastapi import HTTPException

from app.api import create_app
from app.api import settings as api_settings
from app.llm import factory
from app.security.crypto import decrypt_secret
from tests.api_client import authenticated_client
from tests.test_rag.test_effective_settings import SQLiteSettings


def update(**values):
    return api_settings.LLMSettingsUpdate(**values)


@pytest.fixture
async def stored(tmp_path, monkeypatch):
    from app.config.settings import settings

    for provider in ("openai", "anthropic", "deepseek", "google"):
        monkeypatch.delenv(f"{provider.upper()}_API_KEY", raising=False)
        monkeypatch.setattr(settings, f"{provider}_api_key", None)
    adapter = SQLiteSettings(tmp_path / "settings.sqlite")
    adapter.connection.close()
    adapter.connection = sqlite3.connect(tmp_path / "settings.sqlite", check_same_thread=False)
    initial = copy.deepcopy(api_settings._DEFAULT_SETTINGS)
    for section, value in initial.items():
        await adapter.save_setting(section, value)
    monkeypatch.setattr(api_settings, "_settings_store", copy.deepcopy(initial))
    monkeypatch.setattr(api_settings, "_get_store", lambda: adapter)
    monkeypatch.setattr(factory, "_default_router", None)
    factory.apply_llm_settings(initial["llm"])
    yield adapter
    adapter.connection.close()


async def test_failed_write_keeps_old_memory_and_router(stored):
    original = factory.get_default_router()
    before = copy.deepcopy(api_settings._settings_store)
    stored.connection.set_authorizer(
        lambda action, *args: (
            sqlite3.SQLITE_DENY if action == sqlite3.SQLITE_INSERT else sqlite3.SQLITE_OK
        )
    )
    response = authenticated_client(create_app()).put(
        "/api/v1/settings/llm", json={"default_model": "changed-model"}
    )
    assert response.status_code == 503
    assert api_settings._settings_store == before
    assert factory.get_default_router() is original


async def test_invalid_strategy_cannot_partially_mutate(stored):
    before = copy.deepcopy(api_settings._settings_store)
    response = authenticated_client(create_app()).put(
        "/api/v1/settings/llm",
        json={"default_model": "changed-model", "routing_strategy": "invalid"},
    )
    assert response.status_code == 400
    assert api_settings._settings_store == before


async def test_real_encrypted_write_cold_restore_and_delete(stored):
    response = await api_settings.update_llm_settings(
        update(
            default_model="fixture-model",
            providers={
                "fixture": {
                    "api_key": "synthetic-fixture-key",
                    "models": ["fixture-model"],
                    "base_url": "https://example.invalid/v1",
                }
            },
        )
    )
    assert response["persistence"] == "persisted"
    assert response["runtime_applied"] is True
    assert "synthetic-fixture-key" not in str(response)
    assert "api_key_enc" not in str(response)
    raw = await stored.get_setting("llm")
    encrypted = raw["providers"]["fixture"]["api_key_enc"]
    assert encrypted != "synthetic-fixture-key"
    assert decrypt_secret(encrypted) == "synthetic-fixture-key"
    api_settings._settings_store.clear()
    factory.reset_default_router()
    assert (await api_settings.get_llm_settings())["runtime"]["available"] is False
    await api_settings.reload_llm_router_from_db()
    assert "fixture" in factory.get_default_router().config.providers
    deleted = await api_settings.delete_llm_provider("fixture")
    assert deleted["status"] == "deleted"
    assert deleted["persistence"] == "persisted"
    assert "fixture" not in factory.get_default_router().config.providers
    assert "fixture" not in (await stored.get_setting("llm"))["providers"]


async def test_memory_only_explicit(stored, monkeypatch):
    monkeypatch.setattr(api_settings, "_get_store", lambda: None)
    result = await api_settings.update_llm_settings(update(default_model="temporary"))
    assert result["persistence"] == "memory_only" and result["runtime_applied"] is True


@pytest.mark.parametrize("operation", ["read", "models", "startup", "write", "delete"])
async def test_native_read_denial_never_falls_back(stored, operation):
    original = factory.get_default_router()
    stored.connection.set_authorizer(
        lambda action, *args: (
            sqlite3.SQLITE_DENY if action == sqlite3.SQLITE_READ else sqlite3.SQLITE_OK
        )
    )
    operations = {
        "read": lambda: api_settings.get_llm_settings(),
        "models": lambda: api_settings.get_available_models(),
        "startup": lambda: api_settings.reload_llm_router_from_db(),
        "write": lambda: api_settings.update_llm_settings(update(default_model="other")),
        "delete": lambda: api_settings.delete_llm_provider("fixture"),
    }
    with pytest.raises(HTTPException) as caught:
        await operations[operation]()
    assert caught.value.status_code == 503
    assert caught.value.detail["code"] == "LLM_SETTINGS_READ_FAILED"
    assert factory.get_default_router() is original


async def test_prepare_failure_no_write_or_secret_response(stored, monkeypatch):
    original = factory.get_default_router()
    before = await stored.get_setting("llm")

    def fail(config):
        raise ValueError("synthetic-private-error")

    monkeypatch.setattr(factory, "build_router_from_settings", fail)
    response = authenticated_client(create_app()).put(
        "/api/v1/settings/llm", json={"default_model": "other"}
    )
    assert response.status_code == 400
    assert "synthetic-private-error" not in response.text
    assert await stored.get_setting("llm") == before
    assert factory.get_default_router() is original


async def test_write_timeout_does_not_publish(stored, monkeypatch):
    original = factory.get_default_router()

    async def slow(section, config):
        await asyncio.Event().wait()

    monkeypatch.setattr(stored, "save_setting", slow)
    monkeypatch.setattr(api_settings, "_LLM_SETTINGS_TIMEOUT_SECONDS", 0.02)
    with pytest.raises(HTTPException) as caught:
        await api_settings.update_llm_settings(update(default_model="other"))
    assert caught.value.detail["persistence"] == "unconfirmed"
    assert factory.get_default_router() is original


@pytest.mark.parametrize("commit_first", [False, True])
async def test_cancel_may_commit_but_never_publish(stored, monkeypatch, commit_first):
    original = factory.get_default_router()
    entered = asyncio.Event()
    save = stored.save_setting

    async def pause(section, config):
        if commit_first:
            await save(section, config)
        entered.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(stored, "save_setting", pause)
    task = asyncio.create_task(api_settings.update_llm_settings(update(default_model="changed")))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert factory.get_default_router() is original
    assert api_settings._settings_store["llm"]["default_model"] != "changed"
    assert ((await stored.get_setting("llm"))["default_model"] == "changed") is commit_first
    if commit_first:
        view = await api_settings.get_llm_settings()
        assert view["default_model"] == "changed"
        assert view["runtime"]["active_model"] != "changed"


async def test_two_writes_and_startup_share_gate(stored, monkeypatch):
    original_save = stored.save_setting
    entered, release = asyncio.Event(), asyncio.Event()
    writes = []

    async def save(section, config):
        writes.append(config["default_model"])
        if len(writes) == 1:
            entered.set()
            await release.wait()
        await original_save(section, config)

    monkeypatch.setattr(stored, "save_setting", save)
    first = asyncio.create_task(api_settings.update_llm_settings(update(default_model="first")))
    await entered.wait()
    second = asyncio.create_task(api_settings.update_llm_settings(update(temperature=0.6)))
    startup = asyncio.create_task(api_settings.reload_llm_router_from_db())
    await asyncio.sleep(0)
    assert writes == ["first"] and not second.done() and not startup.done()
    release.set()
    await asyncio.gather(first, second, startup)
    raw = await stored.get_setting("llm")
    assert raw["default_model"] == "first" and raw["temperature"] == 0.6
    assert factory.get_default_router().config.default_model == "first"


@pytest.mark.parametrize(
    "payload",
    [
        {"providers": {"fixture": {"api_key": "private-input-" * 60}}},
        {"providers": {"fixture": {"models": {"key": "private-input"}}}},
    ],
)
async def test_validation_errors_do_not_echo_secret_input(stored, payload):
    response = authenticated_client(create_app()).put("/api/v1/settings/llm", json=payload)
    assert response.status_code == 422
    assert "private-input" not in response.text


async def test_delete_write_denied_keeps_runtime(stored):
    await api_settings.update_llm_settings(
        update(providers={"fixture": {"api_key": "synthetic-key", "models": ["fixture-model"]}})
    )
    original = factory.get_default_router()
    stored.connection.set_authorizer(
        lambda action, *args: (
            sqlite3.SQLITE_DENY if action == sqlite3.SQLITE_INSERT else sqlite3.SQLITE_OK
        )
    )
    with pytest.raises(HTTPException):
        await api_settings.delete_llm_provider("fixture")
    assert factory.get_default_router() is original
    assert "fixture" in api_settings._settings_store["llm"]["providers"]


async def test_read_timeout_and_cancel_do_not_fallback(stored, monkeypatch):
    entered = asyncio.Event()

    async def slow(section):
        entered.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(stored, "get_setting", slow)
    monkeypatch.setattr(api_settings, "_LLM_SETTINGS_TIMEOUT_SECONDS", 0.02)
    with pytest.raises(HTTPException) as caught:
        await api_settings.get_llm_settings()
    assert caught.value.detail["code"] == "LLM_SETTINGS_READ_FAILED"
    monkeypatch.setattr(api_settings, "_LLM_SETTINGS_TIMEOUT_SECONDS", 10)
    entered.clear()
    task = asyncio.create_task(api_settings.get_llm_settings())
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.parametrize("raw", [["invalid"], {"providers": {"invalid": "private"}}])
async def test_bad_stored_record_rejected(stored, raw):
    await stored.save_setting("llm", raw)
    with pytest.raises(HTTPException) as caught:
        await api_settings.get_llm_settings()
    assert caught.value.detail["code"] == "LLM_SETTINGS_READ_FAILED"


@pytest.mark.parametrize("method", ["get", "put", "delete"])
@pytest.mark.parametrize("user_id,expected", [(None, 401), (999999, 403)])
async def test_operator_boundary(stored, method, user_id, expected):
    from fastapi.testclient import TestClient

    from app.auth.security import create_access_token

    headers = (
        {}
        if user_id is None
        else {"Authorization": f"Bearer {create_access_token(user_id, 'non-operator')}"}
    )
    path = "/api/v1/settings/llm" + ("/providers/fixture" if method == "delete" else "")
    response = getattr(TestClient(create_app()), method)(path, headers=headers)
    assert response.status_code == expected
