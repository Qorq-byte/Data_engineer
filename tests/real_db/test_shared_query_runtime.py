"""Two HTTP serving processes share real MySQL sessions and global admission."""

import asyncio
import json
import os
import sys
from pathlib import Path

import httpx
import pytest
import pytest_asyncio

from app.auth.security import create_access_token
from app.config.settings import settings
from app.security.crypto import encrypt_secret
from app.storage.mysql_store import MySQLStore
from tests.fixtures.shared_query_worker import SECRET
from tests.real_db.test_mysql_coordination import coordination as coordination

pytestmark = [
    pytest.mark.real_database,
    pytest.mark.parametrize("service", ["mysql"], indirect=True),
]
ROOT = Path(__file__).resolve().parents[2]


@pytest_asyncio.fixture
async def shared_servers(coordination, tmp_path, monkeypatch):
    database, pool, config, children = coordination
    monkeypatch.setattr(settings, "jwt_secret", SECRET)
    store = MySQLStore(pool)
    await store.init_tables()
    await store.save_setting(
        "llm",
        {
            "default_model": "fixture-model",
            "fallback_model": "",
            "max_tokens": 1024,
            "providers": {
                "fixture": {
                    "enabled": True,
                    "models": ["fixture-model"],
                    "api_key_enc": encrypt_secret("synthetic-provider-key"),
                    "base_url": "https://example.invalid/v1",
                }
            },
        },
    )
    environment = {
        k: v
        for k, v in os.environ.items()
        if k.upper()
        in {
            "PATH",
            "SYSTEMROOT",
            "WINDIR",
            "TEMP",
            "TMP",
            "HOME",
            "USERPROFILE",
            "USERNAME",
            "USER",
            "LOGNAME",
            "LNAME",
        }
    }
    environment.update(PYTHONPATH=str(ROOT), PYTHONUTF8="1")
    headers = {"Authorization": f"Bearer {create_access_token(1, 'fixture')}"}
    servers = []
    async with httpx.AsyncClient(trust_env=False, timeout=30, headers=headers) as client:
        try:
            for name in ("first", "second"):
                directory = tmp_path / name
                directory.mkdir()
                with (directory / "stderr.txt").open("wb") as stderr:
                    process = await asyncio.create_subprocess_exec(
                        sys.executable,
                        "-m",
                        "tests.fixtures.shared_query_worker",
                        "--port",
                        str(config["port"]),
                        "--database",
                        database,
                        cwd=directory,
                        env=environment,
                        stdout=asyncio.subprocess.DEVNULL,
                        stderr=stderr,
                    )
                children.append(process)
                async with asyncio.timeout(35):
                    while not (directory / "ready.json").exists():
                        if process.returncode is not None:
                            raise AssertionError((directory / "stderr.txt").read_text())
                        await asyncio.sleep(0.02)
                port = json.loads((directory / "ready.json").read_text())["port"]
                servers.append((f"http://127.0.0.1:{port}", directory, process))
            yield client, servers
        finally:
            for url, _, process in servers:
                if process.returncode is None:
                    try:
                        await client.post(url + "/probe/stop")
                        await asyncio.wait_for(process.wait(), 5)
                    except Exception:
                        if process.returncode is None:
                            process.kill()
                        await process.wait()


def body(session="shared"):
    return {
        "nl_text": "find order ids",
        "database": "fixture",
        "execute": True,
        "num_candidates": 1,
        "session_id": session,
    }


@pytest.mark.parametrize("protocol", ["json", "sse", "mcp"])
async def test_query_protocol_sessions_are_visible_in_another_process(shared_servers, protocol):
    client, servers = shared_servers
    first, second = servers[0][0], servers[1][0]
    if protocol == "mcp":
        response = await client.post(first + "/probe/mcp")
        result = response.json()
    elif protocol == "sse":
        response = await client.post(first + "/api/v1/query/stream", json=body())
        result = next(
            json.loads(block.split("data: ", 1)[1])
            for block in response.text.split("\n\n")
            if block.startswith("event: done\n")
        )
    else:
        response = await client.post(first + "/api/v1/query", json=body())
        result = response.json()
    assert response.status_code == 200, response.text
    assert result["status"] == "completed", result
    session_id = result["session_id"]
    other = await client.get(second + f"/api/v1/sessions/{session_id}")
    assert other.status_code == 200, other.text
    assert len(other.json()["session"]["turns"]) == 1
    versions = await client.get(second + f"/api/v1/sessions/{session_id}/versions")
    assert versions.json()["total"] == 1
    branch = await client.post(
        second + f"/api/v1/sessions/{session_id}/versions/1/branch", json={"edited_sql": "SELECT 2"}
    )
    assert branch.status_code == 200, branch.text
    rollback = await client.post(first + f"/api/v1/sessions/{session_id}/versions/1/rollback")
    assert rollback.status_code == 200, rollback.text
    assert (await client.get(second + f"/api/v1/sessions/{session_id}/versions")).json()[
        "total"
    ] == 3
    for url in (first, second):
        assert (await client.get(url + "/probe/local-sessions")).json() == {"count": 0}


async def test_corrupt_shared_state_fails_closed_and_releases_capacity(
    shared_servers, coordination
):
    client, servers = shared_servers
    first, second = servers[0][0], servers[1][0]
    response = await client.post(first + "/api/v1/query", json=body())
    assert response.status_code == 200, response.text
    _, pool, _, _ = coordination
    async with pool.acquire() as connection, connection.cursor() as cursor:
        await cursor.execute("UPDATE worker_sessions SET payload='private-invalid-json'")
    for response in (
        await client.get(second + "/api/v1/sessions/shared"),
        await client.post(second + "/api/v1/query", json=body()),
    ):
        assert response.status_code == 503, response.text
        assert response.json()["detail"]["code"] == "SHARED_STATE_UNAVAILABLE"
        assert "private-invalid-json" not in response.text
    assert not (servers[1][1] / "model-calls.txt").exists()
    response = await client.post(second + "/api/v1/query", json=body("fresh"))
    assert response.status_code == 200, response.text


async def test_global_capacity_rejects_before_second_model_call(shared_servers):
    client, servers = shared_servers
    first, directory, _ = servers[0]
    second, other_directory, _ = servers[1]
    (directory / "block-model").touch()
    pending = asyncio.create_task(client.post(first + "/api/v1/query", json=body("first")))
    try:
        async with asyncio.timeout(10):
            while not (directory / "model-entered").exists():
                await asyncio.sleep(0.02)
        rejected = await client.post(second + "/api/v1/query", json=body("second"))
        assert rejected.status_code == 503, rejected.text
        assert rejected.json()["detail"]["code"] == "QUERY_CAPACITY"
        assert not (other_directory / "model-calls.txt").exists()
    finally:
        (directory / "release-model").touch()
        completed = await pending
    assert completed.status_code == 200, completed.text
    assert (await client.post(second + "/api/v1/query", json=body("second"))).status_code == 200


async def test_shared_session_endpoints_preserve_operator_boundary(shared_servers):
    client, servers = shared_servers
    url = servers[0][0] + "/api/v1/sessions/missing"
    assert (await client.get(url, headers={"Authorization": ""})).status_code == 401
    token = create_access_token(99999, "outsider")
    assert (await client.get(url, headers={"Authorization": f"Bearer {token}"})).status_code == 403
