"""Exercise actual adapter boundaries without contacting an LLM provider."""

import asyncio

import pytest

from app.harness.constraint import ConstraintEnforcer, ConstraintLimits
from app.llm.budget import BudgetedRouter, active_budget
from app.llm.router import LiteLLMRouter, RouterConfig


class Adapter:
    def __init__(self, response=None, chunks=("SELECT ", "1"), error=None):
        self.config = RouterConfig(default_model="fixture")
        self.response = {"choices": []} if response is None else response
        self.chunks = chunks
        self.error = error
        self.calls = []

    async def complete(self, messages, **kwargs):
        self.calls.append((messages, kwargs))
        if self.error:
            raise self.error
        return self.response

    async def complete_stream(self, messages, **kwargs):
        self.calls.append((messages, kwargs))
        for chunk in self.chunks:
            yield chunk
        if self.error:
            raise self.error


@pytest.fixture
def budget():
    limits = ConstraintEnforcer(ConstraintLimits(max_llm_calls_per_query=1))
    token = active_budget.set(limits)
    try:
        yield limits
    finally:
        active_budget.reset(token)


async def test_adapter_forwards_completion_options_and_counts_once(budget):
    adapter = Adapter()
    router = BudgetedRouter(adapter)
    messages = [{"role": "user", "content": "fixture"}]
    result = await router.complete(messages, model="custom", temperature=0.2, n=3, seed=7)
    assert router.config is adapter.config
    assert result is adapter.response
    assert adapter.calls == [(messages, {"model": "custom", "temperature": 0.2, "n": 3,
                                         "seed": 7})]
    assert budget.llm_calls_used == 1
    with pytest.raises(RuntimeError, match="budget exhausted"):
        await router.complete(messages)
    assert len(adapter.calls) == 1


async def test_adapter_stream_is_lazy_and_reserves_once(budget):
    adapter = Adapter()
    router = BudgetedRouter(adapter)
    stream = router.complete_stream([], model="custom", temperature=0.1, seed=9)
    assert budget.llm_calls_used == 0
    assert [chunk async for chunk in stream] == ["SELECT ", "1"]
    assert budget.llm_calls_used == 1
    assert adapter.calls == [([], {"model": "custom", "temperature": 0.1, "seed": 9})]
    with pytest.raises(RuntimeError, match="budget exhausted"):
        await anext(router.complete_stream([]))
    assert len(adapter.calls) == 1


@pytest.mark.parametrize("response", [None, [], "unstructured", 123])
async def test_adapter_rejects_invalid_response(budget, response):
    adapter = Adapter()
    adapter.response = response
    with pytest.raises(TypeError, match="must be an object"):
        await BudgetedRouter(adapter).complete([])
    assert budget.llm_calls_used == 1


@pytest.mark.parametrize("chunk", [None, {}, 123])
async def test_adapter_rejects_invalid_stream_chunk(budget, chunk):
    with pytest.raises(TypeError, match="chunks must be strings"):
        await anext(BudgetedRouter(Adapter(chunks=[chunk])).complete_stream([]))
    assert budget.llm_calls_used == 1


@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("error", [RuntimeError("provider failed"), asyncio.CancelledError()])
async def test_adapter_propagates_errors_and_cancellation(budget, streaming, error):
    router = BudgetedRouter(Adapter(error=error, chunks=[]))
    with pytest.raises(type(error)) as raised:
        if streaming:
            await anext(router.complete_stream([]))
        else:
            await router.complete([])
    assert raised.value is error
    assert budget.llm_calls_used == 1


@pytest.mark.parametrize("streaming", [False, True])
async def test_native_router_owns_physical_attempt_budget(budget, streaming):
    router = BudgetedRouter(LiteLLMRouter(RouterConfig(), mock_mode=True))
    if streaming:
        messages = [{"role": "user", "content": "SELECT 1"}]
        assert "".join([chunk async for chunk in router.complete_stream(messages)]) == "SELECT 1 "
    else:
        assert "choices" in await router.complete([])
    assert budget.llm_calls_used == 1


async def test_parallel_adapters_keep_request_budgets_isolated():
    async def request():
        limits = ConstraintEnforcer(ConstraintLimits(max_llm_calls_per_query=1))
        token = active_budget.set(limits)
        try:
            await asyncio.sleep(0)
            await BudgetedRouter(Adapter()).complete([])
            return limits.llm_calls_used
        finally:
            active_budget.reset(token)

    assert await asyncio.gather(request(), request()) == [1, 1]
