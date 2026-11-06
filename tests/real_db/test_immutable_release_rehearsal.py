"""Upgrade and roll back a sealed release against an owned MySQL fixture."""

import asyncio
import shutil

import pytest

from app.storage.mysql_store import MySQLStore
from tests.fixtures.immutable_release import build_release
from tests.real_db.test_mysql_coordination import coordination as coordination
from tests.real_db.test_shared_query_runtime import body
from tests.real_db.test_shared_query_runtime import shared_servers as shared_servers

pytestmark = [
    pytest.mark.real_database,
    pytest.mark.parametrize("service", ["mysql"], indirect=True),
    pytest.mark.parametrize("shared_servers", [{"immutable": True}], indirect=True),
]


async def stop_workers(client, workers):
    for url, _, process in workers:
        assert (await client.post(url + "/probe/stop")).status_code == 200
        assert await asyncio.wait_for(process.wait(), 10) == 0


async def test_upgrade_and_rollback_restores_previous_generation(
    shared_servers, coordination, tmp_path
):
    client, servers = shared_servers
    database, pool, config, _ = coordination
    store = MySQLStore(pool)
    original = (await store.get_settings())["llm"]
    session_path = "/api/v1/sessions/rehearsal"

    first, second = servers[:2]
    response = await client.post(first[0] + "/api/v1/query", json=body("rehearsal"))
    assert response.status_code == 200 and response.json()["status"] == "completed"
    saved = (await client.get(second[0] + session_path)).json()["session"]
    await stop_workers(client, (first, second))

    archive = tmp_path / "archive"
    archive.mkdir()
    for name in ("release.json", "lance", "sparse.sqlite3"):
        shutil.move(tmp_path / name, archive / name)

    updated_path = tmp_path / "updated"
    updated_path.mkdir()
    updated_env = await build_release(config, updated_path)
    updated_env["SHARED_WORKER_NAMESPACE"] = database + "-v2"
    await store.save_setting("llm", {**original, "max_tokens": 2048})
    upgraded = [
        await servers.launch(name, overrides=updated_env) for name in ("updated-1", "updated-2")
    ]
    for url, _, _ in upgraded:
        assert (await client.get(url + "/health/ready")).status_code == 200
        assert (await client.get(url + session_path)).status_code == 404
    response = await client.post(upgraded[0][0] + "/api/v1/query", json=body("rehearsal"))
    assert response.status_code == 200 and response.json()["status"] == "completed"
    assert (await client.get(upgraded[1][0] + session_path)).status_code == 200
    await stop_workers(client, upgraded)

    await store.save_setting("llm", original)
    shutil.copytree(archive / "lance", tmp_path / "lance")
    for name in ("release.json", "sparse.sqlite3"):
        shutil.copy2(archive / name, tmp_path / name)
    restored = [await servers.launch(name) for name in ("rollback-1", "rollback-2")]
    for url, _, _ in restored:
        assert (await client.get(url + "/health/ready")).status_code == 200
        assert (await client.get(url + session_path)).json()["session"] == saved
