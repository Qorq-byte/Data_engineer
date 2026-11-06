"""The stream owner keeps its lease across transport, buffering and cancellation."""

import asyncio
from contextlib import asynccontextmanager

import pytest

from app.harness import shared_model_stream


@pytest.mark.parametrize("consume", ["all", "early_close", "cancel", "unstarted_close"])
async def test_owned_stream_closes_provider_and_lease_across_tasks(monkeypatch, consume):
    events = []
    ready = asyncio.Event()
    consumer_waiting = asyncio.Event()

    @asynccontextmanager
    async def lease():
        owner = asyncio.current_task()
        events.append("lease-open")
        try:
            yield
        finally:
            assert asyncio.current_task() is owner
            events.append("lease-close")

    async def provider():
        try:
            yield "first"
            ready.set()
            if consume == "cancel":
                await asyncio.Event().wait()
            yield "second"
        finally:
            events.append("provider-close")

    async def open_stream():
        return provider()

    monkeypatch.setattr(shared_model_stream, "shared_model_scope", lease)
    stream = await shared_model_stream.leased_stream(open_stream)

    async def use():
        if consume == "unstarted_close":
            await stream.aclose()
        elif consume == "early_close":
            assert await anext(stream) == "first"
            await stream.aclose()
        elif consume == "cancel":
            assert await anext(stream) == "first"
            consumer_waiting.set()
            await anext(stream)
        else:
            assert [chunk async for chunk in stream] == ["first", "second"]

    task = asyncio.create_task(use())
    if consume == "cancel":
        await asyncio.wait_for(ready.wait(), 2)
        await asyncio.wait_for(consumer_waiting.wait(), 2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    else:
        await task
    assert events == ["lease-open", "provider-close", "lease-close"]
    assert not any(t.get_name() == "shared-model-stream" for t in asyncio.all_tasks())
