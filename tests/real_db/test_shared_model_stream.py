"""A real killed MySQL model lease must cancel its owned streaming transport."""

import asyncio

import pytest

from app.harness.mysql_coordination import CoordinationUnavailableError, MySQLCoordinator
from app.harness.shared_query_runtime import SharedQueryRuntime, publish_shared_runtime
from app.llm.router import LiteLLMRouter, ProviderConfig, RouterConfig
from app.llm.usage import UsageCollector, active_usage
from app.storage.shared_sessions import MySQLSessionStore
from tests.real_db.test_mysql_coordination import coordination as coordination

pytestmark = [
    pytest.mark.real_database,
    pytest.mark.parametrize("service", ["mysql"], indirect=True),
]


async def test_stream_owner_loss_stops_transport_and_keeps_usage_unknown(coordination):
    database, pool, _, _ = coordination
    runtime = SharedQueryRuntime(
        MySQLCoordinator(pool, database, 1), MySQLSessionStore(pool, database)
    )
    runtime.model_coordinator = MySQLCoordinator(pool, database + ":model", 1)
    await runtime.initialize()
    await runtime.model_coordinator.initialize()
    provider = ProviderConfig("fixture", "UNUSED", api_key="synthetic")
    router = LiteLLMRouter(RouterConfig(), mock_mode=False)
    cleaned = []

    async def transport():
        try:
            yield "first"
            await asyncio.Event().wait()
        finally:
            cleaned.append(True)

    async def call(*args, **kwargs):
        return transport()

    router._call_provider = call
    collector = UsageCollector()
    token = active_usage.set(collector)
    publish_shared_runtime(runtime)
    stream = None
    try:
        stream = await router._limited_call(provider, "fixture", [], 0.3, 1, True)
        assert await anext(stream) == "first"
        async with pool.acquire() as connection, connection.cursor() as cursor:
            await cursor.execute(
                "SELECT IS_USED_LOCK(%s)", (f"de:{runtime.model_coordinator.namespace}:q:0",)
            )
            identity = (await cursor.fetchone())[0]
            assert type(identity) is int
            await cursor.execute(f"KILL CONNECTION {identity}")
        with pytest.raises(CoordinationUnavailableError):
            await asyncio.wait_for(anext(stream), 5)
        assert cleaned
        assert collector.summary()["missing_usage_count"] == 1
        assert collector.records[0].status == "cancelled"
        assert collector.records[0].completion_tokens is None
        async with runtime.model_coordinator.query("next"):
            pass
    finally:
        if stream is not None:
            await stream.aclose()
        publish_shared_runtime(None)
        active_usage.reset(token)
    assert not any(t.get_name() == "shared-model-stream" for t in asyncio.all_tasks())
