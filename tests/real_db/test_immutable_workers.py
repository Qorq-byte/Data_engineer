"""Production lifespan, frozen configuration and fresh-process recovery on real MySQL."""

import asyncio
import json

import pytest

from tests.real_db.test_mysql_coordination import coordination as coordination
from tests.real_db.test_shared_query_runtime import body
from tests.real_db.test_shared_query_runtime import shared_servers as shared_servers

pytestmark = [
    pytest.mark.real_database,
    pytest.mark.parametrize("service", ["mysql"], indirect=True),
    pytest.mark.parametrize("shared_servers", [{"immutable": True}], indirect=True),
]


async def test_actual_startup_restores_connections_and_restart_keeps_session(shared_servers):
    client, servers = shared_servers
    first, second = servers[0][0], servers[1][0]
    for url in (first, second):
        readiness = await client.get(url + "/health/ready")
        assert readiness.status_code == 200, readiness.text
        assert readiness.json()["components"]["shared_worker"] is True
        assert readiness.json()["components"]["rag"] is True
    response = await client.post(first + "/api/v1/query", json=body())
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "completed", response.text
    servers[0][2].kill()
    await servers[0][2].wait()
    replacement, _, process = await servers.launch("replacement")
    assert process.pid != servers[0][2].pid
    restored = await client.get(replacement + "/api/v1/sessions/shared")
    assert len(restored.json()["session"]["turns"]) == 1
    response = await client.post(replacement + "/api/v1/query", json=body())
    assert response.status_code == 200, response.text
    assert (
        len((await client.get(second + "/api/v1/sessions/shared")).json()["session"]["turns"]) == 2
    )


async def test_online_maintenance_is_rejected_after_authentication(shared_servers):
    client, servers = shared_servers
    url = servers[0][0]
    for method, path in (
        ("PUT", "/api/v1/settings/llm"),
        ("POST", "/api/v1/databases/connect"),
        ("POST", "/api/v1/schema/fixture/refresh"),
        ("POST", "/api/v1/feedback"),
    ):
        rejected = await client.request(method, url + path, json={})
        assert rejected.status_code == 409, rejected.text
        assert rejected.json()["detail"]["code"] == "OFFLINE_MAINTENANCE_REQUIRED"
    rejected = await client.put(
        url + "/api/v1/settings/llm", json={}, headers={"Authorization": ""}
    )
    assert rejected.status_code == 401


async def test_persisted_configuration_drift_stops_queries_and_readiness(
    shared_servers, coordination
):
    client, servers = shared_servers
    url, directory, _ = servers[0]
    _, pool, _, _ = coordination
    async with pool.acquire() as connection, connection.cursor() as cursor:
        await cursor.execute("SELECT config FROM user_settings WHERE section='llm'")
        original = (await cursor.fetchone())[0]
        changed = json.loads(original) if isinstance(original, str) else original.copy()
        changed["max_tokens"] = 2048
        await cursor.execute(
            "UPDATE user_settings SET config=%s WHERE section='llm'", (json.dumps(changed),)
        )
    try:
        response = await client.post(url + "/api/v1/query", json=body())
        assert response.status_code == 503, response.text
        assert response.json()["detail"]["code"] == "SHARED_STATE_UNAVAILABLE"
        assert (await client.get(url + "/health/ready")).status_code == 503
        assert not (directory / "model-calls.txt").exists()
    finally:
        async with pool.acquire() as connection, connection.cursor() as cursor:
            await cursor.execute(
                "UPDATE user_settings SET config=%s WHERE section='llm'", (original,)
            )


async def test_global_model_capacity_precedes_transport_in_another_worker(shared_servers):
    client, servers = shared_servers
    first, directory, _ = servers[0]
    second, other_directory, _ = servers[1]
    (directory / "block-model").touch()
    pending = asyncio.create_task(client.post(first + "/api/v1/query", json=body("first")))
    try:
        async with asyncio.timeout(10):
            while not (directory / "model-entered").exists():
                await asyncio.sleep(0.02)
        response = await client.post(second + "/api/v1/query", json=body("second"))
        assert response.status_code == 503, response.text
        assert response.json()["detail"]["code"] == "QUERY_CAPACITY"
        assert not (other_directory / "model-calls.txt").exists()
    finally:
        (directory / "release-model").touch()
        response = await pending
    assert response.status_code == 200, response.text
    response = await client.post(second + "/api/v1/query", json=body("second"))
    assert response.status_code == 200, response.text
