"""Cancellation provenance and monitor lifetime without external services."""

import asyncio

import pytest

from app.harness.mysql_coordination import CoordinationUnavailableError, MySQLCoordinator


def assert_joined():
    assert not any(t.get_name() == "mysql-query-lease-watchdog" for t in asyncio.all_tasks())


@pytest.mark.parametrize("exit_reason", ["normal", "failure", "cancel", "timeout"])
async def test_scope_exit_keeps_original_outcome_and_joins_monitor(exit_reason):
    coordinator = MySQLCoordinator(None, "fixture", 1)
    entered = asyncio.Event()

    async def request():
        async with coordinator._watched_lease(None, ["lock"], 123):
            entered.set()
            if exit_reason == "failure":
                raise ValueError("original")
            if exit_reason != "normal":
                async with asyncio.timeout(0.02 if exit_reason == "timeout" else None):
                    await asyncio.Event().wait()

    task = asyncio.create_task(request())
    await entered.wait()
    if exit_reason == "cancel":
        task.cancel()
    if exit_reason == "normal":
        await task
    else:
        expected = {
            "failure": ValueError,
            "cancel": asyncio.CancelledError,
            "timeout": TimeoutError,
        }[exit_reason]
        with pytest.raises(expected):
            await task
    assert_joined()


@pytest.mark.parametrize("result", [None, 456, "unavailable"])
async def test_ownership_loss_cancels_body_before_return(monkeypatch, result):
    coordinator = MySQLCoordinator(None, "fixture", 1)
    cleaned = []

    async def scalar(*args):
        if result == "unavailable":
            raise OSError("private transport detail")
        return result

    monkeypatch.setattr(coordinator, "_scalar", scalar)
    with pytest.raises(CoordinationUnavailableError, match="ownership lost") as error:
        async with coordinator._watched_lease(None, ["lock"], 123):
            try:
                await asyncio.Event().wait()
            finally:
                cleaned.append(True)
    assert cleaned
    assert "private" not in str(error.value)
    assert_joined()


async def test_repeated_cancellation_waits_for_inflight_probe(monkeypatch):
    coordinator = MySQLCoordinator(None, "fixture", 1)
    probing = asyncio.Event()
    released = asyncio.Event()

    async def scalar(*args):
        probing.set()
        await released.wait()
        return 123

    monkeypatch.setattr(coordinator, "_scalar", scalar)

    async def request():
        async with coordinator._watched_lease(None, ["lock"], 123):
            await asyncio.Event().wait()

    task = asyncio.create_task(request())
    await asyncio.wait_for(probing.wait(), 2)
    try:
        task.cancel()
        await asyncio.sleep(0)
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done()
    finally:
        released.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert_joined()
