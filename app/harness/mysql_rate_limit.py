"""Fail-closed, MySQL-clock rolling provider admission across serving processes."""

from __future__ import annotations

import asyncio
import hashlib
import re
import uuid

import aiomysql

from app.harness.mysql_coordination import CoordinationUnavailableError
from app.workflow.contracts import QueryCapacityError


class MySQLProviderRateLimit:
    def __init__(self, pool: aiomysql.Pool, namespace_digest: str) -> None:
        if not isinstance(namespace_digest, str) or not re.fullmatch(
            r"[0-9a-f]{24}", namespace_digest
        ):
            raise ValueError("Coordinator namespace digest required")
        self.pool = pool
        self.namespace = namespace_digest
        self._ready = False

    async def initialize(self) -> None:
        self._ready = False
        try:
            async with asyncio.timeout(5), self.pool.acquire() as conn, conn.cursor() as cur:
                await cur.execute(
                    "CREATE TABLE IF NOT EXISTS worker_rate_config ("
                    "namespace VARCHAR(24) NOT NULL, provider_key CHAR(64) NOT NULL, "
                    "rpm INT NOT NULL, PRIMARY KEY(namespace,provider_key))"
                )
                await cur.execute(
                    "CREATE TABLE IF NOT EXISTS worker_rate_attempts ("
                    "namespace VARCHAR(24) NOT NULL, provider_key CHAR(64) NOT NULL, "
                    "attempt_id CHAR(32) NOT NULL, created_at DATETIME(6) NOT NULL, "
                    "PRIMARY KEY(namespace,provider_key,attempt_id), "
                    "INDEX rate_expiry(namespace,provider_key,created_at))"
                )
                await conn.commit()
        except Exception:
            raise CoordinationUnavailableError("Unable to initialize shared rate limit") from None
        self._ready = True

    async def reserve(self, provider: str, rpm: int) -> None:
        if not self._ready:
            raise CoordinationUnavailableError("Shared rate limit is not initialized")
        if not isinstance(provider, str) or not provider or len(provider) > 128:
            raise ValueError("Invalid rate provider")
        if type(rpm) is not int or not 0 <= rpm <= 10000:
            raise ValueError("Shared provider RPM must be 0..10000")
        key = hashlib.sha256(provider.encode()).hexdigest()
        try:
            async with asyncio.timeout(5), self.pool.acquire() as conn:
                try:
                    async with conn.cursor() as cur:
                        await cur.execute("START TRANSACTION")
                        # Lock an existing row exclusively at once. INSERT IGNORE
                        # takes a shared duplicate-key lock that can deadlock on upgrade.
                        await cur.execute(
                            "INSERT INTO worker_rate_config VALUES (%s,%s,%s) "
                            "ON DUPLICATE KEY UPDATE rpm=worker_rate_config.rpm",
                            (self.namespace, key, rpm),
                        )
                        await cur.execute(
                            "SELECT rpm FROM worker_rate_config WHERE namespace=%s "
                            "AND provider_key=%s FOR UPDATE",
                            (self.namespace, key),
                        )
                        if await cur.fetchone() != (rpm,):
                            raise CoordinationUnavailableError("Shared provider RPM mismatch")
                        if rpm:
                            await cur.execute(
                                "DELETE FROM worker_rate_attempts WHERE namespace=%s "
                                "AND provider_key=%s "
                                "AND created_at<=UTC_TIMESTAMP(6)-INTERVAL 60 SECOND",
                                (self.namespace, key),
                            )
                            await cur.execute(
                                "SELECT COUNT(*) FROM worker_rate_attempts WHERE namespace=%s "
                                "AND provider_key=%s",
                                (self.namespace, key),
                            )
                            row = await cur.fetchone()
                            if row is None:
                                raise CoordinationUnavailableError("Rate count unconfirmed")
                            if row[0] >= rpm:
                                raise QueryCapacityError("Shared provider rate capacity reached")
                            await cur.execute(
                                "INSERT INTO worker_rate_attempts "
                                "VALUES (%s,%s,%s,UTC_TIMESTAMP(6))",
                                (self.namespace, key, uuid.uuid4().hex),
                            )
                    await conn.commit()
                except BaseException:
                    # A cancelled or unconfirmed COMMIT is not a refundable slot.
                    conn.close()
                    raise
        except (QueryCapacityError, CoordinationUnavailableError):
            raise
        except Exception:
            raise CoordinationUnavailableError("Shared provider rate unavailable") from None
