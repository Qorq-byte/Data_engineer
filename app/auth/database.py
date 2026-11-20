"""MySQL connection pool and users table for the auth subsystem.

Uses aiomysql (same driver as ConnectionFactory) but maintains its own
dedicated pool — auth is infrastructure state, not a user-target database.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

from app.db.mysql_schema import mysql_schema_cursor

if TYPE_CHECKING:
    import aiomysql

logger = logging.getLogger(__name__)

_pool: aiomysql.Pool | None = None
_ready_pool: aiomysql.Pool | None = None
_pool_lock = asyncio.Lock()

_USERS_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS users (
    id          INT AUTO_INCREMENT PRIMARY KEY,
    tenant_id   INT          NOT NULL DEFAULT 1,
    token_version BIGINT UNSIGNED NOT NULL DEFAULT 0,
    username    VARCHAR(50)  NOT NULL UNIQUE,
    email       VARCHAR(120) NOT NULL UNIQUE,
    password_hash VARCHAR(255) NOT NULL,
    avatar_url  VARCHAR(500) DEFAULT '' COMMENT '用户头像 URL 或文件路径',
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_email (email),
    INDEX idx_username (username),
    INDEX idx_tenant (tenant_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
"""

_COLUMN_METADATA = """
SELECT DATA_TYPE, IS_NULLABLE, COLUMN_DEFAULT, COLUMN_TYPE FROM information_schema.COLUMNS
WHERE TABLE_SCHEMA = DATABASE()
  AND TABLE_NAME = 'users'
  AND COLUMN_NAME = %s;
"""

_TENANT_COLUMN_ADD = """
ALTER TABLE users ADD COLUMN tenant_id INT NOT NULL DEFAULT 1 AFTER id;
"""

_TENANT_INDEX_CHECK = """
SELECT COLUMN_NAME, NON_UNIQUE FROM information_schema.STATISTICS
WHERE TABLE_SCHEMA = DATABASE()
  AND TABLE_NAME = 'users'
  AND INDEX_NAME = 'idx_tenant'
ORDER BY SEQ_IN_INDEX;
"""

_TENANT_INDEX_ADD = "ALTER TABLE users ADD INDEX idx_tenant (tenant_id);"

_TOKEN_VERSION_ADD = "ALTER TABLE users ADD COLUMN token_version BIGINT UNSIGNED NOT NULL DEFAULT 0"
_TOKEN_TRIGGER_NAME = "users_token_version_on_update"
_TOKEN_TRIGGER_BODY = """
SET NEW.token_version = GREATEST(
    NEW.token_version,
    OLD.token_version + (NEW.tenant_id <> OLD.tenant_id)
)
""".strip()
_TOKEN_TRIGGER_QUERY = """
SELECT EVENT_OBJECT_TABLE, ACTION_TIMING, EVENT_MANIPULATION, ACTION_STATEMENT
FROM information_schema.TRIGGERS WHERE TRIGGER_SCHEMA = DATABASE() AND TRIGGER_NAME = %s
"""

_AVATAR_COLUMN_ADD = """
ALTER TABLE users ADD COLUMN
    avatar_url VARCHAR(500) DEFAULT '' COMMENT '用户头像 URL 或文件路径'
    AFTER password_hash;
"""


async def get_auth_pool() -> aiomysql.Pool:
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
    """Initialize and validate the users table before opening account endpoints."""
    global _ready_pool
    try:
        pool = await get_auth_pool()
        async with mysql_schema_cursor(pool) as cur:
            await cur.execute(_USERS_TABLE_DDL)
            await cur.execute(_COLUMN_METADATA, ("tenant_id",))
            tenant = await cur.fetchone()
            if tenant is None:
                await cur.execute(_TENANT_COLUMN_ADD)
            elif tenant[:2] != ("int", "NO") or str(tenant[2]) != "1":
                raise RuntimeError("Incompatible users.tenant_id column")

            await cur.execute(_TENANT_INDEX_CHECK)
            index = await cur.fetchall()
            if not index:
                await cur.execute(_TENANT_INDEX_ADD)
            elif tuple(index) != (("tenant_id", 1),):
                raise RuntimeError("Incompatible users.idx_tenant index")

            await cur.execute(_COLUMN_METADATA, ("avatar_url",))
            if await cur.fetchone() is None:
                await cur.execute(_AVATAR_COLUMN_ADD)
            await cur.execute(_COLUMN_METADATA, ("token_version",))
            version = await cur.fetchone()
            if version is None:
                await cur.execute(_TOKEN_VERSION_ADD)
            elif version != ("bigint", "NO", "0", "bigint unsigned"):
                raise RuntimeError("Incompatible users.token_version column")

            await cur.execute(_TOKEN_TRIGGER_QUERY, (_TOKEN_TRIGGER_NAME,))
            trigger = await cur.fetchone()
            if trigger is None:
                await cur.execute(
                    f"CREATE TRIGGER {_TOKEN_TRIGGER_NAME} BEFORE UPDATE ON users "
                    f"FOR EACH ROW {_TOKEN_TRIGGER_BODY}"
                )
            elif trigger[:3] != ("users", "BEFORE", "UPDATE") or (
                trigger[3].split() != _TOKEN_TRIGGER_BODY.split()
            ):
                raise RuntimeError("Incompatible users token version trigger")
        _ready_pool = pool
        logger.info("Auth database ready — users table exists")
    except BaseException:
        _ready_pool = None
        logger.exception("Failed to initialize auth database — auth endpoints will be unavailable")
        raise


async def close_auth_pool() -> None:
    """Close the auth connection pool. Called at shutdown."""
    global _pool, _ready_pool
    _ready_pool = None
    if _pool is not None:
        _pool.close()
        await _pool.wait_closed()
        _pool = None
        logger.info("Auth MySQL pool closed")


def auth_database_ready() -> bool:
    """Return whether schema initialization completed for the current pool."""
    return _ready_pool is not None


@asynccontextmanager
async def get_connection() -> AsyncGenerator[aiomysql.Connection, None]:
    """Async context manager that acquires/releases a connection from the auth pool."""
    pool = _ready_pool
    if pool is None:
        raise RuntimeError("Authentication database schema unavailable")
    async with pool.acquire() as conn:
        yield conn


async def read_token_state(user_id: int) -> tuple[int, int] | None:
    """Read the current tenant and token version by primary key."""
    async with asyncio.timeout(5), get_connection() as conn, conn.cursor() as cur:
        await cur.execute("SELECT tenant_id, token_version FROM users WHERE id = %s", (user_id,))
        row = await cur.fetchone()
        if row is None:
            return None
        tenant_id, version = row
        if type(tenant_id) is not int or tenant_id < 1 or type(version) is not int or version < 0:
            raise RuntimeError("Invalid authentication database token state")
        return tenant_id, version
