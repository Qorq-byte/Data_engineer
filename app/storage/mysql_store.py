"""MySQL storage backend — persistent data layer.

Provides a singleton MySQLStore that handles all CRUD operations for the
QueryHarness application. Uses aiomysql for async MySQL access.

Table mapping:
  - user_settings         → settings (LLM, harness, database, rag, mcp)
  - api_keys              → encrypted API keys per provider
  - datasource_connections→ saved database connection profiles
  - domains               → domain definitions
  - glossary_terms        → business term glossary
  - domain_rules          → domain-specific SQL rules
  - query_history         → NL→SQL query log
  - feedback              → user feedback records
  - learning_rule_candidates → rule mining pipeline
  - learning_quality_trends  → accuracy/quality over time
  - learning_query_pairs  → NL-SQL pairs for training
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from typing import Any, cast

import aiomysql

from app.auth.tenant import get_current_tenant_id
from app.config.settings import settings
from app.db.mysql_schema import mysql_schema_cursor

logger = logging.getLogger("storage.mysql")

# ── Default MySQL connection config ────────────────────────────────────

DEFAULT_MYSQL_CONFIG = {
    "host": settings.mysql_host,
    "port": settings.mysql_port,
    "user": settings.mysql_user,
    "password": settings.mysql_password,
    "db": settings.mysql_database,
    "charset": "utf8mb4",
    "autocommit": True,
}

# ── SQL: CREATE TABLES ─────────────────────────────────────────────────

_CREATE_TABLES_SQL = [
    # 1. User settings (key-value config per section)
    """
    CREATE TABLE IF NOT EXISTS user_settings (
        id INT AUTO_INCREMENT PRIMARY KEY,
        tenant_id INT NOT NULL DEFAULT 1,
        section VARCHAR(100) NOT NULL COMMENT '配置分区: llm, harness, database, rag, mcp',
        config JSON NOT NULL COMMENT 'JSON 配置内容',
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        UNIQUE KEY uk_tenant_section (tenant_id, section)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    COMMENT='用户配置 — LLM 提供商、系统设置、API 密钥';
    """,
    # 2. API keys (encrypted)
    """
    CREATE TABLE IF NOT EXISTS api_keys (
        id INT AUTO_INCREMENT PRIMARY KEY,
        tenant_id INT NOT NULL DEFAULT 1,
        provider VARCHAR(50) NOT NULL COMMENT 'LLM 提供商: openai, anthropic, deepseek, etc.',
        api_key VARCHAR(500) NOT NULL COMMENT '加密后的 API 密钥',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        UNIQUE KEY uk_tenant_provider (tenant_id, provider)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    COMMENT='API 密钥存储';
    """,
    # 3. Database connection profiles
    """
    CREATE TABLE IF NOT EXISTS datasource_connections (
        id INT AUTO_INCREMENT PRIMARY KEY,
        tenant_id INT NOT NULL DEFAULT 1,
        db_id VARCHAR(100) NOT NULL COMMENT '连接唯一标识',
        alias VARCHAR(200) NOT NULL COMMENT '连接别名/显示名称',
        db_type VARCHAR(50) NOT NULL COMMENT '数据库类型: mysql, postgresql, sqlite, etc.',
        host VARCHAR(255) DEFAULT '' COMMENT '主机地址',
        port INT DEFAULT 0 COMMENT '端口号',
        database_name VARCHAR(200) DEFAULT '' COMMENT '数据库名',
        username VARCHAR(200) DEFAULT '' COMMENT '用户名',
        password VARCHAR(500) DEFAULT '' COMMENT '密码',
        file_path VARCHAR(500) DEFAULT '' COMMENT '文件路径 (SQLite, DuckDB)',
        extra_params JSON COMMENT '额外连接参数',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        UNIQUE KEY uk_tenant_db_id (tenant_id, db_id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    COMMENT='数据源连接配置';
    """,
    # 4. Domains
    """
    CREATE TABLE IF NOT EXISTS domains (
        id INT AUTO_INCREMENT PRIMARY KEY,
        tenant_id INT NOT NULL DEFAULT 1,
        name VARCHAR(100) NOT NULL COMMENT '领域标识 (如 ecommerce, finance)',
        label_zh VARCHAR(200) DEFAULT '' COMMENT '中文名称',
        label_en VARCHAR(200) DEFAULT '' COMMENT '英文名称',
        description_zh TEXT COMMENT '中文描述',
        description_en TEXT COMMENT '英文描述',
        keywords JSON COMMENT '关键词列表',
        db_configs JSON COMMENT '领域数据库提示',
        timezone VARCHAR(50) DEFAULT 'Asia/Shanghai',
        currency VARCHAR(10) DEFAULT 'CNY',
        is_active BOOLEAN DEFAULT FALSE COMMENT '是否为当前活跃领域',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        UNIQUE KEY uk_tenant_name (tenant_id, name)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    COMMENT='领域定义';
    """,
    # 5. Glossary terms
    """
    CREATE TABLE IF NOT EXISTS glossary_terms (
        id INT AUTO_INCREMENT PRIMARY KEY,
        tenant_id INT NOT NULL DEFAULT 1,
        domain_name VARCHAR(100) NOT NULL COMMENT '所属领域',
        term VARCHAR(200) NOT NULL COMMENT '术语名称',
        term_en VARCHAR(200) DEFAULT '' COMMENT '英文术语',
        description TEXT COMMENT '术语描述',
        expression TEXT COMMENT 'SQL 表达式',
        mapping_type VARCHAR(50) DEFAULT 'derived_column' COMMENT '映射类型',
        mapping_table VARCHAR(255),
        mapping_condition TEXT,
        mapping_precision INT,
        tags JSON COMMENT '标签列表',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        INDEX idx_tenant_domain_term (tenant_id, domain_name, term),
        FOREIGN KEY (tenant_id, domain_name) REFERENCES domains(tenant_id, name) ON DELETE CASCADE
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    COMMENT='业务术语表';
    """,
    # 6. Domain rules
    """
    CREATE TABLE IF NOT EXISTS domain_rules (
        id INT AUTO_INCREMENT PRIMARY KEY,
        tenant_id INT NOT NULL DEFAULT 1,
        domain_name VARCHAR(100) NOT NULL COMMENT '所属领域',
        rule_id VARCHAR(100) NOT NULL COMMENT '规则标识',
        description TEXT COMMENT '规则描述',
        pattern VARCHAR(500) DEFAULT '' COMMENT '匹配模式 (正则)',
        enforce JSON COMMENT '强制执行规则列表',
        sql_template TEXT COMMENT 'SQL 模板',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        UNIQUE KEY uk_tenant_rule (tenant_id, domain_name, rule_id),
        FOREIGN KEY (tenant_id, domain_name) REFERENCES domains(tenant_id, name) ON DELETE CASCADE
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    COMMENT='领域规则';
    """,
    # 7. Query history
    """
    CREATE TABLE IF NOT EXISTS query_history (
        id INT AUTO_INCREMENT PRIMARY KEY,
        tenant_id INT NOT NULL DEFAULT 1,
        session_id VARCHAR(100) DEFAULT '' COMMENT '会话 ID',
        query_id VARCHAR(100) DEFAULT '' COMMENT '查询 ID',
        nl_text TEXT NOT NULL COMMENT '自然语言查询',
        sql_generated TEXT COMMENT '生成的 SQL',
        sql_final TEXT COMMENT '最终执行的 SQL',
        domain VARCHAR(100) DEFAULT '' COMMENT '领域',
        db_id VARCHAR(100) DEFAULT '' COMMENT '数据库 ID',
        response_time_ms INT DEFAULT 0 COMMENT '响应时间 (毫秒)',
        rating INT DEFAULT 0 COMMENT '用户评分 0-5',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        INDEX idx_tenant_session (tenant_id, session_id),
        INDEX idx_created (created_at)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    COMMENT='查询历史';
    """,
    # 8. Feedback
    """
    CREATE TABLE IF NOT EXISTS feedback (
        id INT AUTO_INCREMENT PRIMARY KEY,
        tenant_id INT NOT NULL DEFAULT 1,
        feedback_id VARCHAR(100) NOT NULL COMMENT '反馈唯一标识',
        query_id VARCHAR(100) DEFAULT '' COMMENT '关联查询 ID',
        session_id VARCHAR(100) DEFAULT '' COMMENT '会话 ID',
        nl_input TEXT COMMENT '原始 NL 输入',
        sql_generated TEXT COMMENT '生成的 SQL',
        sql_final TEXT COMMENT '最终 SQL',
        rating INT DEFAULT 0 COMMENT '评分 0-5',
        feedback_text TEXT COMMENT '反馈文本',
        domain VARCHAR(100) DEFAULT '' COMMENT '领域',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        UNIQUE KEY uk_tenant_feedback_id (tenant_id, feedback_id),
        INDEX idx_feedback_created (created_at)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    COMMENT='用户反馈';
    """,
    # 9. Learning rule candidates
    (
        "\n"
        "    CREATE TABLE IF NOT EXISTS learning_rule_candidates (\n"
        "        id INT AUTO_INCREMENT PRIMARY KEY,\n"
        "        tenant_id INT NOT NULL DEFAULT 1,\n"
        "        rule_id VARCHAR(100) NOT NULL COMMENT '规则候选 ID',\n"
        "        description TEXT COMMENT '规则描述',\n"
        "        pattern VARCHAR(500) DEFAULT '' COMMENT '匹配模式',\n"
        "        sql_template TEXT COMMENT 'SQL 模板',\n"
        "        confidence FLOAT DEFAULT 0.0 COMMENT '置信度',\n"
        "        source_count INT DEFAULT 0 COMMENT '来源数量',\n"
        "        status VARCHAR(50) DEFAULT 'pending_review' COMMENT '状态: pending_r"
        "eview, approved, rejected',\n"
        "        domain VARCHAR(100) DEFAULT '' COMMENT '领域',\n"
        "        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,\n"
        "        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_T"
        "IMESTAMP,\n"
        "        UNIQUE KEY uk_tenant_rule_id (tenant_id, rule_id),\n"
        "        INDEX idx_status (status)\n"
        "    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci\n"
        "    COMMENT='学习规则候选';\n"
        "    "
    ),
    # 10. Learning quality trends
    """
    CREATE TABLE IF NOT EXISTS learning_quality_trends (
        id INT AUTO_INCREMENT PRIMARY KEY,
        tenant_id INT NOT NULL DEFAULT 1,
        record_date DATE NOT NULL COMMENT '记录日期',
        accuracy FLOAT DEFAULT 0.0 COMMENT '准确率',
        query_count INT DEFAULT 0 COMMENT '查询次数',
        avg_response_ms INT DEFAULT 0 COMMENT '平均响应时间',
        feedback_count INT DEFAULT 0 COMMENT '反馈数量',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        UNIQUE KEY uk_tenant_date (tenant_id, record_date)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    COMMENT='学习质量趋势';
    """,
    # 11. Learning query pairs
    """
    CREATE TABLE IF NOT EXISTS learning_query_pairs (
        id INT AUTO_INCREMENT PRIMARY KEY,
        tenant_id INT NOT NULL DEFAULT 1,
        session_id VARCHAR(100) DEFAULT '' COMMENT '会话 ID',
        nl_text TEXT NOT NULL COMMENT '自然语言查询',
        sql_text TEXT NOT NULL COMMENT 'SQL 语句',
        domain VARCHAR(100) DEFAULT '' COMMENT '领域',
        rating INT DEFAULT 0 COMMENT '评分',
        feedback_text TEXT COMMENT '反馈文本',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        INDEX idx_tenant_domain (tenant_id, domain),
        INDEX idx_created (created_at)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    COMMENT='学习查询对 (NL-SQL pairs)';
    """,
]

# ── Seed data: default settings ────────────────────────────────────────

_DEFAULT_SETTINGS = {
    "llm": {
        "default_model": "deepseek-v4-flash",
        "fallback_model": "",
        "routing_strategy": "complexity_aware",
        "temperature": 0.3,
        "max_tokens": 4096,
        "providers": {
            "openai": {
                "enabled": True,
                "models": ["gpt-4.1", "gpt-4.1-mini", "gpt-4o"],
                "api_key_configured": False,
            },
            "anthropic": {
                "enabled": True,
                "models": ["claude-sonnet-4", "claude-haiku-4.5"],
                "api_key_configured": False,
            },
            "deepseek": {
                "enabled": True,
                "models": ["deepseek-v4-flash", "deepseek-v4-pro"],
                "api_key_configured": False,
            },
            "google": {
                "enabled": True,
                "models": ["gemini-2.0-flash", "gemini-2.5-pro"],
                "api_key_configured": False,
            },
            "qwen": {
                "enabled": False,
                "models": ["qwen-max", "qwen-plus"],
                "api_key_configured": False,
            },
            "groq": {"enabled": False, "models": ["llama-4-maverick"], "api_key_configured": False},
        },
    },
    "harness": {
        "max_retries": 3,
        "timeout_seconds": 60,
        "checkpoint_dir": "data/checkpoints",
        "enable_parallel": False,
        "enable_tracing": True,
        "enable_validation": True,
        "enable_retry": True,
        "enable_fallback": True,
        "trace_ttl_hours": 24,
        "default_workflow": "nl2sql_domain_aware",
    },
    "database": {
        "default_db_id": "",
        "connection_pool_size": 5,
        "connection_timeout": 30,
        "query_timeout": 60,
        "max_rows": 1000,
        "enable_ssl": False,
        "cache_schema": True,
        "schema_cache_ttl": 300,
    },
    "rag": {
        "embedding_model": "bge-m3",
        "embedding_dim": 1024,
        "bm25_weight": 0.3,
        "vector_weight": 0.7,
        "top_k": 5,
        "similarity_threshold": 0.7,
        "chunk_size": 512,
        "chunk_overlap": 64,
        "enable_hybrid": True,
        "enable_rerank": False,
        "cache_embeddings": True,
    },
    "mcp": {
        "enabled": True,
        "host": "0.0.0.0",
        "port": 8100,
        "auto_register_tools": True,
        "max_concurrent": 5,
    },
}

# ── MySQLStore ─────────────────────────────────────────────────────────


class MySQLStore:
    """Singleton MySQL storage backend for QueryHarness.

    Provides async CRUD operations for all application entities. Uses
    aiomysql connection pool for efficient connection management.

    Usage::

        store = await MySQLStore.create(config)
        await store.save_setting("llm", {"default_model": "gpt-4o"})
        settings = await store.get_settings()
    """

    _instance: MySQLStore | None = None

    def __init__(self, pool: aiomysql.Pool) -> None:
        self._pool = pool

    @classmethod
    async def create(cls, config: dict[str, Any] | None = None) -> MySQLStore:
        """Create a MySQLStore instance with a connection pool.

        Args:
            config: MySQL connection config dict. Uses DEFAULT_MYSQL_CONFIG if None.
        """
        if cls._instance is not None:
            return cls._instance

        cfg = {**DEFAULT_MYSQL_CONFIG, **(config or {})}
        pool = await aiomysql.create_pool(
            host=cfg["host"],
            port=cfg["port"],
            user=cfg["user"],
            password=cfg["password"],
            db=cfg["db"],
            charset=cfg.get("charset", "utf8mb4"),
            autocommit=cfg.get("autocommit", True),
            minsize=2,
            maxsize=10,
        )
        cls._instance = cls(pool)
        logger.info("MySQLStore connected to %s:%s/%s", cfg["host"], cfg["port"], cfg["db"])
        return cls._instance

    async def close(self) -> None:
        """Close the connection pool."""
        self._pool.close()
        await self._pool.wait_closed()
        MySQLStore._instance = None
        logger.info("MySQLStore connection pool closed")

    async def _execute(self, sql: str, params: tuple[Any, ...] | None = None) -> int:
        """Execute a write SQL statement, returning affected row count."""
        async with self._pool.acquire() as conn, conn.cursor() as cur:
            await cur.execute(sql, params)
            return cur.rowcount

    async def _fetchone(
        self, sql: str, params: tuple[Any, ...] | None = None
    ) -> dict[str, Any] | None:
        """Fetch a single row as dict."""
        async with self._pool.acquire() as conn, conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute(sql, params)
            return await cur.fetchone()

    async def _fetchall(
        self, sql: str, params: tuple[Any, ...] | None = None
    ) -> list[dict[str, Any]]:
        """Fetch all rows as list of dicts."""
        async with self._pool.acquire() as conn, conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute(sql, params)
            return await cur.fetchall()

    # ── Database initialization ────────────────────────────────────────

    async def init_tables(self) -> None:
        """Initialize the current control database under a connection-owned lock."""
        async with mysql_schema_cursor(self._pool) as cur:
            await self._migrate_tables(cur)
        logger.info("All MySQL tables initialized")

    async def _migrate_tables(self, cur: aiomysql.Cursor) -> None:
        # The first four DDL statements include the parent of both domain child tables.
        for sql in _CREATE_TABLES_SQL[:4]:
            await cur.execute(sql.strip())
        await self._ensure_tenant_column(cur, "domains")
        await self._ensure_index(cur, "domains", "uk_tenant_name", "tenant_id, name", unique=True)
        for sql in _CREATE_TABLES_SQL[4:]:
            await cur.execute(sql.strip())

        await self._ensure_column(cur, "domains", "db_configs", "JSON")
        for column, sql_type in (
            ("mapping_table", "VARCHAR(255)"), ("mapping_condition", "TEXT"),
            ("mapping_precision", "INT"),
        ):
            await self._ensure_column(cur, "glossary_terms", column, sql_type)

        unique_keys = (
            ("user_settings", "uk_section", "uk_tenant_section", "section"),
            ("api_keys", "uk_provider", "uk_tenant_provider", "provider"),
            ("datasource_connections", "uk_db_id", "uk_tenant_db_id", "db_id"),
            ("domain_rules", "uk_rule", "uk_tenant_rule", "domain_name, rule_id"),
            ("feedback", "uk_feedback_id", "uk_tenant_feedback_id", "feedback_id"),
            ("learning_rule_candidates", "uk_rule_id", "uk_tenant_rule_id", "rule_id"),
            ("learning_quality_trends", "uk_date", "uk_tenant_date", "record_date"),
        )
        indexes = (
            ("glossary_terms", "idx_tenant_domain_term", "tenant_id, domain_name, term"),
            ("query_history", "idx_tenant_session", "tenant_id, session_id"),
            ("learning_query_pairs", "idx_tenant_domain", "tenant_id, domain"),
        )
        for table, _, index, columns in unique_keys:
            await self._ensure_tenant_column(cur, table)
            await self._ensure_index(cur, table, index, f"tenant_id, {columns}", unique=True)
        for table, index, columns in indexes:
            await self._ensure_tenant_column(cur, table)
            await self._ensure_index(cur, table, index, columns)

        await self._replace_domain_foreign_key(cur, "glossary_terms", "fk_glossary_domain_tenant")
        await self._replace_domain_foreign_key(cur, "domain_rules", "fk_domain_rules_domain_tenant")
        await self._drop_index(cur, "domains", "uk_name")
        for table, legacy_index, _, _ in unique_keys:
            await self._drop_index(cur, table, legacy_index)

    @staticmethod
    async def _ensure_tenant_column(cur: aiomysql.Cursor, table: str) -> None:
        await MySQLStore._ensure_column(cur, table, "tenant_id", "INT NOT NULL DEFAULT 1")

    @staticmethod
    async def _ensure_column(
        cur: aiomysql.Cursor, table: str, column: str, definition: str,
    ) -> None:
        await cur.execute(
            "SELECT 1 FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE() "
            "AND TABLE_NAME=%s AND COLUMN_NAME=%s", (table, column),
        )
        if await cur.fetchone() is None:
            await cur.execute(f"ALTER TABLE `{table}` ADD COLUMN `{column}` {definition}")

    @staticmethod
    async def _has_index(cur: aiomysql.Cursor, table: str, index: str) -> bool:
        await cur.execute(
            "SELECT 1 FROM information_schema.STATISTICS WHERE TABLE_SCHEMA=DATABASE() "
            "AND TABLE_NAME=%s AND INDEX_NAME=%s LIMIT 1", (table, index),
        )
        return await cur.fetchone() is not None

    async def _ensure_index(
        self, cur: aiomysql.Cursor, table: str, index: str, columns: str, *, unique: bool = False,
    ) -> None:
        if not await self._has_index(cur, table, index):
            kind = "UNIQUE KEY" if unique else "INDEX"
            await cur.execute(f"ALTER TABLE `{table}` ADD {kind} `{index}` ({columns})")

    async def _drop_index(self, cur: aiomysql.Cursor, table: str, index: str) -> None:
        if await self._has_index(cur, table, index):
            await cur.execute(f"ALTER TABLE `{table}` DROP INDEX `{index}`")

    @staticmethod
    async def _replace_domain_foreign_key(
        cur: aiomysql.Cursor, table: str, new_constraint: str,
    ) -> None:
        await cur.execute(
            "SELECT CONSTRAINT_NAME, GROUP_CONCAT(COLUMN_NAME ORDER BY ORDINAL_POSITION), "
            "GROUP_CONCAT(REFERENCED_COLUMN_NAME ORDER BY ORDINAL_POSITION) "
            "FROM information_schema.KEY_COLUMN_USAGE WHERE TABLE_SCHEMA=DATABASE() "
            "AND TABLE_NAME=%s AND REFERENCED_TABLE_SCHEMA=DATABASE() "
            "AND REFERENCED_TABLE_NAME='domains' GROUP BY CONSTRAINT_NAME",
            (table,),
        )
        constraints = await cur.fetchall()
        if not any(
            columns == "tenant_id,domain_name" and references == "tenant_id,name"
            for _, columns, references in constraints
        ):
            await cur.execute(
                f"ALTER TABLE `{table}` ADD CONSTRAINT `{new_constraint}` "
                "FOREIGN KEY (tenant_id, domain_name) "
                "REFERENCES domains(tenant_id, name) ON DELETE CASCADE"
            )
        for name, columns, references in constraints:
            if (columns, references) == ("domain_name", "name"):
                escaped = name.replace("`", "``")
                await cur.execute(f"ALTER TABLE `{table}` DROP FOREIGN KEY `{escaped}`")

    async def seed_defaults(self) -> None:
        """Seed default settings if none exist."""
        tenant_id = get_current_tenant_id()
        for section, config in _DEFAULT_SETTINGS.items():
            existing = await self._fetchone(
                "SELECT id FROM user_settings WHERE tenant_id = %s AND section = %s",
                (tenant_id, section),
            )
            if not existing:
                await self._execute(
                    "INSERT INTO user_settings (tenant_id, section, config) VALUES (%s, %s, %s)",
                    (tenant_id, section, json.dumps(config, ensure_ascii=False)),
                )
        logger.info("Default settings seeded")

    # ── Settings CRUD ──────────────────────────────────────────────────

    async def get_settings(self) -> dict[str, Any]:
        """Get all settings as a merged dict."""
        rows = await self._fetchall(
            "SELECT section, config FROM user_settings WHERE tenant_id = %s",
            (get_current_tenant_id(),),
        )
        result: dict[str, Any] = {}
        for row in rows:
            config = row["config"]
            if isinstance(config, str):
                config = json.loads(config)
            result[row["section"]] = config
        return result

    async def get_setting(self, section: str) -> dict[str, Any] | None:
        """Get a single settings section."""
        row = await self._fetchone(
            "SELECT config FROM user_settings WHERE tenant_id = %s AND section = %s",
            (get_current_tenant_id(), section),
        )
        if row is None:
            return None
        config = row["config"]
        if isinstance(config, str):
            return cast("dict[str, Any]", json.loads(config))
        return cast("dict[str, Any]", config)

    async def save_setting(self, section: str, config: dict[str, Any]) -> None:
        """Save or update a settings section."""
        config_json = json.dumps(config, ensure_ascii=False)
        await self._execute(
            "INSERT INTO user_settings (tenant_id, section, config) VALUES (%s, %s, %s) "
            "ON DUPLICATE KEY UPDATE config = VALUES(config)",
            (get_current_tenant_id(), section, config_json),
        )

    async def edit_setting[T](
        self, section: str, update: Callable[[Any], tuple[dict[str, Any], T]],
    ) -> tuple[dict[str, Any], T]:
        """Read, merge and commit a tenant setting under the same row lock."""
        key = (get_current_tenant_id(), section)
        async with self._pool.acquire() as conn:
            try:
                async with conn.cursor() as cursor:
                    await cursor.execute("START TRANSACTION")
                    await cursor.execute(
                        "INSERT INTO user_settings (tenant_id, section, config) "
                        "VALUES (%s, %s, '{}') "
                        "ON DUPLICATE KEY UPDATE config = user_settings.config", key,
                    )
                    await cursor.execute(
                        "SELECT config FROM user_settings WHERE tenant_id = %s AND section = %s "
                        "FOR UPDATE", key,
                    )
                    row = await cursor.fetchone()
                    if row is None:
                        raise RuntimeError("Settings row unavailable")
                    raw = row[0]
                    config, prepared = update(json.loads(raw) if isinstance(raw, str) else raw)
                    await cursor.execute(
                        "UPDATE user_settings SET config = %s "
                        "WHERE tenant_id = %s AND section = %s",
                        (json.dumps(config, ensure_ascii=False), *key),
                    )
                await conn.commit()
            except BaseException:
                conn.close()
                raise
        return config, prepared

    async def delete_setting(self, section: str) -> None:
        """Delete a settings section."""
        await self._execute(
            "DELETE FROM user_settings WHERE tenant_id = %s AND section = %s",
            (get_current_tenant_id(), section),
        )

    # ── API Keys ───────────────────────────────────────────────────────

    async def get_api_key(self, provider: str) -> str | None:
        """Get an API key for a provider."""
        row = await self._fetchone(
            "SELECT api_key FROM api_keys WHERE tenant_id = %s AND provider = %s",
            (get_current_tenant_id(), provider),
        )
        return row["api_key"] if row else None

    async def save_api_key(self, provider: str, api_key: str) -> None:
        """Save or update an API key."""
        await self._execute(
            "INSERT INTO api_keys (tenant_id, provider, api_key) VALUES (%s, %s, %s) "
            "ON DUPLICATE KEY UPDATE api_key = VALUES(api_key)",
            (get_current_tenant_id(), provider, api_key),
        )

    async def delete_api_key(self, provider: str) -> None:
        """Delete an API key."""
        await self._execute(
            "DELETE FROM api_keys WHERE tenant_id = %s AND provider = %s",
            (get_current_tenant_id(), provider),
        )

    async def get_all_api_keys(self) -> dict[str, str]:
        """Get all API keys."""
        rows = await self._fetchall(
            "SELECT provider, api_key FROM api_keys WHERE tenant_id = %s",
            (get_current_tenant_id(),),
        )
        return {r["provider"]: r["api_key"] for r in rows}

    # ── Connections ────────────────────────────────────────────────────

    async def save_connection(self, conn_info: dict[str, Any]) -> None:
        """Save a database connection profile."""
        await self._execute(
            "INSERT INTO datasource_connections "
            "(tenant_id, db_id, alias, db_type, host, port, database_name, username, password, "
            "file_path, extra_params) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
            "ON DUPLICATE KEY UPDATE "
            "alias = VALUES(alias), db_type = VALUES(db_type), "
            "host = VALUES(host), port = VALUES(port), "
            "database_name = VALUES(database_name), "
            "username = VALUES(username), password = VALUES(password), "
            "file_path = VALUES(file_path), extra_params = VALUES(extra_params)",
            (
                get_current_tenant_id(),
                conn_info.get("db_id", ""),
                conn_info.get("alias", ""),
                conn_info.get("db_type", ""),
                conn_info.get("host", ""),
                conn_info.get("port", 0),
                conn_info.get("database_name", ""),
                conn_info.get("username", ""),
                conn_info.get("password", ""),
                conn_info.get("file_path", ""),
                json.dumps(conn_info.get("extra_params", {}), ensure_ascii=False),
            ),
        )

    async def get_connections(self) -> list[dict[str, Any]]:
        """Get all saved connection profiles."""
        rows = await self._fetchall(
            "SELECT * FROM datasource_connections WHERE tenant_id = %s ORDER BY updated_at DESC",
            (get_current_tenant_id(),),
        )
        return rows

    async def get_connection(self, db_id: str) -> dict[str, Any] | None:
        """Get a single connection profile."""
        return await self._fetchone(
            "SELECT * FROM datasource_connections WHERE tenant_id = %s AND db_id = %s",
            (get_current_tenant_id(), db_id),
        )

    async def delete_connection(self, db_id: str) -> None:
        """Delete a connection profile."""
        await self._execute(
            "DELETE FROM datasource_connections WHERE tenant_id = %s AND db_id = %s",
            (get_current_tenant_id(), db_id),
        )

    # ── Domains CRUD ───────────────────────────────────────────────────

    async def get_domains(self) -> list[dict[str, Any]]:
        """Get all domains."""
        rows = await self._fetchall(
            "SELECT * FROM domains WHERE tenant_id = %s ORDER BY created_at DESC",
            (get_current_tenant_id(),),
        )
        for row in rows:
            if isinstance(row.get("keywords"), str):
                row["keywords"] = json.loads(row["keywords"])
        return rows

    async def get_domain(self, name: str) -> dict[str, Any] | None:
        """Get a single domain by name."""
        row = await self._fetchone(
            "SELECT * FROM domains WHERE tenant_id = %s AND name = %s",
            (get_current_tenant_id(), name),
        )
        if row and isinstance(row.get("keywords"), str):
            row["keywords"] = json.loads(row["keywords"])
        return row

    _DOMAIN_SNAPSHOT_SQL = """
            SELECT d.name, d.label_zh, d.label_en, d.description_zh, d.description_en,
                d.keywords, d.db_configs, d.timezone, d.currency, d.is_active,
                (SELECT JSON_ARRAYAGG(JSON_OBJECT(
                    'term', g.term, 'term_en', g.term_en, 'description', g.description,
                    'expression', g.expression, 'mapping_type', g.mapping_type, 'tags', g.tags,
                    'mapping_table', g.mapping_table, 'mapping_condition', g.mapping_condition,
                    'mapping_precision', g.mapping_precision
                )) FROM glossary_terms AS g
                    WHERE g.tenant_id = d.tenant_id AND g.domain_name = d.name) AS glossary,
                (SELECT JSON_ARRAYAGG(JSON_OBJECT(
                    'id', r.rule_id, 'description', r.description, 'pattern', r.pattern,
                    'enforce', r.enforce, 'sql_template', r.sql_template
                )) FROM domain_rules AS r
                    WHERE r.tenant_id = d.tenant_id AND r.domain_name = d.name) AS rules
            FROM domains AS d WHERE d.tenant_id = %s
            """

    @staticmethod
    def _decode_domain_snapshot(row: dict[str, Any]) -> dict[str, Any]:
        for field in ("keywords", "db_configs", "glossary", "rules"):
            value = row[field]
            row[field] = json.loads(value) if isinstance(value, str) else value
        return row

    async def get_domain_snapshot(self, name: str) -> dict[str, Any] | None:
        """Read one tenant's domain, glossary and rules from one MySQL snapshot."""
        row = await self._fetchone(
            self._DOMAIN_SNAPSHOT_SQL + " AND d.name = %s",
            (get_current_tenant_id(), name),
        )
        return self._decode_domain_snapshot(row) if row is not None else None

    async def get_domain_snapshots(self) -> list[dict[str, Any]]:
        """Read this tenant's complete domain catalog in one statement."""
        rows = await self._fetchall(
            self._DOMAIN_SNAPSHOT_SQL + " ORDER BY d.name", (get_current_tenant_id(),),
        )
        return [self._decode_domain_snapshot(row) for row in rows]

    async def save_domain(self, domain: dict[str, Any], *, create_only: bool = False) -> None:
        """Save or update a domain."""
        sql = (
            "INSERT INTO domains (name, label_zh, label_en, description_zh, "
            "description_en, keywords, timezone, currency, is_active, tenant_id, db_configs) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
        )
        update = (
            "ON DUPLICATE KEY UPDATE "
            "label_zh = VALUES(label_zh), label_en = VALUES(label_en), "
            "description_zh = VALUES(description_zh), description_en = VALUES(description_en), "
            "keywords = VALUES(keywords), timezone = VALUES(timezone), "
            "currency = VALUES(currency), is_active = VALUES(is_active), "
            "db_configs = COALESCE(VALUES(db_configs), db_configs)"
        )
        databases = domain.get("databases")
        await self._execute(
            sql if create_only else f"{sql} {update}",
            (
                domain.get("name", ""),
                domain.get("label_zh", ""),
                domain.get("label_en", ""),
                domain.get("description_zh", ""),
                domain.get("description_en", ""),
                json.dumps(domain.get("keywords", []), ensure_ascii=False),
                domain.get("timezone", "Asia/Shanghai"),
                domain.get("currency", "CNY"),
                domain.get("is_active", False),
                get_current_tenant_id(),
                json.dumps(databases, ensure_ascii=False) if databases is not None else None,
            ),
        )

    async def delete_domain(self, name: str) -> bool:
        """Delete a domain and its glossary/terms (CASCADE)."""
        deleted = await self._execute(
            "DELETE FROM domains WHERE tenant_id = %s AND name = %s",
            (get_current_tenant_id(), name),
        )
        return deleted > 0

    async def set_active_domain(self, name: str) -> None:
        """Set a domain as the active one (deactivates others)."""
        tenant_id = get_current_tenant_id()
        await self._execute(
            "UPDATE domains SET is_active = FALSE WHERE tenant_id = %s", (tenant_id,)
        )
        await self._execute(
            "UPDATE domains SET is_active = TRUE WHERE tenant_id = %s AND name = %s",
            (tenant_id, name),
        )

    # ── Glossary Terms ─────────────────────────────────────────────────

    async def get_glossary_terms(self, domain_name: str) -> list[dict[str, Any]]:
        """Get all glossary terms for a domain."""
        rows = await self._fetchall(
            "SELECT * FROM glossary_terms WHERE tenant_id = %s AND domain_name = %s "
            "ORDER BY term",
            (get_current_tenant_id(), domain_name),
        )
        for row in rows:
            if isinstance(row.get("tags"), str):
                row["tags"] = json.loads(row["tags"])
        return rows

    async def save_glossary_term(self, domain_name: str, term_data: dict[str, Any]) -> None:
        """Save or update a glossary term."""
        await self._execute(
            "INSERT INTO glossary_terms "
            "(tenant_id, domain_name, term, term_en, description, expression, mapping_type, tags) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s) "
            "ON DUPLICATE KEY UPDATE "
            "term_en = VALUES(term_en), description = VALUES(description), "
            "expression = VALUES(expression), mapping_type = VALUES(mapping_type), "
            "tags = VALUES(tags)",
            (
                get_current_tenant_id(),
                domain_name,
                term_data.get("term", ""),
                term_data.get("term_en", ""),
                term_data.get("description", ""),
                term_data.get("expression", ""),
                term_data.get("mapping_type", "derived_column"),
                json.dumps(term_data.get("tags", []), ensure_ascii=False),
            ),
        )

    async def delete_glossary_term(self, domain_name: str, term: str) -> None:
        """Delete a glossary term."""
        await self._execute(
            "DELETE FROM glossary_terms WHERE tenant_id = %s AND domain_name = %s "
            "AND term = %s",
            (get_current_tenant_id(), domain_name, term),
        )

    # ── Domain Rules ───────────────────────────────────────────────────

    async def get_rules(self, domain_name: str) -> list[dict[str, Any]]:
        """Get all rules for a domain."""
        rows = await self._fetchall(
            "SELECT * FROM domain_rules WHERE tenant_id = %s AND domain_name = %s "
            "ORDER BY rule_id",
            (get_current_tenant_id(), domain_name),
        )
        for row in rows:
            if isinstance(row.get("enforce"), str):
                row["enforce"] = json.loads(row["enforce"])
        return rows

    async def save_rule(self, domain_name: str, rule_data: dict[str, Any]) -> None:
        """Save or update a domain rule."""
        await self._execute(
            "INSERT INTO domain_rules "
            "(tenant_id, domain_name, rule_id, description, pattern, enforce, sql_template) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s) "
            "ON DUPLICATE KEY UPDATE "
            "description = VALUES(description), pattern = VALUES(pattern), "
            "enforce = VALUES(enforce), sql_template = VALUES(sql_template)",
            (
                get_current_tenant_id(),
                domain_name,
                rule_data.get("rule_id", rule_data.get("id", "")),
                rule_data.get("description", ""),
                rule_data.get("pattern", ""),
                json.dumps(rule_data.get("enforce", []), ensure_ascii=False),
                rule_data.get("sql_template", ""),
            ),
        )

    async def delete_rule(self, domain_name: str, rule_id: str) -> None:
        """Delete a domain rule."""
        await self._execute(
            "DELETE FROM domain_rules WHERE tenant_id = %s AND domain_name = %s "
            "AND rule_id = %s",
            (get_current_tenant_id(), domain_name, rule_id),
        )

    # ── Query History ──────────────────────────────────────────────────

    async def save_query(self, query_data: dict[str, Any]) -> None:
        """Save a query to history."""
        await self._execute(
            "INSERT INTO query_history "
            "(tenant_id, session_id, query_id, nl_text, sql_generated, sql_final, "
            "domain, db_id, response_time_ms, rating) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (
                get_current_tenant_id(),
                query_data.get("session_id", ""),
                query_data.get("query_id", ""),
                query_data.get("nl_text", ""),
                query_data.get("sql_generated", ""),
                query_data.get("sql_final", ""),
                query_data.get("domain", ""),
                query_data.get("db_id", ""),
                query_data.get("response_time_ms", 0),
                query_data.get("rating", 0),
            ),
        )

    async def get_query_history(
        self, limit: int = 50, session_id: str = ""
    ) -> list[dict[str, Any]]:
        """Get recent query history."""
        if session_id:
            rows = await self._fetchall(
                "SELECT * FROM query_history WHERE tenant_id = %s AND session_id = %s "
                "ORDER BY created_at DESC LIMIT %s",
                (get_current_tenant_id(), session_id, limit),
            )
        else:
            rows = await self._fetchall(
                "SELECT * FROM query_history WHERE tenant_id = %s "
                "ORDER BY created_at DESC LIMIT %s",
                (get_current_tenant_id(), limit),
            )
        return rows

    # ── Feedback ───────────────────────────────────────────────────────

    async def save_feedback(self, feedback_data: dict[str, Any]) -> None:
        """Save a feedback record."""
        await self._execute(
            "INSERT INTO feedback "
            "(tenant_id, feedback_id, query_id, session_id, nl_input, sql_generated, "
            "sql_final, rating, feedback_text, domain) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (
                get_current_tenant_id(),
                feedback_data.get("feedback_id", feedback_data.get("id", "")),
                feedback_data.get("query_id", ""),
                feedback_data.get("session_id", ""),
                feedback_data.get("nl_input", ""),
                feedback_data.get("sql_generated", ""),
                feedback_data.get("sql_final", ""),
                feedback_data.get("rating", 0),
                feedback_data.get("feedback_text", ""),
                feedback_data.get("domain", ""),
            ),
        )

    async def get_feedback_stats(self) -> dict[str, Any]:
        """Get feedback statistics."""
        row = await self._fetchone(
            "SELECT COUNT(*) as total, AVG(rating) as avg_rating FROM feedback "
            "WHERE tenant_id = %s AND rating > 0",
            (get_current_tenant_id(),),
        )
        if row is None:
            return {"total": 0, "avg_rating": 0.0, "ratings_distribution": {}, "recent_count_7d": 0}

        dist = await self._fetchall(
            "SELECT rating, COUNT(*) as cnt FROM feedback "
            "WHERE tenant_id = %s AND rating > 0 GROUP BY rating",
            (get_current_tenant_id(),),
        )
        recent = await self._fetchone(
            "SELECT COUNT(*) as cnt FROM feedback "
            "WHERE tenant_id = %s AND created_at >= DATE_SUB(NOW(), INTERVAL 7 DAY)",
            (get_current_tenant_id(),),
        )
        return {
            "total": row["total"] or 0,
            "avg_rating": round(row["avg_rating"] or 0.0, 2),
            "ratings_distribution": {str(r["rating"]): r["cnt"] for r in dist},
            "recent_count_7d": recent["cnt"] if recent else 0,
        }

    async def get_recent_feedback(self, limit: int = 20) -> list[dict[str, Any]]:
        """Get recent feedback records."""
        return await self._fetchall(
            "SELECT * FROM feedback WHERE tenant_id = %s "
            "ORDER BY created_at DESC LIMIT %s",
            (get_current_tenant_id(), limit),
        )

    # ── Learning ───────────────────────────────────────────────────────

    async def get_learning_stats(self) -> dict[str, Any]:
        """Get learning statistics."""
        tenant_id = get_current_tenant_id()
        total_q = await self._fetchone(
            "SELECT COUNT(*) as cnt FROM query_history WHERE tenant_id = %s", (tenant_id,)
        )
        total_f = await self._fetchone(
            "SELECT COUNT(*) as cnt FROM feedback WHERE tenant_id = %s", (tenant_id,)
        )
        avg_r = await self._fetchone(
            "SELECT AVG(rating) as avg FROM feedback "
            "WHERE tenant_id = %s AND rating > 0",
            (tenant_id,),
        )
        rule_c = await self._fetchone(
            "SELECT COUNT(*) as cnt FROM learning_rule_candidates WHERE tenant_id = %s",
            (tenant_id,),
        )
        pending = await self._fetchone(
            "SELECT COUNT(*) as cnt FROM learning_rule_candidates "
            "WHERE tenant_id = %s AND status = 'pending_review'",
            (tenant_id,),
        )
        approved = await self._fetchone(
            "SELECT COUNT(*) as cnt FROM learning_rule_candidates "
            "WHERE tenant_id = %s AND status = 'approved'",
            (tenant_id,),
        )
        return {
            "total_queries": total_q["cnt"] if total_q else 0,
            "total_feedback": total_f["cnt"] if total_f else 0,
            "avg_rating": round(avg_r["avg"] if avg_r and avg_r["avg"] else 0.0, 2),
            "rule_candidates_count": rule_c["cnt"] if rule_c else 0,
            "pending_review_count": pending["cnt"] if pending else 0,
            "approved_rules_count": approved["cnt"] if approved else 0,
        }

    async def get_rule_candidates(self, status: str = "pending_review") -> list[dict[str, Any]]:
        """Get rule candidates by status."""
        return await self._fetchall(
            "SELECT * FROM learning_rule_candidates "
            "WHERE tenant_id = %s AND status = %s ORDER BY confidence DESC",
            (get_current_tenant_id(), status),
        )

    async def save_rule_candidate(self, candidate: dict[str, Any]) -> None:
        """Save or update a rule candidate."""
        await self._execute(
            "INSERT INTO learning_rule_candidates "
            "(tenant_id, rule_id, description, pattern, sql_template, confidence, "
            "source_count, status, domain) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) "
            "ON DUPLICATE KEY UPDATE "
            "description = VALUES(description), pattern = VALUES(pattern), "
            "sql_template = VALUES(sql_template), confidence = VALUES(confidence), "
            "source_count = VALUES(source_count), status = VALUES(status), "
            "domain = VALUES(domain)",
            (
                get_current_tenant_id(),
                candidate.get("id", candidate.get("rule_id", "")),
                candidate.get("description", ""),
                candidate.get("pattern", ""),
                candidate.get("sql_template", ""),
                candidate.get("confidence", 0.0),
                candidate.get("source_count", 0),
                candidate.get("status", "pending_review"),
                candidate.get("domain", ""),
            ),
        )

    async def update_rule_candidate_status(self, rule_id: str, status: str) -> bool:
        """Update rule candidate status."""
        result = await self._execute(
            "UPDATE learning_rule_candidates SET status = %s "
            "WHERE tenant_id = %s AND rule_id = %s",
            (status, get_current_tenant_id(), rule_id),
        )
        return result > 0

    async def save_quality_trend(self, trend: dict[str, Any]) -> None:
        """Save a quality trend record."""
        await self._execute(
            "INSERT INTO learning_quality_trends "
            "(tenant_id, record_date, accuracy, query_count, avg_response_ms, feedback_count) "
            "VALUES (%s, %s, %s, %s, %s, %s) "
            "ON DUPLICATE KEY UPDATE "
            "accuracy = VALUES(accuracy), query_count = VALUES(query_count), "
            "avg_response_ms = VALUES(avg_response_ms), feedback_count = VALUES(feedback_count)",
            (
                get_current_tenant_id(),
                trend.get("date", ""),
                trend.get("accuracy", 0.0),
                trend.get("query_count", 0),
                trend.get("avg_response_ms", 0),
                trend.get("feedback_count", 0),
            ),
        )

    async def get_quality_trends(self, days: int = 30) -> list[dict[str, Any]]:
        """Get quality trends for the last N days."""
        return await self._fetchall(
            "SELECT * FROM learning_quality_trends "
            "WHERE tenant_id = %s AND record_date >= DATE_SUB(CURDATE(), INTERVAL %s DAY) "
            "ORDER BY record_date ASC",
            (get_current_tenant_id(), days),
        )

    async def save_query_pair(self, pair: dict[str, Any]) -> None:
        """Save an NL-SQL query pair."""
        await self._execute(
            "INSERT INTO learning_query_pairs "
            "(tenant_id, session_id, nl_text, sql_text, domain, rating, feedback_text) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s)",
            (
                get_current_tenant_id(),
                pair.get("session_id", ""),
                pair.get("nl_text", ""),
                pair.get("sql_text", ""),
                pair.get("domain", ""),
                pair.get("rating", 0),
                pair.get("feedback_text", ""),
            ),
        )

    async def get_query_pairs(self, limit: int = 20, domain: str = "") -> list[dict[str, Any]]:
        """Get recent NL-SQL query pairs."""
        if domain:
            return await self._fetchall(
                "SELECT * FROM learning_query_pairs WHERE tenant_id = %s AND domain = %s "
                "ORDER BY created_at DESC LIMIT %s",
                (get_current_tenant_id(), domain, limit),
            )
        return await self._fetchall(
            "SELECT * FROM learning_query_pairs WHERE tenant_id = %s "
            "ORDER BY created_at DESC LIMIT %s",
            (get_current_tenant_id(), limit),
        )


# ── Module-level helpers ───────────────────────────────────────────────


async def init_db(config: dict[str, Any] | None = None) -> MySQLStore:
    """Initialize the MySQL database and return the store.

    Creates the database if it doesn't exist, creates all tables,
    and seeds default settings.

    Args:
        config: MySQL connection config dict (host, port, user, password, db).
    """
    cfg = {**DEFAULT_MYSQL_CONFIG, **(config or {})}

    # Create the database if it doesn't exist
    try:
        import aiomysql

        temp_conn = await aiomysql.connect(
            host=cfg["host"],
            port=cfg["port"],
            user=cfg["user"],
            password=cfg["password"],
            charset="utf8mb4",
        )
        async with temp_conn.cursor() as cur:
            await cur.execute(
                f"CREATE DATABASE IF NOT EXISTS `{cfg['db']}` "
                "CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
            )
        temp_conn.close()
    except Exception as e:
        logger.warning("Could not create database: %s. Make sure MySQL is running.", e)

    store = await MySQLStore.create(cfg)
    await store.init_tables()
    await store.seed_defaults()
    return store


def get_store() -> MySQLStore | None:
    """Get the singleton MySQLStore instance, or None if not initialized."""
    return MySQLStore._instance
