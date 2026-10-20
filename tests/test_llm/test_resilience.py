"""Provider fault injection: bounded attempts, cooldown recovery and concurrency."""

import asyncio

import pytest

from app.llm.router import LiteLLMRouter, ProviderConfig, ProviderTimeoutError, RouterConfig


def make_router(**kwargs):
    return LiteLLMRouter(
        RouterConfig(
            providers={"test": ProviderConfig("test", "", models=["fixture"])},
            default_model="fixture",
            fallback_chain=[],
            retry_backoff_seconds=0,
            **kwargs,
        )
    )


async def test_transient_failure_retries_then_recovers(monkeypatch):
    router = make_router(max_retries=1)
    calls = []

    async def call(*args, **kwargs):
        calls.append(True)
        if len(calls) == 1:
            raise ProviderTimeoutError("temporary")
        return {"choices": []}

    monkeypatch.setattr(router, "_call_provider", call)
    await router.complete([])
    assert len(calls) == 2
    assert router.provider_healthy("test") is True


async def test_cooldown_allows_new_probe(monkeypatch):
    router = make_router(max_retries=0, cooldown_seconds=0)
    attempts = []

    async def call(*args, **kwargs):
        attempts.append(True)
        if len(attempts) == 1:
            raise ProviderTimeoutError("temporary")
        return {"choices": []}

    monkeypatch.setattr(router, "_call_provider", call)
    with pytest.raises(ProviderTimeoutError):
        await router.complete([])
    assert router.provider_healthy("test") is False
    await router.complete([])
    assert router.provider_healthy("test") is True
    assert len(attempts) == 2


async def test_provider_concurrency_is_bounded(monkeypatch):
    router = make_router(max_retries=0, max_concurrency=2)
    active, peak = 0, 0

    async def call(*args, **kwargs):
        nonlocal active, peak
        active += 1
        peak = max(active, peak)
        try:
            await asyncio.sleep(0.005)
            return {"choices": []}
        finally:
            active -= 1

    monkeypatch.setattr(router, "_call_provider", call)
    await asyncio.gather(*(router.complete([]) for _ in range(10)))
    assert peak == 2
    assert active == 0


async def test_outer_provider_timeout_is_enforced(monkeypatch):
    router = make_router(max_retries=0, timeout_seconds=0.01)

    async def call(*args, **kwargs):
        await asyncio.Event().wait()

    monkeypatch.setattr(router, "_call_provider", call)
    with pytest.raises(ProviderTimeoutError, match="deadline"):
        await router.complete([])


async def test_rate_limit_wait_is_cancellable_before_second_attempt(monkeypatch):
    router = make_router(max_retries=0)
    router.config.providers["test"].rate_limit_rpm = 1
    attempts = []

    async def call(*args, **kwargs):
        attempts.append(True)
        return {"choices": []}

    monkeypatch.setattr(router, "_call_provider", call)
    await router.complete([])
    with pytest.raises(TimeoutError):
        await asyncio.wait_for(router.complete([]), 0.01)
    assert len(attempts) == 1


def test_cost_history_is_bounded_but_totals_remain_cumulative():
    from app.llm.router import CostTracker

    tracker = CostTracker()
    for _ in range(1005):
        tracker.record("openai", "gpt-4.1", prompt_tokens=10)
    assert len(tracker.records) == 1000
    assert tracker.total_calls == 1005
    assert tracker.total_tokens["prompt"] == 10050
    assert tracker.total_cost == pytest.approx(0.0201)
