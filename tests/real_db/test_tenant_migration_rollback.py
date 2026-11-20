"""Rehearse a tenant schema upgrade and restore the old application on fresh MySQL."""

import asyncio
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
from collections import Counter
from contextlib import asynccontextmanager, suppress

import httpx
import pytest

from app.auth.security import hash_password
from app.config.settings import settings
from app.security.crypto import encrypt_secret
from app.storage.mysql_store import MySQLStore
from tests.fixtures.immutable_release import build_release
from tests.fixtures.shared_query_worker import SECRET
from tests.real_db.support import Service, admin_connection, admin_sql
from tests.real_db.test_immutable_release_rehearsal import stop_workers
from tests.real_db.test_mysql_coordination import coordination as coordination
from tests.real_db.test_mysql_worker_disaster import relay
from tests.real_db.test_shared_query_runtime import ROOT, body, launch_worker, worker_environment

pytestmark = [
    pytest.mark.real_database,
    pytest.mark.skipif(
        not os.getenv("DE_REAL_MYSQL_RECOVERY_PORT"), reason="Dedicated recovery MySQL required",
    ),
    pytest.mark.parametrize("service", ["mysql"], indirect=True),
]

LEGACY_COMMIT = "c92fe9a51c5d256a981f84365e6e31bcd55bd0cf"
PASSWORD = "tenant-rollback-fixture"
OWNER = "legacy@example.test"
ADDED = "candidate@example.test"
SETTINGS_PATH = "/api/v1/settings/llm"
RAG_PATH = "/api/v1/learning/rag/stats"
LEGACY_SESSION = "/api/v1/sessions/before-migration"
CANDIDATE_SESSION = "/api/v1/sessions/after-migration"


def extract_legacy_code(root):
    source, archive = root / "legacy-code", root / "legacy.tar"
    subprocess.run(
        ["git", "archive", "--format=tar", "--output", str(archive), LEGACY_COMMIT],
        cwd=ROOT, check=True, timeout=30,
    )
    with tarfile.open(archive) as package:
        package.extractall(source, filter="data")
    return source


async def seed_legacy_code(root, cwd, config, llm):
    # Run the actual legacy initializer against an empty test database.
    script = """
import asyncio, json, sys
from app.storage.mysql_store import MySQLStore

async def initialize():
    store = await MySQLStore.create(json.loads(sys.argv[1]))
    try:
        await store.init_tables()
        await store.seed_defaults()
        await store.save_setting('llm', json.loads(sys.argv[2]))
        await store.save_domain({'name': 'ecommerce', 'label_en': 'Legacy sales'})
        await store.save_glossary_term('ecommerce', {'term': 'Legacy order'})
        await store.save_rule('ecommerce', {'id': 'legacy-rule', 'description': 'Legacy rule'})
    finally:
        await store.close()

asyncio.run(initialize())
"""
    run = await asyncio.to_thread(
        subprocess.run, [sys.executable, "-c", script, json.dumps(config), json.dumps(llm)],
        cwd=cwd, env=worker_environment(root), capture_output=True, timeout=35, text=True,
    )
    assert run.returncode == 0, run.stderr


@asynccontextmanager
async def control_endpoint(port):
    """Keep the environment fingerprint stable when switching control-plane servers."""
    upstream = port
    handlers = set()

    def switch_to(new_port):
        nonlocal upstream
        upstream = new_port

    async def handle(reader, writer):
        task = asyncio.current_task()
        handlers.add(task)
        peer = None
        try:
            remote, peer = await asyncio.open_connection("127.0.0.1", upstream)
            async with asyncio.TaskGroup() as group:
                pumps = [
                    group.create_task(relay(reader, peer)),
                    group.create_task(relay(remote, writer)),
                ]
                _, pending = await asyncio.wait(pumps, return_when=asyncio.FIRST_COMPLETED)
                for pump in pending:
                    pump.cancel()
        finally:
            for stream in (writer, peer):
                if stream is not None:
                    stream.close()
                    with suppress(OSError):
                        await stream.wait_closed()
            handlers.discard(task)

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    async with server:
        try:
            yield server.sockets[0].getsockname()[1], switch_to
        finally:
            server.close()
            tasks = list(handlers)
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)


async def snapshot_control_plane(cursor):
    await cursor.execute("SHOW FULL TABLES")
    tables = sorted(await cursor.fetchall())
    assert tables
    snapshot = {}
    for table, kind in tables:
        assert kind == "BASE TABLE" and re.fullmatch(r"[a-z0-9_]+", table)
        await cursor.execute(f"SHOW CREATE TABLE `{table}`")
        _, ddl = await cursor.fetchone()
        await cursor.execute(f"SHOW FULL COLUMNS FROM `{table}`")
        columns = await cursor.fetchall()
        await cursor.execute(f"SELECT * FROM `{table}`")
        snapshot[table] = (ddl, columns, await cursor.fetchall())
    return snapshot


def normalized_table_ddl(ddl):
    # MySQL may expand a column's charset when the restored DDL declares its collation explicitly.
    return re.sub(r"\bCHARACTER SET ([a-z0-9]+) (?=COLLATE \1_[a-z0-9_]+\b)", "", ddl)


async def restore_control_plane(cursor, snapshot):
    # Restore the exact constraint definitions, including the legacy foreign keys.
    await cursor.execute("SET SESSION FOREIGN_KEY_CHECKS = 0")
    try:
        for table, (ddl, _, rows) in snapshot.items():
            await cursor.execute(ddl)
            if rows:
                placeholders = ",".join(["%s"] * len(rows[0]))
                await cursor.executemany(f"INSERT INTO `{table}` VALUES ({placeholders})", rows)
    finally:
        await cursor.execute("SET SESSION FOREIGN_KEY_CHECKS = 1")
    restored = await snapshot_control_plane(cursor)
    assert restored.keys() == snapshot.keys()
    for table, (ddl, columns, rows) in snapshot.items():
        restored_ddl, restored_columns, restored_rows = restored[table]
        assert normalized_table_ddl(restored_ddl) == normalized_table_ddl(ddl), table
        assert restored_columns == columns, table
        assert Counter(restored_rows) == Counter(rows), table


async def ready(client, worker):
    response = await client.get(worker[0] + "/health/ready")
    assert response.status_code == 200, response.text
    assert response.json()["components"]["shared_worker"] is True
    assert response.json()["components"]["rag"] is True


async def execute(client, worker, session):
    response = await client.post(worker[0] + "/api/v1/query", json=body(session))
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["status"] == "completed", response.text
    assert result["execution"]["rows"] == [[10], [20], [30], [40], [50]]


async def login(client, worker, email):
    return await client.post(worker[0] + "/api/v1/auth/login", json={
        "email": email, "password": PASSWORD,
    })


async def test_tenant_migration_rollback_restores_old_workers(
    service, coordination, tmp_path, monkeypatch,
):
    database, pool, config, children = coordination
    recovery_port = int(os.environ["DE_REAL_MYSQL_RECOVERY_PORT"])
    assert 1024 <= recovery_port <= 65535 and recovery_port not in (3306, config["port"])
    recovery = Service("mysql", recovery_port)
    monkeypatch.setattr(settings, "jwt_secret", SECRET)
    legacy_root = await asyncio.to_thread(extract_legacy_code, tmp_path)
    legacy_files, candidate_files = tmp_path / "original", tmp_path / "candidate"
    legacy_files.mkdir()
    candidate_files.mkdir()
    llm = {
        "default_model": "fixture-model", "fallback_model": "", "max_tokens": 1024,
        "providers": {"fixture": {
            "enabled": True, "models": ["fixture-model"],
            "api_key_enc": encrypt_secret("synthetic-provider-key"),
            "base_url": "https://example.invalid/v1", "rate_limit_rpm": 60,
        }},
    }
    await seed_legacy_code(legacy_root, tmp_path, config, llm)
    legacy_env = {
        **worker_environment(legacy_root),
        **await build_release(config, legacy_files, code_root=legacy_root),
        "SHARED_WORKER_NAMESPACE": database, "REGISTRATION_TOKEN": "fixture-invitation",
    }
    candidate_env = {
        **worker_environment(), **await build_release(config, candidate_files),
        "SHARED_WORKER_NAMESPACE": database + "-candidate", "ADMIN_USER_IDS": "[1,2]",
    }

    async with (
        control_endpoint(config["port"]) as (endpoint, switch_to),
        httpx.AsyncClient(trust_env=False, timeout=30) as client,
        admin_connection(recovery) as destination,
    ):
        async def launch(name, environment):
            worker = await launch_worker(
                tmp_path / name, environment=environment, port=endpoint, database=database,
                children=children,
            )
            await ready(client, worker)
            return worker

        before = [await launch(f"legacy-{index}", legacy_env) for index in (1, 2)]
        account = await client.post(
            before[0][0] + "/api/v1/auth/register",
            json={"username": "legacy", "email": OWNER, "password": PASSWORD},
            headers={"X-Registration-Token": "fixture-invitation"},
        )
        assert account.status_code == 201, account.text
        original_login = await login(client, before[1], OWNER)
        assert original_login.status_code == 200
        legacy_token = original_login.json()["access_token"]
        client.headers["Authorization"] = f"Bearer {legacy_token}"
        await execute(client, before[0], "before-migration")
        saved = (await client.get(before[1][0] + LEGACY_SESSION)).json()["session"]
        old_rag = (await client.get(before[1][0] + RAG_PATH)).json()
        assert old_rag["schema"]["lancedb_count"] > 0 and old_rag["schema"]["bm25_count"] > 0
        await stop_workers(client, before)

        async with pool.acquire() as connection, connection.cursor() as cursor:
            await cursor.execute("SELECT @@server_uuid")
            origin_uuid = await cursor.fetchone()
            assert re.fullmatch(r"de_coord66_[0-9a-f]{32}", database)
            await cursor.execute(f"SHOW CREATE DATABASE `{database}`")
            _, database_ddl = await cursor.fetchone()
            backup = await snapshot_control_plane(cursor)
        assert {"users", "domains", "worker_sessions", "worker_release_config"} <= backup.keys()
        assert all("`tenant_id`" not in backup[name][0] for name in ("users", "domains"))
        archive = tmp_path / "original-files"
        assert archive.resolve().parent == legacy_files.resolve().parent == tmp_path.resolve()
        shutil.move(legacy_files, archive)

        candidate = [await launch(f"candidate-{index}", candidate_env) for index in (1, 2)]
        store = MySQLStore(pool)
        assert (await store.get_setting("llm"))["max_tokens"] == llm["max_tokens"]
        assert (await store._fetchone("SELECT tenant_id FROM users WHERE id=1"))["tenant_id"] == 1
        assert (await store.get_domain("ecommerce"))["tenant_id"] == 1
        migrated_login = await login(client, candidate[0], OWNER)
        assert migrated_login.status_code == 200 and migrated_login.json()["user"]["tenant_id"] == 1
        assert (await client.get(candidate[1][0] + LEGACY_SESSION)).status_code == 404
        await execute(client, candidate[0], "after-migration")
        assert (await client.get(candidate[1][0] + CANDIDATE_SESSION)).status_code == 200
        await store._execute(
            "INSERT INTO users (id,tenant_id,username,email,password_hash) VALUES (2,2,%s,%s,%s)",
            ("candidate", ADDED, hash_password(PASSWORD)),
        )
        added_login = await login(client, candidate[1], ADDED)
        assert added_login.status_code == 200 and added_login.json()["user"]["tenant_id"] == 2
        denied = await client.get(candidate[1][0] + "/api/v1/databases", headers={
            "Authorization": "Bearer " + added_login.json()["access_token"],
        })
        assert denied.status_code == 403
        await stop_workers(client, candidate)
        await store.save_setting("llm", {**llm, "max_tokens": 2048})

        assert await admin_sql(recovery, destination, "SELECT @@server_uuid") != (origin_uuid,)
        await admin_sql(recovery, destination, database_ddl)
        try:
            async with destination.cursor() as cursor:
                await cursor.execute(f"USE `{database}`")
                await restore_control_plane(cursor, backup)
            shutil.copytree(archive, legacy_files)
            switch_to(recovery_port)
            restored = [await launch(f"restored-{index}", legacy_env) for index in (1, 2)]
            for worker in restored:
                url = worker[0]
                user = await login(client, worker, OWNER)
                assert user.status_code == 200, user.text
                assert user.json()["user"] == original_login.json()["user"]
                assert (await login(client, worker, ADDED)).status_code == 401
                assert (await client.get(url + SETTINGS_PATH)).json()["max_tokens"] == 1024
                assert (await client.get(url + RAG_PATH)).json() == old_rag
                assert (await client.get(url + LEGACY_SESSION)).json()["session"] == saved
                assert (await client.get(url + CANDIDATE_SESSION)).status_code == 404
            await execute(client, restored[0], "before-migration")
            resumed = (await client.get(restored[1][0] + LEGACY_SESSION)).json()["session"]
            assert len(resumed["turns"]) == len(saved["turns"]) + 1
            await stop_workers(client, restored)
        finally:
            await admin_sql(recovery, destination, f"DROP DATABASE `{database}`")
