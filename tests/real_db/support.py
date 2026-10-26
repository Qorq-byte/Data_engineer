"""Fixture-only accounts and explicit opt-in; never load host database settings."""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any

DATABASE = "de_stage40_fixture"
ADMIN_PASSWORD = "stage40-disposable-admin"
READER_PASSWORD = "stage40-disposable-reader"


@dataclass(frozen=True)
class Service:
    kind: str
    port: int
    password: str = field(default=READER_PASSWORD, repr=False)

    def config(self, *, admin: bool = False) -> dict[str, Any]:
        return {"host": "127.0.0.1", "port": self.port, "database": DATABASE,
                "user": ("stage40_admin" if self.kind == "postgresql" else "root")
                if admin else "stage40_reader",
                "password": ADMIN_PASSWORD if admin else self.password}


def configured_service(kind: str) -> Service:
    if os.environ.get("DE_REAL_DB_TESTS") != "1":
        raise ValueError("Real database tests require explicit opt-in")
    if kind not in {"postgresql", "mysql"}:
        raise ValueError("Unknown test service")
    name = "DE_REAL_PG_PORT" if kind == "postgresql" else "DE_REAL_MYSQL_PORT"
    try:
        port = int(os.environ[name])
    except (KeyError, ValueError):
        raise ValueError("Explicit disposable service ports are required") from None
    if not 1024 <= port <= 65535 or port in {3306, 5432}:
        raise ValueError("Use a dedicated dynamically mapped test port")
    return Service(kind, port)


@asynccontextmanager
async def admin_connection(service):
    config = service.config(admin=True)
    if service.kind == "postgresql":
        import asyncpg

        conn = await asyncpg.connect(**config, timeout=10)
        try:
            yield conn
        finally:
            await conn.close()
    else:
        import aiomysql

        config["db"] = config.pop("database")
        conn = await aiomysql.connect(**config, autocommit=True, connect_timeout=10)
        try:
            yield conn
        finally:
            conn.close()
            await conn.ensure_closed()


async def admin_sql(service, conn, sql, args=()):
    if service.kind == "postgresql":
        return await conn.fetch(sql, *args)
    cursor = await conn.cursor()
    try:
        await cursor.execute(sql, args if args else None)
        return await cursor.fetchall() if cursor.description else []
    finally:
        await cursor.close()


async def initialize(service):
    async with admin_connection(service) as conn:
        if service.kind == "postgresql":
            existing = await conn.fetch(
                "SELECT table_name FROM information_schema.tables WHERE table_schema='public'"
            )
        else:
            existing = await admin_sql(service, conn,
                "SELECT table_name FROM information_schema.tables WHERE table_schema=%s",
                (DATABASE,))
        if existing:
            raise RuntimeError("Refusing to initialize a nonempty fixture database")
        statements = [
            "CREATE TABLE stage40_marker (marker VARCHAR(64) PRIMARY KEY)",
            "INSERT INTO stage40_marker VALUES ('isolated-stage40-v1')",
            "CREATE TABLE users (id INTEGER PRIMARY KEY, name VARCHAR(64) NOT NULL, "
            "email VARCHAR(128) UNIQUE NOT NULL, note VARCHAR(64))",
            "CREATE TABLE orders (id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, "
            "amount DECIMAL(10,2) NOT NULL, status VARCHAR(20) NOT NULL DEFAULT 'pending', "
            "CONSTRAINT orders_user_fk FOREIGN KEY (user_id) REFERENCES users(id))",
            "CREATE INDEX orders_user_idx ON orders(user_id)",
            "CREATE TABLE composite_parent (region INTEGER, id INTEGER, "
            "PRIMARY KEY(region,id))",
            "CREATE TABLE composite_child (region INTEGER, parent_id INTEGER, "
            "CONSTRAINT composite_fk FOREIGN KEY(region,parent_id) "
            "REFERENCES composite_parent(region,id))",
            "INSERT INTO users VALUES (1,'Ada','ada@example.invalid',NULL), "
            "(2,'Bo','bo@example.invalid','fixture'), (3,'Noorders','none@example.invalid',NULL)",
            "INSERT INTO orders VALUES (10,1,10.25,'completed'),(20,1,20.50,'completed'),"
            "(30,2,99.00,'pending'),(40,2,5.00,'completed'),(50,2,1.00,'cancelled')",
        ]
        for sql in statements:
            await admin_sql(service, conn, sql)
        if service.kind == "postgresql":
            await conn.execute("CREATE SCHEMA other_fixture")
            await conn.execute("CREATE TABLE other_fixture.users (id INTEGER PRIMARY KEY)")
            await conn.execute("CREATE TABLE other_fixture.orders (id INTEGER PRIMARY KEY, "
                "user_id INTEGER CONSTRAINT orders_user_fk REFERENCES other_fixture.users(id))")
            await conn.execute(f"CREATE ROLE stage40_reader LOGIN PASSWORD '{READER_PASSWORD}'")
            await conn.execute("GRANT USAGE ON SCHEMA public TO stage40_reader")
            await conn.execute("GRANT SELECT ON ALL TABLES IN SCHEMA public TO stage40_reader")
            await conn.execute(f"REVOKE TEMP ON DATABASE {DATABASE} FROM PUBLIC")
            version = await conn.fetchval("SELECT version()")
        else:
            await admin_sql(service, conn,
                f"CREATE USER 'stage40_reader'@'%' IDENTIFIED BY '{READER_PASSWORD}'")
            await admin_sql(service, conn, f"GRANT SELECT ON {DATABASE}.* TO 'stage40_reader'@'%'")
            version = (await admin_sql(service, conn, "SELECT VERSION()"))[0][0]
        return str(version)
