"""Restore synthetic worker control-plane state onto a second MySQL server."""

import asyncio
import os
from contextlib import suppress

import aiomysql
import pytest

from tests.real_db.test_immutable_release_rehearsal import stop_workers
from tests.real_db.test_mysql_coordination import coordination as coordination
from tests.real_db.test_shared_query_runtime import body
from tests.real_db.test_shared_query_runtime import shared_servers as shared_servers

pytestmark = [
    pytest.mark.real_database,
    pytest.mark.skipif(
        not os.getenv("DE_REAL_MYSQL_RECOVERY_PORT"), reason="Dedicated recovery MySQL required"
    ),
    pytest.mark.parametrize("service", ["mysql"], indirect=True),
    pytest.mark.parametrize("shared_servers", [{"immutable": True}], indirect=True),
]

TABLES = (
    "user_settings",
    "users",
    "worker_coordination_config",
    "worker_session_config",
    "worker_sessions",
    "worker_release_config",
    "worker_rate_config",
    "worker_rate_attempts",
)


async def relay(reader, writer):
    while chunk := await reader.read(65536):
        writer.write(chunk)
        await writer.drain()


async def test_control_plane_restore_to_distinct_mysql(shared_servers, coordination):
    recovery_port = int(os.environ["DE_REAL_MYSQL_RECOVERY_PORT"])
    database, source, config, _ = coordination
    assert 1024 <= recovery_port <= 65535 and recovery_port not in (3306, config["port"])
    client, workers = shared_servers
    await stop_workers(client, workers[:2])

    upstream = config["port"]
    connections = set()

    async def handle(reader, writer):
        current = asyncio.current_task()
        connections.add(current)
        peer = None
        pumps = []
        try:
            remote, peer = await asyncio.open_connection("127.0.0.1", upstream)
            pumps = [
                asyncio.create_task(relay(reader, peer)),
                asyncio.create_task(relay(remote, writer)),
            ]
            await asyncio.wait(pumps, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for pump in pumps:
                pump.cancel()
            await asyncio.gather(*pumps, return_exceptions=True)
            for stream in (writer, peer):
                if stream is not None:
                    stream.close()
                    with suppress(OSError):
                        await stream.wait_closed()
            connections.discard(current)

    admin_config = {**config, "port": recovery_port}
    admin_config.pop("db")
    admin = await aiomysql.connect(**admin_config, autocommit=True, connect_timeout=5)
    listener = await asyncio.start_server(handle, "127.0.0.1", 0)
    endpoint = listener.sockets[0].getsockname()[1]
    created = False
    session_path = "/api/v1/sessions/disaster"
    environment = {"SHARED_WORKER_NAMESPACE": database + "-dr"}
    try:
        async with admin.cursor() as cursor:
            await cursor.execute("SELECT @@server_uuid")
            destination_id = await cursor.fetchone()
            async with source.acquire() as connection, connection.cursor() as original:
                await original.execute("SELECT @@server_uuid")
                assert await original.fetchone() != destination_id
            await cursor.execute(f"CREATE DATABASE `{database}`")
            created = True
            await cursor.execute(f"USE `{database}`")

            original_worker = await workers.launch(
                "before-loss", overrides=environment, mysql_port=endpoint
            )
            response = await client.post(
                original_worker[0] + "/api/v1/query", json=body("disaster")
            )
            assert response.status_code == 200 and response.json()["status"] == "completed"
            saved = (await client.get(original_worker[0] + session_path)).json()["session"]
            await stop_workers(client, [original_worker])

            # Offline logical copy of only the declared synthetic control-plane tables.
            async with source.acquire() as connection, connection.cursor() as original:
                for table in TABLES:
                    await original.execute(f"SHOW CREATE TABLE `{table}`")
                    _, ddl = await original.fetchone()
                    await original.execute(f"SELECT * FROM `{table}`")
                    rows = await original.fetchall()
                    await cursor.execute(ddl)
                    if rows:
                        parameters = ",".join(["%s"] * len(rows[0]))
                        await cursor.executemany(
                            f"INSERT INTO `{table}` VALUES ({parameters})", rows
                        )

        upstream = recovery_port
        restored = []
        try:
            for name in ("after-loss-1", "after-loss-2"):
                restored.append(
                    await workers.launch(name, overrides=environment, mysql_port=endpoint)
                )
            for url, _, _ in restored:
                assert (await client.get(url + "/health/ready")).status_code == 200
                assert (await client.get(url + session_path)).json()["session"] == saved
            response = await client.post(restored[0][0] + "/api/v1/query", json=body("disaster"))
            assert response.status_code == 200 and response.json()["status"] == "completed"
            result = (await client.get(restored[1][0] + session_path)).json()["session"]
            assert len(result["turns"]) == len(saved["turns"]) + 1
        finally:
            await stop_workers(client, restored)
    finally:
        listener.close()
        await listener.wait_closed()
        running = list(connections)
        for task in running:
            task.cancel()
        await asyncio.gather(*running, return_exceptions=True)
        try:
            if created:
                async with admin.cursor() as cursor:
                    await cursor.execute(f"DROP DATABASE `{database}`")
        finally:
            await admin.ensure_closed()
