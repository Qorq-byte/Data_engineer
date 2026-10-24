import asyncio
from types import SimpleNamespace

import pytest

from app.llm.router import LiteLLMRouter, ProviderConfig, ProviderServerError, RouterConfig
from app.llm.usage import UsageCollector, active_usage, observe


def router():
    return LiteLLMRouter(
        RouterConfig(
            providers={"p": ProviderConfig("p", "", ["m"])},
            default_model="m",
            fallback_chain=[],
            max_retries=1,
            retry_backoff_seconds=0,
        )
    )


@pytest.mark.parametrize(
    "value,expected", [(0, 0), (3, 3), (True, None), (-1, None), (1.5, None), (None, None)]
)
def test_usage_validation(value, expected):
    collector = UsageCollector()
    with observe(collector, "p", "m", "adapter"):
        collector.observe(SimpleNamespace(usage=SimpleNamespace(prompt_tokens=value)))
    assert collector.summary()["attempts"][0]["prompt_tokens"] == expected


async def test_isolation_retry_and_reset():
    r = router()

    async def call(provider, model, messages, *args, **kwargs):
        await asyncio.sleep(0)
        if messages[0]["content"] == "retry":
            messages[0]["content"] = "ok"
            raise ProviderServerError("private")
        return {
            "usage": {"prompt_tokens": 3, "completion_tokens": 0},
            "choices": [{"finish_reason": "stop"}],
        }

    r._call_provider = call

    async def request(text):
        c = UsageCollector()
        token = active_usage.set(c)
        try:
            await r.complete([{"content": text}])
            return c.summary()
        finally:
            active_usage.reset(token)

    a, b = await asyncio.gather(request("retry"), request("ok"))
    assert [a["attempt_count"], b["attempt_count"]] == [2, 1]
    assert a["known_prompt_tokens"] == b["known_prompt_tokens"] == 3
    assert a["missing_usage_count"] == 1
    assert active_usage.get() is None


async def test_stream_close_releases_and_never_succeeds_early():
    r = router()
    closed = []

    async def source():
        try:
            yield "hello"
            await asyncio.sleep(10)
        finally:
            closed.append(True)

    async def call(*args, **kwargs):
        return source()

    r._call_provider = call
    c = UsageCollector()
    token = active_usage.set(c)
    try:
        stream = r.complete_stream([])
        assert await anext(stream) == "hello"
        assert c.summary()["attempts"][0]["status"] == "pending"
        assert r.cost_tracker.total_calls == 0
        await stream.aclose()
        assert c.summary()["attempts"][0]["status"] == "closed"
        assert r.cost_tracker.total_calls == 1
        assert not r.cost_tracker.records[0].success
        assert closed
    finally:
        active_usage.reset(token)


@pytest.mark.parametrize("end", ["stop", "length", None])
async def test_stream_terminal_usage(end):
    r = router()

    async def raw():
        yield {"choices": [{"delta": {"content": "SELECT 1"}}]}
        if end:
            yield {"choices": [{"finish_reason": end}]}
        yield {"usage": {"prompt_tokens": 7, "completion_tokens": 2}}

    async def call(*args, **kwargs):
        return r._stream_to_async_gen(raw())

    r._call_provider = call
    c = UsageCollector()
    token = active_usage.set(c)
    try:
        if end == "stop":
            assert [x async for x in r.complete_stream([])] == ["SELECT 1"]
        else:
            with pytest.raises(RuntimeError):
                _ = [x async for x in r.complete_stream([])]
        result = c.summary()
        assert result["attempt_count"] == 1
        assert result["attempts"][0]["status"] == ("completed" if end == "stop" else "failed")
        if end == "stop":
            assert result["known_prompt_tokens"] == 7
    finally:
        active_usage.reset(token)


async def test_budget_rejection_and_native_wrapper_no_double_count():
    from app.harness.constraint import ConstraintEnforcer, ConstraintLimits
    from app.llm.budget import BudgetedRouter, active_budget

    c = UsageCollector()
    t = active_usage.set(c)
    b = active_budget.set(ConstraintEnforcer(ConstraintLimits(max_llm_calls_per_query=1)))
    try:
        r = BudgetedRouter(LiteLLMRouter(RouterConfig(), mock_mode=True))
        await r.complete([])
        with pytest.raises(RuntimeError, match="budget exhausted"):
            await r.complete([])
        assert c.summary()["attempt_count"] == 1
    finally:
        active_budget.reset(b)
        active_usage.reset(t)


async def test_timeout_and_cancellation_release_slot():
    from app.db.runtime import gate

    r = router()
    r.config.max_retries = 0
    r.config.max_concurrency = 1
    r.config.timeout_seconds = 0.02
    entered = asyncio.Event()
    closed = []

    async def source():
        try:
            entered.set()
            await asyncio.Event().wait()
            yield "never"
        finally:
            closed.append(True)

    async def call(*args, **kwargs):
        return source()

    r._call_provider = call
    c = UsageCollector()
    token = active_usage.set(c)
    try:

        async def consume():
            return [x async for x in r.complete_stream([])]

        task = asyncio.create_task(consume())
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert c.records[0].status == "cancelled"
        assert closed
        assert not gate("llm-calls", 1).locked()
        with pytest.raises(TimeoutError):
            await consume()
        assert c.records[1].status in {"timeout", "cancelled"}
        assert not gate("llm-calls", 1).locked()
    finally:
        active_usage.reset(token)


def test_bound_and_snapshot():
    c = UsageCollector(limit=1)
    with observe(c, "p", "m", "adapter"):
        pass
    snapshot = c.summary()
    snapshot["attempts"][0]["status"] = "changed"
    with observe(c, "p", "m", "adapter"):
        pass
    assert c.records[0].status == "completed"
    assert c.summary()["dropped_attempts"] == 1
    assert not c.summary()["complete"]


async def test_fallback_and_failure_usage():
    r = router()
    r.config.max_retries = 0
    r.config.fallback_chain = ["fallback"]
    r.config.providers["second"] = ProviderConfig("second", "", ["fallback"])

    class FailureError(ProviderServerError):
        usage = {"prompt_tokens": 5, "completion_tokens": 0}

    async def call(provider, *args, **kwargs):
        if provider.provider == "p":
            raise FailureError("secret")
        return {"usage": {"prompt_tokens": 2, "completion_tokens": 1}}

    r._call_provider = call
    c = UsageCollector()
    token = active_usage.set(c)
    try:
        await r.complete([])
        assert [x.provider for x in c.records] == ["p", "second"]
        assert c.summary()["known_prompt_tokens"] == 7
        assert c.records[0].status == "failed"
        assert "secret" not in str(c.summary())
    finally:
        active_usage.reset(token)


async def test_queued_cancellation_is_not_an_attempt():
    from app.db.runtime import gate

    r = router()
    r.config.max_concurrency = 1
    c = UsageCollector()
    token = active_usage.set(c)
    slot = gate("llm-calls", 1)
    await slot.acquire()
    try:
        task = asyncio.create_task(r.complete([]))
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert c.records == []
    finally:
        slot.release()
        active_usage.reset(token)


async def test_adapter_stream_is_one_unknown_observation():
    from app.llm.budget import BudgetedRouter
    from tests.test_llm.test_budget_adapter import Adapter

    c = UsageCollector()
    token = active_usage.set(c)
    try:
        assert [x async for x in BudgetedRouter(Adapter()).complete_stream([])]
        assert len(c.records) == 1
        assert c.records[0].source == "adapter_boundary"
        assert c.records[0].prompt_tokens is None
        assert c.records[0].finish_status == "unknown"
    finally:
        active_usage.reset(token)


@pytest.mark.parametrize("object_shape", [False, True])
@pytest.mark.parametrize("reason", ["stop", "length", "secret"])
def test_usage_shapes_and_completion_status(object_shape, reason):
    c = UsageCollector()
    response = {
        "usage": {"prompt_tokens": 0, "completion_tokens": 2},
        "choices": [{"finish_reason": reason}],
    }
    if object_shape:
        response = SimpleNamespace(
            usage=SimpleNamespace(prompt_tokens=0, completion_tokens=2),
            choices=[SimpleNamespace(finish_reason=reason)],
        )
    with observe(c, "p", "m", "adapter"):
        c.observe(response)
    assert c.records[0].status == ("completed" if reason == "stop" else "incomplete")
    assert c.summary()["complete"]
    assert c.summary()["known_completion_tokens"] == 2
    assert "secret" not in str(c.summary())
