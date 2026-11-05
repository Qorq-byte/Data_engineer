"""Actual MySQL persistence and new-process startup; no provider requests."""

import asyncio
import copy
import json
import os
import sys
import uuid
from pathlib import Path

import pytest
import pytest_asyncio
from fastapi import HTTPException

from app.api import settings as api_settings
from app.config.settings import settings
from app.llm import factory
from app.security.crypto import decrypt_secret
from app.storage.mysql_store import MySQLStore
from tests.fixtures.mysql_settings_worker import KEY, SECRET, candidate
from tests.real_db.support import admin_connection, admin_sql

pytestmark = [pytest.mark.real_database,
              pytest.mark.parametrize("service", ["mysql"], indirect=True)]
ROOT = Path(__file__).resolve().parents[2]


@pytest_asyncio.fixture
async def settings_store(service, monkeypatch):
    database = f"de_settings57_{uuid.uuid4().hex}"
    config = service.config(admin=True)
    config["db"] = database
    del config["database"]
    monkeypatch.setattr(MySQLStore, "_instance", None)
    monkeypatch.setattr(api_settings, "_settings_store", {})
    monkeypatch.setattr(factory, "_default_router", None)
    monkeypatch.setattr(settings, "jwt_secret", SECRET)
    async with admin_connection(service) as admin:
        await admin_sql(service, admin, f"CREATE DATABASE `{database}` CHARACTER SET utf8mb4")
        try:
            store = await MySQLStore.create(config)
            await store.init_tables()
            await store.save_setting("llm", candidate())
            await api_settings.reload_llm_router_from_db()
            yield store, config
        finally:
            if MySQLStore._instance is not None:
                await MySQLStore._instance.close()
            await admin_sql(service, admin, f"DROP DATABASE `{database}`")


async def worker(mode, config, directory):
    directory.mkdir()
    keep = {key: value for key, value in os.environ.items() if key.upper() in {
        "PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "HOME", "USERPROFILE",
        "USERNAME", "USER", "LOGNAME", "LNAME", "SSL_CERT_FILE", "SSL_CERT_DIR",
    }}
    keep.update(PYTHONPATH=str(ROOT), PYTHONUTF8="1")
    process = await asyncio.create_subprocess_exec(
        sys.executable, "-m", "tests.fixtures.mysql_settings_worker", mode,
        "--port", str(config["port"]), "--database", config["db"],
        cwd=directory, env=keep, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    try:
        _, stderr = await asyncio.wait_for(process.communicate(), timeout=60)
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()
    expected = 57 if mode == "commit-crash" else 0
    assert process.returncode == expected, stderr.decode(errors="replace")
    return directory


async def test_encrypted_save_reopen_reload_and_delete(settings_store):
    store, config = settings_store
    response = await api_settings.update_llm_settings(api_settings.LLMSettingsUpdate(
        max_tokens=888, providers={"fixture": {"api_key": KEY}},
    ))
    assert response["persistence"] == "persisted" and response["runtime_applied"]
    assert KEY not in str(response) and "api_key_enc" not in str(response)
    raw = await store.get_setting("llm")
    assert decrypt_secret(raw["providers"]["fixture"]["api_key_enc"]) == KEY
    assert KEY not in json.dumps(raw)
    await store.close()
    reopened = await MySQLStore.create(config)
    api_settings._settings_store.clear()
    factory.reset_default_router()
    await api_settings.reload_llm_router_from_db()
    assert factory.get_default_router().config.max_output_tokens == 888
    deleted = await api_settings.delete_llm_provider("fixture")
    assert deleted["persistence"] == "persisted"
    assert "fixture" not in (await reopened.get_setting("llm"))["providers"]


async def test_real_write_denial_does_not_publish(settings_store):
    store, _ = settings_store
    original = factory.get_default_router()
    memory = copy.deepcopy(api_settings._settings_store)
    before = await store.get_setting("llm")
    await store._execute("CREATE TRIGGER stage57_reject BEFORE INSERT ON user_settings "
                         "FOR EACH ROW SIGNAL SQLSTATE '45000' "
                         "SET MESSAGE_TEXT='synthetic write denial'")
    with pytest.raises(HTTPException) as caught:
        await api_settings.update_llm_settings(api_settings.LLMSettingsUpdate(max_tokens=999))
    assert caught.value.detail["code"] == "LLM_SETTINGS_PERSIST_FAILED"
    assert caught.value.detail["runtime_applied"] is False
    assert "synthetic write denial" not in str(caught.value.detail)
    assert factory.get_default_router() is original
    assert api_settings._settings_store == memory
    assert await store.get_setting("llm") == before


async def test_committed_write_survives_death_before_publish(settings_store, tmp_path):
    store, config = settings_store
    initial = candidate()
    initial["max_tokens"] = 111
    await store.save_setting("llm", initial)
    first = await worker("commit-crash", config, tmp_path / "writer")
    assert json.loads((first / "before-publish.json").read_text()) == {
        "model": "old", "memory_model": None,
    }
    assert (await store.get_setting("llm"))["max_tokens"] == 777
    second = await worker("restore", config, tmp_path / "reader")
    result = json.loads((second / "restored.json").read_text())
    assert result["http_status"] == 200
    assert result["model"] == "stage57-model" and result["max_tokens"] == 777
    assert result["key_restored"] is True
    assert all(result["readiness"]["components"][key] for key in ("storage", "auth", "llm"))
    assert KEY not in json.dumps(result)


@pytest.mark.parametrize("failure", [
    "missing_table", "invalid_limit", "invalid_providers",
    "models_string", "enabled_string", "rpm_string", "base_url_object", "key_number",
    "broken_ciphertext",
])
async def test_bad_database_settings_make_new_process_not_ready(settings_store, tmp_path, failure):
    store, config = settings_store
    if failure == "missing_table":
        # init_tables recreates a missing table at startup, so use a view with
        # the wrong shape; CREATE TABLE IF NOT EXISTS cannot silently repair it.
        await store._execute("DROP TABLE user_settings")
        await store._execute("CREATE VIEW user_settings AS SELECT 1 AS incompatible")
    elif failure in {"invalid_limit", "invalid_providers"}:
        invalid = candidate()
        invalid["max_tokens" if failure == "invalid_limit" else "providers"] = (
            0 if failure == "invalid_limit" else []
        )
        await store.save_setting("llm", invalid)
    else:
        invalid = candidate()
        field, value = {
            "models_string": ("models", "private-malformed-model"),
            "enabled_string": ("enabled", "false"),
            "rpm_string": ("rate_limit_rpm", "60"),
            "base_url_object": ("base_url", {"private-endpoint": "value"}),
            "key_number": ("api_key_enc", 123),
            "broken_ciphertext": ("api_key_enc", "private-broken-ciphertext"),
        }[failure]
        invalid["providers"]["fixture"][field] = value
        await store.save_setting("llm", invalid)
    directory = await worker("restore", config, tmp_path / "restart")
    result = json.loads((directory / "restored.json").read_text())
    assert result["http_status"] == 503
    assert result["readiness"]["components"]["llm"] is False
    assert result["readiness"]["components"]["auth"] is True
    assert result["key_restored"] is False
    assert result["model"] != "stage57-model"  # No partial publication of the damaged record.
    assert "private-" not in json.dumps(result)
