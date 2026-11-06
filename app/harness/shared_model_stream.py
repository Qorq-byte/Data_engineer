"""Keep a streaming model lease and its watchdog in one owned producer task."""

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

from app.harness.shared_query_runtime import shared_model_scope


async def leased_stream(open_stream: Callable[[], Awaitable[Any]]) -> AsyncIterator[Any]:
    queue: asyncio.Queue[tuple[bool, Any]] = asyncio.Queue(maxsize=1)
    ready: asyncio.Future[None] = asyncio.get_running_loop().create_future()
    done = object()

    async def produce() -> None:
        try:
            async with shared_model_scope():
                stream = await open_stream()
                if not ready.done():
                    ready.set_result(None)
                try:
                    async for chunk in stream:
                        await queue.put((True, chunk))
                finally:
                    await stream.aclose()
            await queue.put((True, done))
        except BaseException as exc:
            if not ready.done():
                ready.set_exception(exc)
            elif not isinstance(exc, asyncio.CancelledError):
                await queue.put((False, exc))

    producer = asyncio.create_task(produce(), name="shared-model-stream")

    async def close() -> None:
        if not producer.done():
            producer.cancel()
        joined = asyncio.gather(producer, return_exceptions=True)
        cancelled = False
        while not joined.done():
            try:
                await asyncio.shield(joined)
            except asyncio.CancelledError:
                cancelled = True
        joined.result()
        if cancelled:
            raise asyncio.CancelledError

    try:
        await ready
    except BaseException:
        await close()
        raise

    class OwnedStream:
        def __aiter__(self) -> "OwnedStream":
            return self

        async def __anext__(self) -> Any:
            try:
                successful, value = await queue.get()
                if not successful:
                    raise value
                if value is done:
                    await close()
                    raise StopAsyncIteration
                return value
            except BaseException:
                await close()
                raise

        async def aclose(self) -> None:
            await close()

    return OwnedStream()
