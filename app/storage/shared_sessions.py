"""MySQL session snapshots with opaque compare-and-swap revisions and bounded retention."""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import uuid
from dataclasses import dataclass
from typing import Any

import aiomysql


class SessionStoreUnavailableError(RuntimeError):
    """Storage outcome is unconfirmed; read before retrying a write."""


class SessionConflictError(RuntimeError):
    """The version read by the caller is no longer current."""


class SessionCapacityError(RuntimeError):
    """No new session can be created within the shared retention bound."""


@dataclass(frozen=True)
class SessionRecord:
    revision: str
    payload: dict[str, Any]


def _reject_constant(value: str) -> None:
    raise ValueError("Invalid session JSON")


class MySQLSessionStore:
    """Authoritative snapshots; never serve a cached process-local fallback.

    The namespace must match MySQLCoordinator. A held coordinator session lock
    protects an expired active session from pruning. Revisions are random tokens
    so deleting/recreating a session cannot accidentally validate a stale writer.
    """

    def __init__(
        self, pool: aiomysql.Pool, namespace: str, max_entries: int = 1000, ttl_seconds: int = 1800
    ) -> None:
        if not isinstance(namespace, str) or not namespace.strip():
            raise ValueError("Session namespace required")
        if type(max_entries) is not int or not 1 <= max_entries <= 10000:
            raise ValueError("Invalid shared session capacity")
        if type(ttl_seconds) is not int or not 1 <= ttl_seconds <= 86400:
            raise ValueError("Invalid session retention")
        self.pool = pool
        self.namespace = hashlib.sha256(namespace.encode()).hexdigest()[:24]
        self.max_entries = max_entries
        self.ttl_seconds = ttl_seconds
        self._ready = False

    async def initialize(self) -> None:
        self._ready = False
        try:
            async with asyncio.timeout(5), self.pool.acquire() as conn, conn.cursor() as cur:
                await cur.execute(
                    "CREATE TABLE IF NOT EXISTS worker_session_config ("
                    "namespace VARCHAR(24) PRIMARY KEY, max_entries INT NOT NULL, "
                    "ttl_seconds INT NOT NULL)"
                )
                await cur.execute(
                    "CREATE TABLE IF NOT EXISTS worker_sessions ("
                    "namespace VARCHAR(24) NOT NULL, session_key CHAR(64) NOT NULL, "
                    "revision CHAR(32) NOT NULL, payload MEDIUMTEXT NOT NULL, "
                    "expires_at DATETIME(6) NOT NULL, PRIMARY KEY(namespace,session_key))"
                )
                await cur.execute(
                    "INSERT IGNORE INTO worker_session_config VALUES (%s,%s,%s)",
                    (self.namespace, self.max_entries, self.ttl_seconds),
                )
                await conn.commit()
                await cur.execute(
                    "SELECT max_entries,ttl_seconds FROM worker_session_config WHERE namespace=%s",
                    (self.namespace,),
                )
                if await cur.fetchone() != (self.max_entries, self.ttl_seconds):
                    raise SessionStoreUnavailableError("Shared session configuration mismatch")
        except SessionStoreUnavailableError:
            raise
        except Exception:
            raise SessionStoreUnavailableError("Unable to initialize shared sessions") from None
        self._ready = True

    def _key(self, session_id: str) -> str:
        if not self._ready:
            raise SessionStoreUnavailableError("Shared sessions are not initialized")
        if not isinstance(session_id, str) or not session_id or len(session_id) > 256:
            raise ValueError("Invalid session identity")
        return hashlib.sha256(session_id.encode()).hexdigest()

    def _encode(self, session_id: str, payload: dict[str, Any]) -> str:
        if not isinstance(payload, dict) or payload.get("session_id") != session_id:
            raise ValueError("Invalid session snapshot")
        if not isinstance(payload.get("turns", []), list) or not isinstance(
            payload.get("versions", []),
            list,
        ):
            raise ValueError("Invalid session history")
        try:
            value = json.dumps(payload, ensure_ascii=False, allow_nan=False)
            if len(value.encode("utf-8")) > 1024 * 1024:
                raise ValueError("Session snapshot exceeds 1 MiB")
            return value
        except (TypeError, ValueError):
            raise ValueError("Invalid or oversized session snapshot") from None

    async def get(self, session_id: str) -> SessionRecord | None:
        key = self._key(session_id)
        try:
            async with asyncio.timeout(5), self.pool.acquire() as conn, conn.cursor() as cur:
                await cur.execute(
                    "SELECT revision,payload FROM worker_sessions "
                    "WHERE namespace=%s AND session_key=%s AND expires_at>UTC_TIMESTAMP(6)",
                    (self.namespace, key),
                )
                row = await cur.fetchone()
            if row is None:
                return None
            payload = json.loads(row[1], parse_constant=_reject_constant)
            self._encode(session_id, payload)
            if not re.fullmatch(r"[0-9a-f]{32}", row[0]):
                raise ValueError("Invalid stored revision")
            return SessionRecord(row[0], payload)
        except Exception:
            raise SessionStoreUnavailableError("Unable to read shared session") from None

    async def put(
        self, session_id: str, payload: dict[str, Any], expected_revision: str | None
    ) -> str:
        key = self._key(session_id)
        value = self._encode(session_id, payload)
        if expected_revision is not None and (
            not isinstance(expected_revision, str)
            or not re.fullmatch(r"[0-9a-f]{32}", expected_revision)
        ):
            raise ValueError("Invalid expected session revision")
        revision = uuid.uuid4().hex
        try:
            async with asyncio.timeout(5), self.pool.acquire() as conn:
                try:
                    async with conn.cursor() as cur:
                        await cur.execute("START TRANSACTION")
                        await cur.execute(
                            "SELECT max_entries,ttl_seconds FROM worker_session_config "
                            "WHERE namespace=%s FOR UPDATE",
                            (self.namespace,),
                        )
                        if await cur.fetchone() != (self.max_entries, self.ttl_seconds):
                            raise SessionStoreUnavailableError(
                                "Shared session configuration changed"
                            )
                        await cur.execute(
                            "DELETE FROM worker_sessions WHERE namespace=%s "
                            "AND expires_at<=UTC_TIMESTAMP(6) AND IS_FREE_LOCK("
                            "CONCAT('de:',namespace,':s:',LEFT(session_key,24)))=1",
                            (self.namespace,),
                        )
                        await cur.execute(
                            "SELECT revision,expires_at<=UTC_TIMESTAMP(6) FROM worker_sessions "
                            "WHERE namespace=%s AND session_key=%s FOR UPDATE",
                            (self.namespace, key),
                        )
                        row = await cur.fetchone()
                        if (expected_revision is None and row is not None and not row[1]) or (
                            expected_revision is not None
                            and (row is None or row[0] != expected_revision)
                        ):
                            raise SessionConflictError(
                                "Shared session changed; read before retrying"
                            )
                        if row is None:
                            await cur.execute(
                                "SELECT COUNT(*) FROM worker_sessions WHERE namespace=%s",
                                (self.namespace,),
                            )
                            count = await cur.fetchone()
                            if count is None or count[0] >= self.max_entries:
                                raise SessionCapacityError("Shared session capacity reached")
                            await cur.execute(
                                "INSERT INTO worker_sessions VALUES (%s,%s,%s,%s,"
                                "DATE_ADD(UTC_TIMESTAMP(6),INTERVAL %s SECOND))",
                                (self.namespace, key, revision, value, self.ttl_seconds),
                            )
                        else:
                            await cur.execute(
                                "UPDATE worker_sessions SET revision=%s,payload=%s,expires_at="
                                "DATE_ADD(UTC_TIMESTAMP(6),INTERVAL %s SECOND) "
                                "WHERE namespace=%s AND session_key=%s",
                                (revision, value, self.ttl_seconds, self.namespace, key),
                            )
                    await conn.commit()
                except BaseException:
                    # Never recycle an unconfirmed transaction.
                    conn.close()
                    raise
        except (SessionConflictError, SessionCapacityError, SessionStoreUnavailableError):
            raise
        except Exception:
            raise SessionStoreUnavailableError(
                "Session write unconfirmed; read before retrying"
            ) from None
        return revision
