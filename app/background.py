"""Track process-local background index tasks so shutdown can drain cancellation."""

import asyncio
import logging
from collections.abc import Coroutine
from typing import Any


def start_background(coroutine: Coroutine[Any, Any, Any]) -> asyncio.Task[Any]:
    loop: Any = asyncio.get_running_loop()
    if not hasattr(loop, "_data_engineer_background"):
        loop._data_engineer_background = set()
    tasks = loop._data_engineer_background
    task = asyncio.create_task(coroutine)
    tasks.add(task)

    def completed(done: asyncio.Task[Any]) -> None:
        tasks.discard(done)
        if not done.cancelled() and (error := done.exception()) is not None:
            logging.getLogger(__name__).error(
                "Background task failed", exc_info=(type(error), error, error.__traceback__)
            )

    task.add_done_callback(completed)
    return task


async def stop_background() -> None:
    tasks = list(getattr(asyncio.get_running_loop(), "_data_engineer_background", set()))
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
