"""Exercise the legacy auth schema and issued JWTs on disposable MySQL."""

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import jwt
import pytest
import pytest_asyncio
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials

from app.api import create_app, domains, query
from app.api import settings as api_settings
from app.api.auth import get_current_user
from app.auth import database
from app.auth.security import decode_access_token, hash_password
from app.auth.tenant import reset_current_tenant_id, set_current_tenant_id
from app.config.settings import settings
from app.db.connections import ConnectionFactory, DatabaseType
from app.learning import rule_registry
from app.mcp.fastmcp_server import create_nl2sql_server
from app.mcp.query_projection import query_error
from app.models.domain import BusinessRule, DomainConfig, GlossaryTerm
from app.storage.mysql_store import MySQLStore
from app.workflow import query_domain
from tests.real_db.support import admin_connection, admin_sql
from tests.real_db.test_tenant_mysql_migration import tenant_store
from tests.test_api.test_metric_query import body
from tests.test_api.test_query_rules import CaptureRouter
from tests.test_mcp.test_metric_tools import args, http_call, http_exchange

pytestmark = [
    pytest.mark.real_database,
    pytest.mark.parametrize("service", ["mysql"], indirect=True),
]

PASSWORD = "fixture-password"
LOGIN = {"email": "legacy@example.test", "password": PASSWORD}


@pytest_asyncio.fixture
async def auth_schema(service, monkeypatch):
    name = f"de_auth_{uuid.uuid4().hex}"
    config = service.config(admin=True)
    for key in ("host", "port", "user", "password"):
        monkeypatch.setattr(settings, f"auth_mysql_{key}", config[key])
    monkeypatch.setattr(settings, "auth_mysql_database", name)
    monkeypatch.setattr(settings, "admin_user_ids", [1, 2])
    monkeypatch.setattr(settings, "shared_worker_release", "")
    async with admin_connection(service) as admin:
        await admin_sql(service, admin, f"CREATE DATABASE `{name}` CHARACTER SET utf8mb4")
        try:
            await admin_sql(service, admin, f"USE `{name}`")
            await admin_sql(
                service, admin,
                "CREATE TABLE users (id INT AUTO_INCREMENT PRIMARY KEY, "
                "username VARCHAR(50) NOT NULL UNIQUE, email VARCHAR(120) NOT NULL UNIQUE, "
                "password_hash VARCHAR(255) NOT NULL, created_at TIMESTAMP DEFAULT "
                "CURRENT_TIMESTAMP, updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP "
                "ON UPDATE CURRENT_TIMESTAMP) ENGINE=InnoDB",
            )
            await admin_sql(
                service, admin,
                "INSERT INTO users(username,email,password_hash) VALUES (%s,%s,%s)",
                ("legacy", LOGIN["email"], hash_password(PASSWORD)),
            )
            yield admin
        finally:
            await database.close_auth_pool()
            await admin_sql(service, admin, f"DROP DATABASE `{name}`")


def legacy_token(**claims):
    now = datetime.now(UTC)
    return jwt.encode(
        {"sub": "1", "username": "legacy", "iat": now, "exp": now + timedelta(minutes=2),
         **claims},
        settings.jwt_secret, algorithm=settings.jwt_algorithm,
    )


async def test_tenant_auth_mysql_migrates_and_preserves_legacy_jwt(service, auth_schema):
    await asyncio.gather(database.init_auth_db(), database.init_auth_db())
    await database.close_auth_pool()
    await database.init_auth_db()
    assert await admin_sql(service, auth_schema, "SELECT id, tenant_id FROM users") == ((1, 1),)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app()), base_url="http://testserver",
    ) as client:
        response = await client.post("/api/v1/auth/login", json=LOGIN)
        assert response.status_code == 200
        issued = response.json()
        assert issued["user"]["id"] == 1 and issued["user"]["tenant_id"] == 1
        assert decode_access_token(issued["access_token"])["tenant_id"] == 1
        assert decode_access_token(issued["access_token"])["token_version"] == 0
        headers = {"Authorization": f"Bearer {legacy_token()}"}
        assert (await client.get("/api/v1/auth/me", headers=headers)).json()["tenant_id"] == 1
        assert (await client.get("/api/v1/databases", headers=headers)).status_code == 200
        for invalid in (None, "", True, "1"):
            headers = {"Authorization": f"Bearer {legacy_token(tenant_id=invalid)}"}
            response = await client.get("/api/v1/databases", headers=headers)
            assert response.status_code == 401
        for invalid in (None, True, "0", -1):
            headers = {"Authorization": f"Bearer {legacy_token(token_version=invalid)}"}
            assert (await client.get("/api/v1/auth/me", headers=headers)).status_code == 401


async def test_tenant_auth_mysql_rejects_bad_index_and_retries(service, auth_schema):
    await admin_sql(service, auth_schema, "ALTER TABLE users ADD INDEX idx_tenant(username)")
    with pytest.raises(RuntimeError, match="Incompatible users.idx_tenant"):
        await database.init_auth_db()
    with pytest.raises(RuntimeError, match="schema unavailable"):
        async with database.get_connection():
            pytest.fail("Authentication opened on an incomplete schema")
    await admin_sql(service, auth_schema, "ALTER TABLE users DROP INDEX idx_tenant")
    await database.init_auth_db()
    async with database.get_connection() as conn, conn.cursor() as cur:
        await cur.execute("SELECT tenant_id FROM users")
        assert await cur.fetchone() == (1,)


async def test_tenant_auth_mysql_rest_and_mcp_operator_boundary(
    service, auth_schema, test_sqlite_db, monkeypatch,
):
    await database.init_auth_db()
    email = "tenant-two@example.test"
    await admin_sql(
        service, auth_schema,
        "INSERT INTO users(tenant_id,username,email,password_hash) VALUES (2,%s,%s,%s)",
        ("second", email, hash_password(PASSWORD)),
    )
    catalog = Path(__file__).resolve().parents[1] / "fixtures" / "metric_catalog.yml"
    monkeypatch.setattr(settings, "metric_catalog_path", str(catalog))
    await ConnectionFactory.create(DatabaseType.SQLITE, {"path": test_sqlite_db}, "metric-db")
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=create_app()), base_url="http://testserver",
        ) as client:
            response = await client.post("/api/v1/auth/login", json=LOGIN)
            assert response.status_code == 200
            owner = response.json()["access_token"]
            response = await client.post(
                "/api/v1/auth/login", json={"email": email, "password": PASSWORD},
            )
            assert response.status_code == 200
            other = response.json()["access_token"]
            assert decode_access_token(other)["tenant_id"] == 2
            filters = {"domain": "ecommerce", "database": "metric-db"}
            for token, expected in ((owner, 200), (other, 403)):
                headers = {"Authorization": f"Bearer {token}"}
                discovery = await client.get("/api/v1/metrics", params=filters, headers=headers)
                query = await client.post(
                    "/api/v1/query", json=body(execute=True), headers=headers,
                )
                assert discovery.status_code == query.status_code == expected
                if token == owner:
                    assert query.json()["execution"]["rows"]

        server = create_nl2sql_server(trust_local_metric_tools=True)
        scope = {"domain": "ecommerce", "db_id": "metric-db"}
        assert (await http_call(server, "list_metrics", scope, owner))["status"] == "completed"
        executed = await http_call(server, "metric_query", args(execute=True), owner)
        assert executed["status"] == "completed" and executed["executed"] and executed["rows"]
        for tool, arguments in (("list_metrics", scope), ("metric_query", args(execute=True))):
            denied = await http_exchange(
                server, "tools/call", {"name": tool, "arguments": arguments}, other
            )
            assert denied.status_code == 403 and set(denied.json()) == {"detail"}
    finally:
        await ConnectionFactory.aclose("metric-db")


async def test_tenant_auth_mysql_revokes_rest_and_existing_mcp_sessions(service, auth_schema):
    server = create_nl2sql_server()
    http_app = server.http_app(path="/mcp", stateless_http=False, json_response=True)
    sse_app = server.http_app(transport="sse", path="/sse")
    assert not database.auth_database_ready()

    async with (
        http_app.router.lifespan_context(http_app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=create_app()), base_url="http://rest",
        ) as rest,
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=http_app), base_url="http://mcp",
            headers={"Accept": "application/json, text/event-stream"},
        ) as mcp,
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=sse_app), base_url="http://sse",
        ) as sse,
    ):
        async def sign_in(expected_tenant, expected_version):
            response = await rest.post("/api/v1/auth/login", json=LOGIN)
            assert response.status_code == 200, response.text
            token = response.json()["access_token"]
            claims = decode_access_token(token)
            assert (claims["tenant_id"], claims["token_version"]) == (
                expected_tenant, expected_version,
            )
            return {"Authorization": f"Bearer {token}"}

        async def assert_rest(headers, me_status, operator_status):
            assert (await rest.get("/api/v1/auth/me", headers=headers)).status_code == me_status
            databases = await rest.get("/api/v1/databases", headers=headers)
            assert databases.status_code == operator_status

        assert database.auth_database_ready()
        previous = await sign_in(1, 0)
        unsigned_version = {"Authorization": f"Bearer {legacy_token()}"}
        await assert_rest(unsigned_version, 200, 200)
        initialize = {
            "jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
                "protocolVersion": "2025-06-18", "capabilities": {},
                "clientInfo": {"name": "fixture", "version": "1"},
            },
        }
        initialized = await mcp.post("/mcp", headers=previous, json=initialize)
        assert initialized.status_code == 200
        active_session = {**previous, "Mcp-Session-Id": initialized.headers["mcp-session-id"]}

        await admin_sql(service, auth_schema, "UPDATE users SET tenant_id=2 WHERE id=1")
        await assert_rest(previous, 401, 401)
        await assert_rest(unsigned_version, 401, 401)
        old_mcp = await mcp.post("/mcp", headers=active_session, json={
            "jsonrpc": "2.0", "id": 2, "method": "resources/list",
        })
        assert old_mcp.status_code == 401
        assert old_mcp.headers["www-authenticate"] == "Bearer"
        assert (await sse.get("/sse", headers=previous)).status_code == 401
        assert (await sse.post("/messages/", headers=previous)).status_code == 401
        moved = await sign_in(2, 1)
        await assert_rest(moved, 200, 403)
        assert (await sse.get("/sse", headers=moved)).status_code == 403

        await admin_sql(service, auth_schema, "UPDATE users SET tenant_id=1 WHERE id=1")
        await admin_sql(service, auth_schema, "UPDATE users SET token_version=0 WHERE id=1")
        await assert_rest(previous, 401, 401)
        await assert_rest(moved, 401, 401)
        returned = await sign_in(1, 2)
        await assert_rest(returned, 200, 200)
        rebound = await mcp.post("/mcp", headers={**active_session, **returned}, json={
            "jsonrpc": "2.0", "id": 3, "method": "resources/list",
        })
        assert rebound.status_code == 404

        await admin_sql(
            service, auth_schema, "UPDATE users SET token_version=token_version+1 WHERE id=1",
        )
        await assert_rest(returned, 401, 401)
        renewed = await sign_in(1, 3)
        assert (await mcp.post("/mcp", headers=renewed, json=initialize)).status_code == 200
        await assert_rest(renewed, 200, 200)

        await database.close_auth_pool()
        await assert_rest(renewed, 503, 503)
        assert (await sse.get("/sse", headers=renewed)).status_code == 503
        await database.init_auth_db()
        new_password = "changed-fixture-password"
        changed = await rest.put("/api/v1/auth/password", headers=renewed, json={
            "old_password": PASSWORD, "new_password": new_password,
        })
        assert changed.status_code == 200, changed.text
        removed = await rest.request("DELETE", "/api/v1/auth/account", headers=renewed, json={
            "password": new_password,
        })
        assert removed.status_code == 200, removed.text
        await assert_rest(renewed, 401, 401)
    assert not database.auth_database_ready()


async def test_tenant_auth_mysql_query_uses_own_domain_knowledge(
    service, auth_schema, monkeypatch, tmp_path,
):
    await database.init_auth_db()
    email = "domain-tenant-two@example.test"
    await admin_sql(
        service, auth_schema,
        "INSERT INTO users(tenant_id,username,email,password_hash) VALUES (2,%s,%s,%s)",
        ("domain-tenant-two", email, hash_password(PASSWORD)),
    )
    name = "shared_sales"
    db_id = "tenant-domain-fixture"
    monkeypatch.setitem(domains._domain_manager._domains, name, DomainConfig(
        name=name,
        glossary=[GlossaryTerm("saleslabel", description="legacy-glossary-marker")],
        rules=[BusinessRule("shared-rule", "legacy-rule-marker", enforce=["require=legacy_tag"])],
    ))
    monkeypatch.setitem(
        domains._domain_manager._domains, "legacy_only", DomainConfig("legacy_only"),
    )
    learned = rule_registry.LearnedRuleRegistry(
        rule_registry.LearnedRuleStore(tmp_path / "rules.db"),
    )
    monkeypatch.setattr(rule_registry, "learned_rule_registry", learned)
    monkeypatch.setattr(ConnectionFactory, "_connections", {})
    monkeypatch.setattr(ConnectionFactory, "_infos", {})

    async with (
        tenant_store(service) as store,
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=create_app()), base_url="http://testserver",
        ) as rest,
    ):
        await store.init_tables()
        monkeypatch.setattr(MySQLStore, "_instance", store)
        await store.save_domain({"name": name})
        await store.save_glossary_term(name, {
            "term": "saleslabel", "description": "wrong-db-marker",
        })

        async def run_for_account(login_email, expected_tenant, value, own, other):
            login = await rest.post("/api/v1/auth/login", json={
                "email": login_email, "password": PASSWORD,
            })
            assert login.status_code == 200, login.text
            jwt_token = login.json()["access_token"]
            identity = await get_current_user(HTTPAuthorizationCredentials(
                scheme="Bearer", credentials=jwt_token,
            ))
            assert identity["tenant_id"] == expected_tenant
            token = set_current_tenant_id(identity["tenant_id"])
            try:
                if expected_tenant == 2:
                    await store.save_domain({"name": name})
                    await store.save_glossary_term(name, {
                        "term": "saleslabel", "description": "tenant-two-glossary-marker",
                        "expression": "orders.id", "tags": ["owned"],
                    })
                    await store.save_rule(name, {
                        "id": "shared-rule", "description": "tenant-two-rule-marker",
                        "enforce": ["require=tenant_two_tag"],
                    })
                    snapshot = await query_domain.resolve_query_domain(name)
                    assert snapshot.glossary[0].mapping.expression == "orders.id"
                    assert snapshot.glossary[0].tags == ["owned"]
                connection = await ConnectionFactory.create(DatabaseType.SQLITE, {}, db_id)
                connection.execute("CREATE TABLE orders (id INTEGER PRIMARY KEY)")
                connection.execute("INSERT INTO orders VALUES (?)", (value,))
                assert learned.record_review("learned-shared", "approved", {
                    "domain": name, "description": f"{own}-learned-marker",
                })
                router = CaptureRouter()
                monkeypatch.setattr(query, "get_default_router", lambda: router)

                async def resolve_tenant_router(model):
                    return router

                monkeypatch.setattr(
                    api_settings, "resolve_tenant_query_router", resolve_tenant_router,
                )
                request = query.QueryRequest(
                    nl_text="list saleslabel orders",
                    domain=name.upper() if expected_tenant == 2 else name, database=db_id,
                    session_id="same-tenant-domain-session", num_candidates=1, execute=True,
                )
                headers = {"Authorization": f"Bearer {jwt_token}"}
                response = await rest.post(
                    "/api/v1/query", json=request.model_dump(), headers=headers,
                )
                if expected_tenant == 1:
                    assert response.status_code == 200, response.text
                    result = query.QueryResponse.model_validate(response.json())
                else:
                    assert response.status_code == 403
                    result = await query.submit_query(request)
                assert result.status == "completed"
                assert result.execution["rows"] == [[value]]
                assert [rule["description"] for rule in result.applied_rules] == [
                    f"{own}-learned-marker"
                ]
                assert f"{own}-glossary-marker" in router.prompt_text
                assert f"{own}-rule-marker" in router.prompt_text
                assert f"{other}-" not in router.prompt_text
                assert "wrong-db-marker" not in router.prompt_text
                expected_warning = "legacy_tag" if expected_tenant == 1 else "tenant_two_tag"
                assert any(
                    expected_warning in item for item in result.validation["business_warnings"]
                )

                if expected_tenant == 2:
                    calls = len(router.calls)
                    missing = request.model_copy(update={"domain": "legacy_only", "session_id": ""})
                    with pytest.raises(HTTPException) as rejected:
                        await query.submit_query(missing)
                    assert rejected.value.status_code == 400
                    with monkeypatch.context() as patch:
                        patch.setattr(query_domain, "get_store", lambda: None)
                        with pytest.raises(HTTPException) as unavailable:
                            await query.submit_query(request)
                    assert unavailable.value.status_code == 503
                    failure = query_error(unavailable.value)
                    assert failure["error_code"] == "DOMAIN_CONFIG_UNAVAILABLE"
                    assert missing.session_id == "" and len(router.calls) == calls
            finally:
                await ConnectionFactory.aclose(db_id)
                reset_current_tenant_id(token)

        try:
            await run_for_account(LOGIN["email"], 1, 101, "legacy", "tenant-two")
            await run_for_account(email, 2, 202, "tenant-two", "legacy")
            assert learned.store.get("learned-shared").description == "legacy-learned-marker"
        finally:
            learned.store.close()
