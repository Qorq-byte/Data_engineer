"""MySQL connection pool and users table for the auth subsystem.

Uses aiomysql (same driver as ConnectionFactory) but maintains its own
dedicated pool — auth is infrastructure state, not a user-target database.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

logger = logging.getLogger(__name__)

_pool: aiomysql.Pool | None = None  # type: ignore[name-defined]
_pool_lock = asyncio.Lock()

_USERS_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS users (
    id          INT AUTO_INCREMENT PRIMARY KEY,
    username    VARCHAR(50)  NOT NULL UNIQUE,
    email       VARCHAR(120) NOT NULL UNIQUE,
    password_hash VARCHAR(255) NOT NULL,
    avatar_url  VARCHAR(500) DEFAULT '' COMMENT '用户头像 URL 或文件路径',
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_email (email),
    INDEX idx_username (username)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
"""

_AVATAR_COLUMN_DDL = """
ALTER TABLE users ADD COLUMN IF NOT EXISTS
    avatar_url VARCHAR(500) DEFAULT '' COMMENT '用户头像 URL 或文件路径'
    AFTER password_hash;
"""

# Fallback for MySQL < 8.0 where IF NOT EXISTS is not supported
_AVATAR_COLUMN_CHECK = """
SELECT COUNT(*) FROM information_schema.COLUMNS
WHERE TABLE_SCHEMA = DATABASE()
  AND TABLE_NAME = 'users'
  AND COLUMN_NAME = 'avatar_url';
"""

_AVATAR_COLUMN_ADD = """
ALTER TABLE users ADD COLUMN
    avatar_url VARCHAR(500) DEFAULT '' COMMENT '用户头像 URL 或文件路径'
    AFTER password_hash;
"""


async def get_auth_pool() -> aiomysql.Pool:  # type: ignore[name-defined]
    """Return (or create) the module-level aiomysql connection pool."""
    global _pool

    if _pool is not None:
        return _pool

    async with _pool_lock:
        if _pool is not None:
            return _pool

        import aiomysql

        from app.config.settings import settings

        logger.info(
            "Creating auth MySQL pool: %s:%d/%s",
            settings.auth_mysql_host,
            settings.auth_mysql_port,
            settings.auth_mysql_database,
        )
        _pool = await aiomysql.create_pool(
            host=settings.auth_mysql_host,
            port=settings.auth_mysql_port,
            user=settings.auth_mysql_user,
            password=settings.auth_mysql_password,
            db=settings.auth_mysql_database,
            autocommit=True,
            minsize=1,
            maxsize=10,
            charset="utf8mb4",
        )
        return _pool


async def init_auth_db() -> None:
    """Create the users table if it doesn't exist. Called once at startup."""
    try:
        pool = await get_auth_pool()
        async with pool.acquire() as conn, conn.cursor() as cur:
            await cur.execute(_USERS_TABLE_DDL)
            # Ensure avatar_url column exists (for existing databases)
            try:
                await cur.execute(_AVATAR_COLUMN_DDL)
            except Exception:
                # Fallback: check if column exists, add if not
                try:
                    await cur.execute(_AVATAR_COLUMN_CHECK)
                    row = await cur.fetchone()
                    if row and row[0] == 0:
                        await cur.execute(_AVATAR_COLUMN_ADD)
                        logger.info("Added avatar_url column to users table")
                except Exception:
                    pass  # Column may already exist or not needed
        logger.info("Auth database ready — users table exists")
    except Exception:
        logger.exception("Failed to initialize auth database — auth endpoints will be unavailable")
        raise


async def close_auth_pool() -> None:
    """Close the auth connection pool. Called at shutdown."""
    global _pool
    if _pool is not None:
        _pool.close()
        await _pool.wait_closed()
        _pool = None
        logger.info("Auth MySQL pool closed")


@asynccontextmanager
async def get_connection() -> AsyncGenerator[aiomysql.Connection, None]:  # type: ignore[name-defined]
    """Async context manager that acquires/releases a connection from the auth pool."""
    pool = await get_auth_pool()
    conn = await pool.acquire()
    try:
        yield conn
    finally:
        pool.release(conn)
