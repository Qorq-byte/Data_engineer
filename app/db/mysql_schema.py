"""Serialize MySQL schema initialization on the connection that executes DDL."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from aiomysql import Cursor, Pool


@asynccontextmanager
async def mysql_schema_cursor(pool: Pool) -> AsyncIterator[Cursor]:
    lock = "CONCAT('de:schema:', LEFT(SHA2(DATABASE(), 256), 48))"
    async with pool.acquire() as conn:
        try:
            async with conn.cursor() as cur:
                await cur.execute(f"SELECT GET_LOCK({lock}, 30)")
                if await cur.fetchone() != (1,):
                    raise RuntimeError("Could not acquire the MySQL schema lock")
                yield cur
                await cur.execute(f"SELECT RELEASE_LOCK({lock})")
                if await cur.fetchone() != (1,):
                    raise RuntimeError("Lost the MySQL schema lock")
        except BaseException:
            # Never return a connection with uncertain lock ownership to the pool.
            conn.close()
            raise
