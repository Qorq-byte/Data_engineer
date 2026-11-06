"""Cross-process lock exclusion against an owned MySQL database."""

import asyncio
import json
import os
import sys
import uuid
from pathlib import Path

import aiomysql
import pytest
import pytest_asyncio

from app.harness.mysql_coordination import (
    CoordinationBusyError,
    CoordinationUnavailableError,
    MySQLCoordinator,
)
from tests.real_db.support import admin_connection, admin_sql

pytestmark = [
    pytest.mark.real_database,
    pytest.mark.parametrize("service", ["mysql"], indirect=True),
]
ROOT = Path(__file__).resolve().parents[2]


@pytest_asyncio.fixture
async def coordination(service):
    database = f"de_coord66_{uuid.uuid4().hex}"
    async with admin_connection(service) as admin:
        await admin_sql(service, admin, f"CREATE DATABASE `{database}`")
        config = service.config(admin=True)
        config["db"] = database
        del config["database"]
        pool = await aiomysql.create_pool(**config, autocommit=True, minsize=1, maxsize=4)
        children = []
        try:
            yield database, pool, config, children
        finally:
            for child in children:
                if child.returncode is None:
                    child.kill()
                await child.wait()
            pool.close()
            await pool.wait_closed()
            await admin_sql(service, admin, f"DROP DATABASE `{database}`")


async def launch(fixture, directory, mode, capacity=1, session="shared"):
    database, _, config, children = fixture
    directory.mkdir()
    keep = {
        key: value
        for key, value in os.environ.items()
        if key.upper()
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
    keep.update(
        PYTHONPATH=str(ROOT),
        PYTHONUTF8="1",
        LITELLM_LOCAL_MODEL_COST_MAP="True",
        LLM_MOCK_MODE="1",
        RAG_EMBEDDING_PROVIDER="mock",
    )
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "tests.fixtures.coordination_worker",
        mode,
        "--port",
        str(config["port"]),
        "--database",
        database,
        "--capacity",
        str(capacity),
        "--session",
        session,
        cwd=directory,
        env=keep,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    children.append(process)
    async with asyncio.timeout(15):
        while not (directory / "result.json").exists():
            if process.returncode is not None:
                raise AssertionError("Coordination fixture failed before reporting")
            await asyncio.sleep(0.02)
    return process, json.loads((directory / "result.json").read_text(encoding="utf-8"))


async def test_global_capacity_shared_across_processes(coordination, tmp_path):
    holder, first = await launch(coordination, tmp_path / "holder", "hold")
    assert first["entered"]
    other, second = await launch(coordination, tmp_path / "other", "try", session="different")
    assert not second["entered"]
    assert await asyncio.wait_for(other.wait(), 5) == 0
    (tmp_path / "holder/release").touch()
    assert await asyncio.wait_for(holder.wait(), 5) == 0
    third, result = await launch(coordination, tmp_path / "third", "try")
    assert result["entered"]
    assert await asyncio.wait_for(third.wait(), 5) == 0


async def test_session_exclusion_does_not_leak_global_slot(coordination, tmp_path):
    holder, _ = await launch(coordination, tmp_path / "holder", "hold", capacity=2)
    other, result = await launch(coordination, tmp_path / "same", "try", capacity=2)
    assert not result["entered"]
    assert await asyncio.wait_for(other.wait(), 5) == 0
    third, result = await launch(
        coordination, tmp_path / "different", "try", capacity=2, session="different"
    )
    assert result["entered"]
    assert await asyncio.wait_for(third.wait(), 5) == 0
    (tmp_path / "holder/release").touch()
    assert await asyncio.wait_for(holder.wait(), 5) == 0


async def test_process_death_releases_server_locks(coordination, tmp_path):
    database, pool, _, _ = coordination
    holder, _ = await launch(coordination, tmp_path / "holder", "hold")
    holder.kill()
    await holder.wait()
    coordinator = MySQLCoordinator(pool, database, 1)
    await coordinator.initialize()
    async with asyncio.timeout(5):
        while True:
            try:
                async with coordinator.query("shared"):
                    break
            except CoordinationBusyError:
                await asyncio.sleep(0.02)


async def test_capacity_mismatch_is_rejected(coordination):
    database, pool, _, _ = coordination
    first = MySQLCoordinator(pool, database, 1)
    await first.initialize()
    second = MySQLCoordinator(pool, database, 2)
    with pytest.raises(CoordinationUnavailableError):
        await second.initialize()
    with pytest.raises(CoordinationUnavailableError):
        async with second.query("session"):
            pytest.fail("Unconfirmed coordination cannot grant a lease")


@pytest.mark.parametrize("failure", ["exception", "cancel", "connection_loss"])
async def test_failed_owner_does_not_recycle_held_locks(coordination, failure):
    database, pool, config, _ = coordination
    coordinator = MySQLCoordinator(pool, database, 1)
    await coordinator.initialize()
    entered = asyncio.Event()

    async def request():
        async with coordinator.query("shared") as lease:
            entered.set()
            if failure == "exception":
                raise ValueError("synthetic")
            if failure == "connection_loss":
                admin = await aiomysql.connect(**config, autocommit=True)
                try:
                    async with admin.cursor() as cursor:
                        await cursor.execute(f"KILL CONNECTION {lease.connection_id}")
                finally:
                    await admin.ensure_closed()
            else:
                await asyncio.Event().wait()

    task = asyncio.create_task(request())
    await entered.wait()
    if failure == "cancel":
        task.cancel()
    expected = {
        "exception": ValueError,
        "cancel": asyncio.CancelledError,
        "connection_loss": CoordinationUnavailableError,
    }[failure]
    with pytest.raises(expected):
        await asyncio.wait_for(task, 5)
    async with coordinator.query("shared"):
        pass


async def test_connection_loss_cancels_inflight_owner_before_scope_exits(coordination):
    database, pool, config, _ = coordination
    coordinator = MySQLCoordinator(pool, database, 1)
    await coordinator.initialize()
    entered = asyncio.Future()
    cleaned_up = asyncio.Event()

    async def request():
        async with coordinator.query("shared") as lease:
            entered.set_result(lease.connection_id)
            try:
                await asyncio.Event().wait()
            finally:
                cleaned_up.set()

    task = asyncio.create_task(request())
    identity = await asyncio.wait_for(entered, 5)
    admin = await aiomysql.connect(**config, autocommit=True)
    try:
        async with admin.cursor() as cursor:
            await cursor.execute(f"KILL CONNECTION {identity}")
        with pytest.raises(CoordinationUnavailableError):
            await asyncio.wait_for(task, 5)
        assert cleaned_up.is_set()
        assert not any(t.get_name() == "mysql-query-lease-watchdog" for t in asyncio.all_tasks())
        async with coordinator.query("shared"):
            pass
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await admin.ensure_closed()
