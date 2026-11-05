"""MySQL session locks for single-host query admission and session exclusion.

This primitive is not a complete multi-worker runtime: connection restoration,
shared sessions, index ownership and configuration invalidation need integration.
"""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass

import aiomysql


class CoordinationUnavailableError(RuntimeError):
    """Coordination is unconfirmed; the caller must not start a query."""


class CoordinationBusyError(RuntimeError):
    """Global capacity or the requested session is already occupied."""


@dataclass(frozen=True)
class QueryLease:
    slot: int
    connection_id: int


class MySQLCoordinator:
    """Hold a dedicated acquired connection until all named locks are released.

    MySQL releases session locks when that connection dies. All workers must
    initialize with the same namespace/capacity; configuration changes require
    an offline migration of the configuration row, not a per-worker override.
    """

    def __init__(
        self, pool: aiomysql.Pool, namespace: str, capacity: int, timeout_seconds: float = 2.0
    ) -> None:
        if not isinstance(namespace, str) or not namespace.strip():
            raise ValueError("Coordination namespace required")
        if type(capacity) is not int or not 1 <= capacity <= 64:
            raise ValueError("Coordination capacity must be 1..64")
        if isinstance(timeout_seconds, bool) or not 0 < timeout_seconds <= 30:
            raise ValueError("Invalid coordination timeout")
        self.pool = pool
        self.namespace = hashlib.sha256(namespace.encode()).hexdigest()[:24]
        self.capacity = capacity
        self.timeout = timeout_seconds
        self._ready = False

    async def initialize(self) -> None:
        self._ready = False
        try:
            async with (
                asyncio.timeout(self.timeout),
                self.pool.acquire() as conn,
                conn.cursor() as cur,
            ):
                await cur.execute(
                    "CREATE TABLE IF NOT EXISTS worker_coordination_config ("
                    "namespace VARCHAR(24) PRIMARY KEY, capacity INT NOT NULL)"
                )
                await cur.execute(
                    "INSERT IGNORE INTO worker_coordination_config VALUES (%s,%s)",
                    (self.namespace, self.capacity),
                )
                await conn.commit()
                await cur.execute(
                    "SELECT capacity FROM worker_coordination_config WHERE namespace=%s",
                    (self.namespace,),
                )
                row = await cur.fetchone()
                if row is None or row[0] != self.capacity:
                    raise CoordinationUnavailableError("Worker capacity configuration mismatch")
        except CoordinationUnavailableError:
            raise
        except Exception:
            raise CoordinationUnavailableError("Unable to initialize coordination") from None
        self._ready = True

    async def _scalar(
        self, conn: aiomysql.Connection, sql: str, name: str | None = None
    ) -> int | None:
        async with asyncio.timeout(self.timeout), conn.cursor() as cur:
            await cur.execute(sql, (name,) if name is not None else None)
            row = await cur.fetchone()
            return int(row[0]) if row is not None and row[0] is not None else None

    async def _release(self, conn: aiomysql.Connection, locks: list[str]) -> None:
        if conn.closed:
            raise CoordinationUnavailableError("Coordination connection closed")
        try:
            for name in reversed(locks):
                if await self._scalar(conn, "SELECT RELEASE_LOCK(%s)", name) != 1:
                    raise CoordinationUnavailableError("Lock ownership was lost")
        except BaseException:
            conn.close()  # Never recycle a connection with uncertain lock ownership.
            raise

    async def _watch_ownership(
        self,
        conn: aiomysql.Connection,
        locks: list[str],
        identity: int,
        stopped: asyncio.Event,
        deadline: asyncio.Timeout,
    ) -> None:
        while not stopped.is_set():
            try:
                await asyncio.wait_for(stopped.wait(), 0.25)
                return
            except TimeoutError:
                pass
            try:
                for name in locks:
                    if await self._scalar(conn, "SELECT IS_USED_LOCK(%s)", name) != identity:
                        raise CoordinationUnavailableError("Lock ownership was lost")
            except Exception:
                # Timeout owns its cancellation, so ordinary caller cancellation
                # remains CancelledError rather than being mistaken for lease loss.
                deadline.reschedule(asyncio.get_running_loop().time())
                return

    @asynccontextmanager
    async def _watched_lease(
        self,
        conn: aiomysql.Connection,
        locks: list[str],
        identity: int,
    ) -> AsyncIterator[None]:
        stopped = asyncio.Event()
        deadline = asyncio.timeout(None)
        try:
            async with deadline:
                monitor = asyncio.create_task(
                    self._watch_ownership(conn, locks, identity, stopped, deadline),
                    name="mysql-query-lease-watchdog",
                )
                try:
                    yield
                finally:
                    # Do not cancel an in-flight MySQL read: aiomysql may close
                    # that connection. Join the bounded read before RELEASE_LOCK.
                    stopped.set()
                    cancelled = False
                    while not monitor.done():
                        try:
                            await asyncio.shield(monitor)
                        except asyncio.CancelledError:
                            cancelled = True
                    monitor.result()
                    if cancelled:
                        raise asyncio.CancelledError
        except TimeoutError:
            if deadline.expired():
                raise CoordinationUnavailableError("Query coordination ownership lost") from None
            raise

    async def _joined_release(self, conn: aiomysql.Connection, locks: list[str]) -> None:
        task = asyncio.create_task(self._release(conn, locks))
        cancelled = False
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                cancelled = True
            except Exception:
                break
        try:
            task.result()
        except Exception:
            raise CoordinationUnavailableError("Unable to release coordination locks") from None
        if cancelled:
            raise asyncio.CancelledError

    @asynccontextmanager
    async def query(self, session_id: str) -> AsyncIterator[QueryLease]:
        if not self._ready:
            raise CoordinationUnavailableError("Coordination has not been initialized")
        if not isinstance(session_id, str) or not session_id:
            raise ValueError("Session identity required")
        try:
            async with asyncio.timeout(self.timeout):
                conn = await self.pool.acquire()
        except Exception:
            raise CoordinationUnavailableError("Coordination connection unavailable") from None
        locks: list[str] = []
        failed = False
        entered = False
        try:
            slot = -1
            for index in range(self.capacity):
                name = f"de:{self.namespace}:q:{index}"
                result = await self._scalar(conn, "SELECT GET_LOCK(%s,0)", name)
                if result is None:
                    raise CoordinationUnavailableError("Admission result unconfirmed")
                if result == 1:
                    locks.append(name)
                    slot = index
                    break
            if slot < 0:
                raise CoordinationBusyError("Global query capacity reached")
            session = hashlib.sha256(session_id.encode()).hexdigest()[:24]
            name = f"de:{self.namespace}:s:{session}"
            result = await self._scalar(conn, "SELECT GET_LOCK(%s,0)", name)
            if result is None:
                raise CoordinationUnavailableError("Session lock result unconfirmed")
            if result != 1:
                raise CoordinationBusyError("Session is busy in another worker")
            locks.append(name)
            identity = await self._scalar(conn, "SELECT CONNECTION_ID()")
            if identity is None:
                raise CoordinationUnavailableError("Connection identity unconfirmed")
            entered = True
            async with self._watched_lease(conn, locks, identity):
                yield QueryLease(slot, identity)
        except BaseException as exc:
            failed = True
            # GET_LOCK may have reached MySQL even if its result was cancelled
            # or lost. Closing the session also releases unconfirmed locks.
            conn.close()
            if (
                not entered
                and isinstance(exc, Exception)
                and not isinstance(
                    exc,
                    CoordinationBusyError | CoordinationUnavailableError,
                )
            ):
                raise CoordinationUnavailableError("Coordination result unconfirmed") from None
            raise
        finally:
            try:
                await self._joined_release(conn, locks)
            except Exception:
                if not failed:
                    raise
            finally:
                await self.pool.release(conn)
