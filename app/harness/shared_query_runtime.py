"""Optional shared query dependencies; activation is owned by deployment startup."""

from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from app.auth.tenant import get_current_tenant_id
from app.harness.mysql_coordination import (
    CoordinationBusyError,
    CoordinationUnavailableError,
    MySQLCoordinator,
)
from app.harness.mysql_rate_limit import MySQLProviderRateLimit
from app.harness.worker_release import WorkerRelease
from app.storage.shared_sessions import MySQLSessionStore
from app.workflow.contracts import QueryCapacityError


@dataclass(frozen=True)
class TenantReleaseSnapshot:
    settings: dict[str, Any]
    domains: dict[str, dict[str, Any]]


class SharedQueryRuntime:
    def __init__(self, coordinator: MySQLCoordinator, sessions: MySQLSessionStore) -> None:
        if coordinator.namespace != sessions.namespace:
            raise ValueError("Shared runtime namespaces must match")
        self.coordinator = coordinator
        self.sessions = sessions
        self.rate_limit = MySQLProviderRateLimit(sessions.pool, sessions.namespace)
        self.ready = False
        self.verify_configuration: Callable[[], Awaitable[None]] | None = None
        self.model_coordinator: MySQLCoordinator | None = None
        self.worker_release: WorkerRelease | None = None
        self.tenant_snapshots: dict[int, TenantReleaseSnapshot] = {}

    async def initialize(self) -> None:
        self.ready = False
        await self.coordinator.initialize()
        await self.sessions.initialize()
        await self.rate_limit.initialize()
        self.ready = True


_runtime: SharedQueryRuntime | None = None


def current_shared_runtime() -> SharedQueryRuntime | None:
    return _runtime


def publish_shared_runtime(runtime: SharedQueryRuntime | None) -> None:
    global _runtime
    _runtime = runtime


def current_tenant_release_snapshot() -> TenantReleaseSnapshot | None:
    runtime = current_shared_runtime()
    if runtime is None or not runtime.ready:
        return None
    return runtime.tenant_snapshots.get(get_current_tenant_id())


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
            if runtime.verify_configuration is not None:
                await runtime.verify_configuration()
            yield
    except CoordinationBusyError:
        raise QueryCapacityError("Shared query or session capacity reached") from None


@asynccontextmanager
async def shared_model_scope() -> AsyncIterator[None]:
    runtime = current_shared_runtime()
    if runtime is None or runtime.model_coordinator is None:
        yield
        return
    if not runtime.ready:
        raise CoordinationUnavailableError("Shared model runtime is not ready")
    try:
        async with runtime.model_coordinator.query(uuid4().hex):
            yield
    except CoordinationBusyError:
        raise QueryCapacityError("Shared model concurrency reached") from None
