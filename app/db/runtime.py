"""Single-event-loop resource gates and cancellation-safe native database work."""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator, Callable, Hashable
from typing import Any

type GateTable = dict[tuple[Hashable, int], asyncio.Semaphore]
_GATES_ATTRIBUTE = "_data_engineer_gates"


def gate(key: Hashable, capacity: int = 1) -> asyncio.Semaphore:
    """Gates belong to the serving loop; no locks survive a closed test/server loop."""
    loop = asyncio.get_running_loop()
    gates: GateTable | None = getattr(loop, _GATES_ATTRIBUTE, None)
    if gates is None:
        gates = {}
        setattr(loop, _GATES_ATTRIBUTE, gates)
    return gates.setdefault((key, capacity), asyncio.Semaphore(capacity))


@contextlib.asynccontextmanager
async def lease(connection: Any) -> AsyncIterator[None]:
    async with gate(("database", id(connection))):
        yield


async def native_call[T](connection: Any, function: Callable[[], T]) -> T:
    """Keep the connection lease until a cancelled worker has stopped touching it."""
    from app.db.connections import ConnectionFactory

    offload = type(connection).__name__ == "DuckDBPyConnection" or (
        ConnectionFactory._offload_connections.get(id(connection)) is connection
    )
    if not offload:
        # Standalone caller-owned SQLite may enforce check_same_thread=True.
        return function()
    task = asyncio.create_task(asyncio.to_thread(function))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        with contextlib.suppress(Exception):
            connection.interrupt()
        # Repeated cancellation must not release a handle still used by the worker.
        while not task.done():
            with contextlib.suppress(Exception, asyncio.CancelledError):
                await asyncio.shield(task)
        with contextlib.suppress(Exception, asyncio.CancelledError):
            task.result()
        raise


def discard_gate(key: Hashable, capacity: int = 1) -> None:
    try:
        gates: GateTable = getattr(asyncio.get_running_loop(), _GATES_ATTRIBUTE, {})
    except RuntimeError:
        return
    existing = gates.get((key, capacity))
    if existing is not None and not existing.locked() and not getattr(existing, "_waiters", None):
        gates.pop((key, capacity), None)
