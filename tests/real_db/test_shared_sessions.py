"""Real MySQL CAS, retention and independent-process session visibility."""

import asyncio
import json
import os
import sys
from pathlib import Path

import aiomysql
import pytest

from app.harness.mysql_coordination import MySQLCoordinator
from app.storage.shared_sessions import (
    MySQLSessionStore,
    SessionCapacityError,
    SessionConflictError,
    SessionStoreUnavailableError,
)
from tests.real_db.test_mysql_coordination import coordination as coordination

pytestmark = [
    pytest.mark.real_database,
    pytest.mark.parametrize("service", ["mysql"], indirect=True),
]
ROOT = Path(__file__).resolve().parents[2]


def payload(session="shared", value=0):
    return {"session_id": session, "turns": [{"sql": f"SELECT {value}"}], "value": value}


async def store_for(fixture, capacity=2):
    database, pool, _, _ = fixture
    store = MySQLSessionStore(pool, database, max_entries=capacity, ttl_seconds=60)
    await store.initialize()
    return store


async def spawn(fixture, directory, mode, request, barrier):
    database, _, config, children = fixture
    directory.mkdir()
    (directory / "input.json").write_text(json.dumps(request), encoding="utf-8")
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
    environment.update(
        PYTHONPATH=str(ROOT),
        PYTHONUTF8="1",
        LLM_MOCK_MODE="1",
        LITELLM_LOCAL_MODEL_COST_MAP="True",
        RAG_EMBEDDING_PROVIDER="mock",
    )
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "tests.fixtures.session_worker",
        mode,
        "--database",
        database,
        "--port",
        str(config["port"]),
        "--barrier",
        str(barrier),
        env=environment,
        cwd=directory,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    children.append(process)
    return process


async def wait_file(path, processes):
    async with asyncio.timeout(15):
        while not path.exists():
            assert all(p.returncode in (None, 0) for p in processes)
            await asyncio.sleep(0.02)


async def test_independent_writers_cannot_overwrite_the_same_revision(coordination, tmp_path):
    store = await store_for(coordination)
    revision = await store.put("shared", payload(), None)
    barrier = tmp_path / "start"
    first = await spawn(
        coordination,
        tmp_path / "first",
        "write",
        {"revision": revision, "payload": payload(value=1)},
        barrier,
    )
    second = await spawn(
        coordination,
        tmp_path / "second",
        "write",
        {"revision": revision, "payload": payload(value=2)},
        barrier,
    )
    await wait_file(tmp_path / "first/ready", [first, second])
    await wait_file(tmp_path / "second/ready", [first, second])
    barrier.touch()
    assert await asyncio.wait_for(first.wait(), 10) == 0
    assert await asyncio.wait_for(second.wait(), 10) == 0
    results = [
        json.loads((tmp_path / name / "result.json").read_text()) for name in ("first", "second")
    ]
    assert sum(r["committed"] for r in results) == 1
    current = await store.get("shared")
    assert current.revision != revision
    assert current.payload["value"] in (1, 2)
    reader = await spawn(coordination, tmp_path / "reader", "read", {}, barrier)
    assert await asyncio.wait_for(reader.wait(), 15) == 0
    result = json.loads((tmp_path / "reader/result.json").read_text())
    assert result["payload"] == current.payload
    assert result["revision"] == current.revision


async def expire(fixture):
    _, pool, _, _ = fixture
    async with pool.acquire() as conn, conn.cursor() as cur:
        await cur.execute(
            "UPDATE worker_sessions SET expires_at=DATE_SUB(UTC_TIMESTAMP(),INTERVAL 1 SECOND)"
        )


async def test_expiry_and_recreation_never_accept_old_revision(coordination):
    store = await store_for(coordination)
    old = await store.put("shared", payload(), None)
    await expire(coordination)
    assert await store.get("shared") is None
    new = await store.put("shared", payload(value=1), None)
    assert new != old
    with pytest.raises(SessionConflictError):
        await store.put("shared", payload(value=2), old)
    assert (await store.get("shared")).payload["value"] == 1


async def test_capacity_and_active_expired_session_protection(coordination):
    database, pool, _, _ = coordination
    store = await store_for(coordination, capacity=1)
    coordinator = MySQLCoordinator(pool, database, 1)
    await coordinator.initialize()
    revision = await store.put("shared", payload(), None)
    async with coordinator.query("shared"):
        await expire(coordination)
        with pytest.raises(SessionCapacityError):
            await store.put("other", payload("other"), None)
        await store.put("shared", payload(value=1), revision)
    await expire(coordination)
    await store.put("other", payload("other"), None)
    assert await store.get("shared") is None
    assert (await store.get("other")).payload["session_id"] == "other"


async def test_config_mismatch_and_corruption_fail_closed(coordination):
    database, pool, _, _ = coordination
    store = await store_for(coordination)
    incompatible = MySQLSessionStore(pool, database, max_entries=3, ttl_seconds=60)
    with pytest.raises(SessionStoreUnavailableError):
        await incompatible.initialize()
    await store.put("shared", payload(), None)
    async with pool.acquire() as conn, conn.cursor() as cur:
        await cur.execute("UPDATE worker_sessions SET payload='private-broken-json'")
    with pytest.raises(SessionStoreUnavailableError) as caught:
        await store.get("shared")
    assert "private" not in str(caught.value)


async def test_cancelled_transaction_does_not_commit_or_block_later_writes(coordination):
    _, _, config, _ = coordination
    store = await store_for(coordination)
    admin = await aiomysql.connect(**config, autocommit=False)
    try:
        async with admin.cursor() as cur:
            await cur.execute("START TRANSACTION")
            await cur.execute("SELECT * FROM worker_session_config FOR UPDATE")
        task = asyncio.create_task(store.put("shared", payload(), None))
        await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await admin.rollback()
    finally:
        await admin.ensure_closed()
    assert await store.get("shared") is None
    await store.put("shared", payload(), None)
    assert (await store.get("shared")).payload == payload()


async def test_snapshot_bounds_and_read_isolation(coordination):
    store = await store_for(coordination)
    with pytest.raises(ValueError):
        await store.put("shared", {**payload(), "large": "x" * (1024 * 1024)}, None)
    await store.put("shared", payload(), None)
    record = await store.get("shared")
    record.payload["turns"].append({"sql": "SELECT 999"})
    assert (await store.get("shared")).payload == payload()
