"""Security regressions exercise actual SQLite/DuckDB and real JWT checks."""

import sqlite3

import duckdb
import pytest
from fastapi.testclient import TestClient

from app.api import create_app
from app.auth.security import create_access_token
from app.db.connections import ConnectionFactory, DatabaseType
from app.nodes.base import NodeInput
from app.nodes.execute_sql import ExecuteSQLNode
from tests.api_client import authenticated_client


@pytest.mark.parametrize(
    "sql",
    [
        "/* comment */ DELETE FROM items",
        "WITH x AS (SELECT 1) DELETE FROM items",
        "SELECT 1; DELETE FROM items",
        "PRAGMA query_only=OFF",
        "ATTACH ':memory:' AS extra",
        "SELECT load_extension('unsafe')",
        "SELECT * FROM read_csv('private.csv')",
        "SELECT nextval('seq')",
        "SELECT 1 INTO OUTFILE 'private.csv'",
        "SELECT 1 /*!50000 INTO OUTFILE 'private.csv' */",
        "SELECT 1 /*M! INTO OUTFILE 'private.csv' */",
        "SELECT * FROM 'private.csv'",
        "SELECT @x:=1",
    "SELECT 1--1; DELETE FROM items",
    "SELECT 1--\u00a0x; DELETE FROM items",
    "SELECT 1 /* outer /* inner */ ; DELETE FROM items; -- x */",
    ],
)
@pytest.mark.parametrize("driver", ["sqlite", "duckdb"])
async def test_side_effects_never_execute(driver, sql):
    conn = sqlite3.connect(":memory:") if driver == "sqlite" else duckdb.connect()
    try:
        conn.execute("CREATE TABLE items(id INTEGER)")
        conn.execute("INSERT INTO items VALUES (1)")
        output = await ExecuteSQLNode().execute(
            NodeInput(
                query_text=sql,
                context={"connection": conn},
                config={"allow_write": True},
            )
        )
        assert output.metadata["status"] == "blocked"
        assert conn.execute("SELECT COUNT(*) FROM items").fetchone()[0] == 1
    finally:
        conn.close()


async def test_sqlite_deadline_and_reuse():
    conn = sqlite3.connect(":memory:")
    try:
        sql = (
            "WITH RECURSIVE numbers(x) AS (SELECT 1 UNION ALL SELECT x+1 "
            "FROM numbers WHERE x<100000000) SELECT SUM(x) FROM numbers"
        )
        node = ExecuteSQLNode()
        output = await node.execute(
            NodeInput(query_text=sql, context={"connection": conn}, config={"timeout_ms": 1})
        )
        assert output.errors
        output = await node.execute(NodeInput(query_text="SELECT 1", context={"connection": conn}))
        assert output.result["rows"] == [[1]]
    finally:
        conn.close()


def test_business_access_requires_operator():
    client = TestClient(create_app())
    assert client.get("/api/v1/databases").status_code == 401
    client.headers["Authorization"] = f"Bearer {create_access_token(2, 'ordinary')}"
    assert client.get("/api/v1/databases").status_code == 403
    client.headers["Authorization"] = f"Bearer {create_access_token(1, 'operator')}"
    assert client.get("/api/v1/databases").status_code == 200


def test_registration_requires_configured_invitation():
    response = TestClient(create_app()).post(
        "/api/v1/auth/register",
        json={
            "username": "uninvited",
            "email": "uninvited@example.com",
            "password": "Strong12345!",
        },
    )
    assert response.status_code == 403


def test_default_signing_secret_fails_closed(monkeypatch):
    from app.config.settings import settings

    monkeypatch.setattr(settings, "jwt_secret", "change-me-in-production")
    with pytest.raises(ValueError, match="JWT_SECRET"):
        create_app()


def test_domain_path_and_secrets(monkeypatch):
    from app.api import settings as api_settings

    client = authenticated_client(create_app())
    assert client.post("/api/v1/domains", json={"name": "../escaped"}).status_code == 422
    monkeypatch.setattr(
        api_settings,
        "_settings_store",
        {
            "llm": {"providers": {"test": {"api_key": "raw", "api_key_enc": "cipher"}}},
        },
    )
    body = client.get("/api/v1/settings").json()
    assert "raw" not in str(body)
    assert "cipher" not in str(body)


async def test_direct_row_limit():
    conn = await ConnectionFactory.create(DatabaseType.SQLITE, {"path": ":memory:"}, "limit_test")
    try:
        conn.execute("CREATE TABLE items(id INTEGER)")
        conn.executemany("INSERT INTO items VALUES (?)", [(i,) for i in range(150)])
        result = (
            authenticated_client(create_app())
            .post(
                "/api/v1/execute",
                json={
                    "database": "limit_test",
                    "sql": "SELECT * FROM items",
                    "max_rows": 1,
                },
            )
            .json()
        )
        assert result["row_count"] == 1
        assert result["truncated"] is True
    finally:
        ConnectionFactory.close("limit_test")
