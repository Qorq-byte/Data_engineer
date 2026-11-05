"""Real MySQL serialization, cancellation and immutable shared provider limits."""

import asyncio
import hashlib

import pytest

from app.harness.mysql_coordination import CoordinationUnavailableError
from app.harness.mysql_rate_limit import MySQLProviderRateLimit
from app.workflow.contracts import QueryCapacityError
from tests.real_db.test_mysql_coordination import coordination as coordination

pytestmark = [
    pytest.mark.real_database,
    pytest.mark.parametrize("service", ["mysql"], indirect=True),
]


async def limiter_for(coordination):
    database, pool, _, _ = coordination
    limiter = MySQLProviderRateLimit(pool, hashlib.sha256(database.encode()).hexdigest()[:24])
    await limiter.initialize()
    return limiter


async def test_racing_instances_share_one_rolling_slot(coordination):
    first, second = await limiter_for(coordination), await limiter_for(coordination)
    results = await asyncio.gather(
        first.reserve("fixture", 1), second.reserve("fixture", 1), return_exceptions=True
    )
    assert sum(result is None for result in results) == 1
    assert sum(isinstance(result, QueryCapacityError) for result in results) == 1
    await second.reserve("other-provider", 1)
    with pytest.raises(CoordinationUnavailableError, match="mismatch"):
        await second.reserve("fixture", 0)


async def test_cancelled_waiter_does_not_charge_or_leave_transaction(coordination):
    limiter = await limiter_for(coordination)
    await limiter.reserve("fixture", 1)
    _, pool, _, _ = coordination
    key = hashlib.sha256(b"fixture").hexdigest()
    async with pool.acquire() as blocker, blocker.cursor() as cursor:
        await blocker.begin()
        await cursor.execute(
            "SELECT rpm FROM worker_rate_config WHERE namespace=%s AND provider_key=%s FOR UPDATE",
            (limiter.namespace, key),
        )
        pending = asyncio.create_task(limiter.reserve("fixture", 1))
        try:
            await asyncio.sleep(0.05)
            pending.cancel()
            with pytest.raises(asyncio.CancelledError):
                await pending
        finally:
            await blocker.rollback()
    async with pool.acquire() as connection, connection.cursor() as cursor:
        await cursor.execute("SELECT COUNT(*) FROM worker_rate_attempts")
        assert (await cursor.fetchone())[0] == 1
    await limiter.reserve("other-provider", 1)


async def test_unlimited_still_detects_worker_configuration_mismatch(coordination):
    limiter = await limiter_for(coordination)
    await limiter.reserve("fixture", 0)
    await limiter.reserve("fixture", 0)
    with pytest.raises(CoordinationUnavailableError):
        await limiter.reserve("fixture", 1)
    for invalid in (-1, 10001, True):
        with pytest.raises(ValueError):
            await limiter.reserve("fixture", invalid)
