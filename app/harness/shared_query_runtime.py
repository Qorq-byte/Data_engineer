"""Optional shared query dependencies; activation is owned by deployment startup."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from app.harness.mysql_coordination import (
    CoordinationBusyError,
    CoordinationUnavailableError,
    MySQLCoordinator,
)
from app.storage.shared_sessions import MySQLSessionStore
from app.workflow.contracts import QueryCapacityError


class SharedQueryRuntime:
    def __init__(self, coordinator: MySQLCoordinator, sessions: MySQLSessionStore) -> None:
        if coordinator.namespace != sessions.namespace:
            raise ValueError("Shared runtime namespaces must match")
        self.coordinator = coordinator
        self.sessions = sessions
        self.ready = False

    async def initialize(self) -> None:
        self.ready = False
        await self.coordinator.initialize()
        await self.sessions.initialize()
        self.ready = True


_runtime: SharedQueryRuntime | None = None


def current_shared_runtime() -> SharedQueryRuntime | None:
    return _runtime


def publish_shared_runtime(runtime: SharedQueryRuntime | None) -> None:
    global _runtime
    _runtime = runtime


@asynccontextmanager
async def shared_query_scope(session_id: str) -> AsyncIterator[None]:
    runtime = current_shared_runtime()
    if runtime is None:
        yield
        return
    if not runtime.ready:
        raise CoordinationUnavailableError("Shared query runtime is not ready")
    try:
        async with runtime.coordinator.query(session_id):
            yield
    except CoordinationBusyError:
        raise QueryCapacityError("Shared query or session capacity reached") from None
