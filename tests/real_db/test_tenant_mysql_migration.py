"""Tenant storage upgrade and recovery on disposable MySQL."""

import asyncio
import uuid
from contextlib import asynccontextmanager

import aiomysql
import pytest

from app.auth.tenant import reset_current_tenant_id, set_current_tenant_id
from app.storage.mysql_store import MySQLStore
from tests.real_db.support import admin_connection, admin_sql

pytestmark = [
    pytest.mark.real_database,
    pytest.mark.parametrize("service", ["mysql"], indirect=True),
]


@asynccontextmanager
async def tenant_store(service):
    database = f"de_tenant_{uuid.uuid4().hex}"
    config = service.config(admin=True)
    del config["database"]
    async with admin_connection(service) as admin:
        await admin_sql(service, admin, f"CREATE DATABASE `{database}` CHARACTER SET utf8mb4")
        try:
            async with aiomysql.create_pool(**config, db=database, autocommit=True) as pool:
                yield MySQLStore(pool)
        finally:
            await admin_sql(service, admin, f"DROP DATABASE `{database}`")


async def legacy_schema(store, *, children):
    suffix = " ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci"
    await store._execute(
        "CREATE TABLE domains (id INT AUTO_INCREMENT PRIMARY KEY, name VARCHAR(100) NOT NULL, "
        "label_zh VARCHAR(200) DEFAULT '', label_en VARCHAR(200) DEFAULT '', "
        "description_zh TEXT, description_en TEXT, keywords JSON, timezone VARCHAR(50), "
        "currency VARCHAR(10), is_active BOOLEAN DEFAULT FALSE, "
        "created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, UNIQUE KEY uk_name (name))" + suffix
    )
    await store._execute("INSERT INTO domains (name,label_en) VALUES ('sales','Legacy sales')")
    await store._execute(
        "CREATE TABLE user_settings (id INT AUTO_INCREMENT PRIMARY KEY, "
        "section VARCHAR(100) NOT NULL, config JSON NOT NULL, UNIQUE KEY uk_section(section))"
        + suffix
    )
    await store._execute(
        "INSERT INTO user_settings (section, config) VALUES ('llm', %s)",
        ('{"default_model":"legacy"}',),
    )
    if children:
        await store._execute(
            "CREATE TABLE domain_rules (id INT AUTO_INCREMENT PRIMARY KEY, "
            "domain_name VARCHAR(100) NOT NULL, rule_id VARCHAR(100) NOT NULL, "
            "description TEXT, pattern VARCHAR(500), enforce JSON, sql_template TEXT, "
            "UNIQUE KEY uk_rule (domain_name,rule_id), "
            "FOREIGN KEY (domain_name) REFERENCES domains(name) ON DELETE CASCADE)" + suffix
        )
        await store._execute(
            "INSERT INTO domain_rules(domain_name,rule_id,description) "
            "VALUES ('sales','r1','Legacy rule')"
        )
        await store._execute(
            "CREATE TABLE glossary_terms (id INT AUTO_INCREMENT PRIMARY KEY, "
            "domain_name VARCHAR(100) NOT NULL, term VARCHAR(200) NOT NULL, "
            "INDEX idx_domain_term (domain_name,term), "
            "FOREIGN KEY (domain_name) REFERENCES domains(name) ON DELETE CASCADE)" + suffix
        )
        await store._execute(
            "INSERT INTO glossary_terms(domain_name,term) VALUES ('sales','Legacy term')"
        )


async def test_tenant_mysql_migration_preserves_legacy_rows_and_scopes_mutations(service):
    async with tenant_store(service) as store:
        await legacy_schema(store, children=True)
        await store.init_tables()
        await store.init_tables()
        assert (await store.get_setting("llm"))["default_model"] == "legacy"
        assert (await store.get_domain("sales"))["label_en"] == "Legacy sales"
        assert (await store.get_domain("sales"))["db_configs"] is None
        assert (await store.get_rules("sales"))[0]["description"] == "Legacy rule"
        assert (await store.get_glossary_terms("sales"))[0]["term"] == "Legacy term"
        assert (await store.get_glossary_terms("sales"))[0]["mapping_precision"] is None

        token = set_current_tenant_id(2)
        try:
            assert await store.get_setting("llm") is None
            assert not await store.get_rules("sales")
            with pytest.raises(aiomysql.IntegrityError) as error:
                await store.save_rule("sales", {"id": "r1"})
            assert error.value.args[0] == 1452
            await store.save_setting("llm", {"default_model": "tenant-two"})
            await store.save_domain({"name": "sales"})
            await store.save_rule("sales", {"id": "r1", "description": "Tenant two"})
            assert (await store.get_rules("sales"))[0]["description"] == "Tenant two"
            await store.delete_domain("sales")
            assert not await store.get_rules("sales")
        finally:
            reset_current_tenant_id(token)

        assert (await store.get_setting("llm"))["default_model"] == "legacy"
        assert (await store.get_rules("sales"))[0]["description"] == "Legacy rule"


async def test_tenant_mysql_migration_resumes_with_missing_children_and_concurrent_start(service):
    async with tenant_store(service) as store:
        await legacy_schema(store, children=False)
        await asyncio.gather(store.init_tables(), MySQLStore(store._pool).init_tables())
        assert (await store.get_domain("sales"))["label_en"] == "Legacy sales"
        await store.save_glossary_term("sales", {"term": "Revenue"})
        assert [term["term"] for term in await store.get_glossary_terms("sales")] == ["Revenue"]


async def test_tenant_mysql_migration_isolates_same_id_datasource_profiles(service):
    async with tenant_store(service) as store:
        await store._execute(
            "CREATE TABLE datasource_connections (id INT AUTO_INCREMENT PRIMARY KEY, "
            "db_id VARCHAR(100) NOT NULL, alias VARCHAR(200), db_type VARCHAR(50), "
            "host VARCHAR(255), port INT, database_name VARCHAR(200), username VARCHAR(200), "
            "password VARCHAR(500), file_path VARCHAR(500), extra_params JSON, "
            "updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP, "
            "UNIQUE KEY uk_db_id (db_id))"
        )
        await store._execute(
            "INSERT INTO datasource_connections (db_id,alias,db_type,file_path) "
            "VALUES ('orders','Legacy orders','sqlite','legacy.db')"
        )
        await store.init_tables()
        assert (await store.get_connection("orders"))["file_path"] == "legacy.db"

        token = set_current_tenant_id(2)
        try:
            assert await store.get_connection("orders") is None
            profile = {"db_id": "orders", "alias": "Tenant two", "db_type": "sqlite"}
            await store.save_connection({**profile, "file_path": "tenant-two.db"})
            await store.save_connection({**profile, "file_path": "replacement.db"})
            assert [row["file_path"] for row in await store.get_connections()] == ["replacement.db"]
            await store.delete_connection("orders")
            assert await store.get_connection("orders") is None
        finally:
            reset_current_tenant_id(token)

        assert [row["file_path"] for row in await store.get_connections()] == ["legacy.db"]


async def test_tenant_mysql_migration_recovers_preupgrade_snapshot(service):
    tables = ("domains", "user_settings", "glossary_terms", "domain_rules")
    snapshot = []
    async with tenant_store(service) as store:
        await legacy_schema(store, children=True)
        for table in tables:
            definition = await store._fetchone(f"SHOW CREATE TABLE `{table}`")
            rows = await store._fetchall(f"SELECT * FROM `{table}`")
            snapshot.append((table, definition["Create Table"], rows))
        await store.init_tables()
        await store.save_setting("llm", {"default_model": "after-upgrade"})
        await store.delete_domain("sales")

    async with tenant_store(service) as restored:
        for table, ddl, rows in snapshot:
            await restored._execute(ddl)
            for row in rows:
                fields = ", ".join(f"`{column}`" for column in row)
                placeholders = ", ".join(["%s"] * len(row))
                await restored._execute(
                    f"INSERT INTO `{table}` ({fields}) VALUES ({placeholders})",
                    tuple(row.values()),
                )
            assert await restored._fetchall(f"SELECT * FROM `{table}`") == rows
        with pytest.raises(aiomysql.IntegrityError) as error:
            await restored._execute("INSERT INTO domains (name) VALUES ('sales')")
        assert error.value.args[0] == 1062
        await restored.init_tables()
        assert (await restored.get_setting("llm"))["default_model"] == "legacy"
        assert (await restored.get_rules("sales"))[0]["description"] == "Legacy rule"
