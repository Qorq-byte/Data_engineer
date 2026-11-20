"""Persist and consume tenant model configuration through the query entry points."""

import asyncio
import json
import socket

import httpx
import pytest
import uvicorn

from app.api import create_app
from app.api import settings as api_settings
from app.auth import database
from app.auth.security import hash_password
from app.auth.tenant import reset_current_tenant_id, set_current_tenant_id
from app.config.settings import settings
from app.db.connections import ConnectionFactory
from app.db.tenant_profiles import load_tenant_query_profiles
from app.llm import factory, router
from app.mcp.fastmcp_server import create_nl2sql_server
from app.security.crypto import decrypt_secret
from app.storage import mysql_store
from app.storage.mysql_store import MySQLStore
from tests.real_db.support import admin_sql
from tests.real_db.test_tenant_auth_mysql import PASSWORD
from tests.real_db.test_tenant_auth_mysql import auth_schema as auth_schema
from tests.real_db.test_tenant_mysql_migration import tenant_store

pytestmark = [
    pytest.mark.real_database,
    pytest.mark.parametrize("service", ["mysql"], indirect=True),
]
MCP_INIT = {
    "jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
        "protocolVersion": "2025-06-18", "capabilities": {},
        "clientInfo": {"name": "tenant-fixture", "version": "1"},
    },
}
MCP_READY = {"jsonrpc": "2.0", "method": "notifications/initialized"}


def model_response(request):
    body = json.loads(request.content)
    assert request.url.path == "/v1/chat/completions"
    assert "openai-organization" not in request.headers
    assert "openai-project" not in request.headers
    assert "x-host-fixture" not in request.headers
    assert request.headers["host"] == request.extensions["sni_hostname"]
    assert request.url.host == "8.8.8.8"
    sql = "SELECT id FROM orders"
    common = {"id": "fixture", "created": 0, "model": body["model"]}
    if not body["stream"]:
        return httpx.Response(200, json={
            **common, "object": "chat.completion",
            "choices": [{
                "index": 0, "finish_reason": "stop",
                "message": {"role": "assistant", "content": sql},
            }],
            "usage": {"prompt_tokens": 5, "completion_tokens": 4, "total_tokens": 9},
        })
    events = [
        {**common, "object": "chat.completion.chunk", "choices": [{
            "index": 0, "delta": delta, "finish_reason": reason,
        }]}
        for delta, reason in (({"content": sql}, None), ({}, "stop"))
    ]
    payload = "".join(f"data: {json.dumps(event)}\n\n" for event in events)
    return httpx.Response(
        200, content=payload + "data: [DONE]\n\n",
        headers={"content-type": "text/event-stream"},
    )


async def exercise_tenant_mcp(headers, member, call):
    app = create_nl2sql_server().http_app(
        path="/mcp", stateless_http=False, json_response=True,
    )
    async with app.router.lifespan_context(app), httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://mcp",
        headers={"Accept": "application/json, text/event-stream"},
    ) as mcp:
        assert (await mcp.post("/mcp", headers=member, json=MCP_INIT)).status_code == 403
        session_headers = {}
        for tenant in (2, 3):
            response = await mcp.post("/mcp", headers=headers[tenant], json=MCP_INIT)
            assert response.status_code == 200, response.text
            session = response.headers["mcp-session-id"]
            session_headers[tenant] = {
                **headers[tenant], "Mcp-Session-Id": session,
                "MCP-Protocol-Version": "2025-06-18",
            }
            notified = await mcp.post("/mcp", headers=session_headers[tenant], json=MCP_READY)
            assert notified.status_code == 202

        for tenant in (2, 3):
            response = await mcp.post("/mcp", headers=session_headers[tenant], json=call)
            assert response.status_code == 200, response.text
            result = response.json()["result"]["structuredContent"]
            assert result["status"] == "completed", result
            assert result["rows"] == [[tenant]]
        borrowed = {**session_headers[2], **headers[3]}
        rejected = await mcp.post("/mcp", headers=borrowed, json=call)
        assert rejected.status_code == 404


async def exercise_tenant_mcp_sse(headers, call):
    app = create_nl2sql_server().http_app(path="/sse", transport="sse")
    server = uvicorn.Server(uvicorn.Config(
        app, lifespan="on", access_log=False, timeout_graceful_shutdown=5,
    ))
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
        running = asyncio.create_task(server.serve(sockets=[listener]))
        try:
            async with asyncio.timeout(20):
                while not server.started:
                    if running.done():
                        await running
                        pytest.fail("MCP SSE fixture failed to start")
                    await asyncio.sleep(0.01)
                async with (
                    httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}") as http,
                    http.stream("GET", "/sse", headers=headers[2]) as events,
                ):
                    assert events.status_code == 200
                    lines = events.aiter_lines()

                    async def read_event():
                        async for line in lines:
                            if line.startswith("data: "):
                                return line.removeprefix("data: ")
                        pytest.fail("MCP SSE stream closed")

                    endpoint = await read_event()
                    wrong = await http.post(endpoint, headers=headers[3], json=MCP_INIT)
                    assert wrong.status_code == 404
                    opened = await http.post(endpoint, headers=headers[2], json=MCP_INIT)
                    assert opened.status_code == 202
                    assert json.loads(await read_event())["id"] == 1
                    ready = await http.post(endpoint, headers=headers[2], json=MCP_READY)
                    assert ready.status_code == 202
                    result = await http.post(endpoint, headers=headers[2], json=call)
                    assert result.status_code == 202
                    query = json.loads(await read_event())["result"]["structuredContent"]
                    assert query["status"] == "completed" and query["rows"] == [[2]], query
        finally:
            server.should_exit = True
            await running


async def test_persisted_tenant_models_drive_json_sse_and_mcp(
    service, auth_schema, monkeypatch, tmp_path,
):
    await database.init_auth_db()
    monkeypatch.setattr(api_settings, "_tenant_llm_runtimes", {})
    monkeypatch.setattr(ConnectionFactory, "_connections", {})
    monkeypatch.setattr(ConnectionFactory, "_infos", {})
    monkeypatch.setattr(factory, "_mock_forced", lambda: False)
    monkeypatch.setattr(settings, "tenant_admin_user_ids", {})
    monkeypatch.setattr(settings, "tenant_llm_endpoints", {
        tenant: [f"https://tenant-{tenant}.example.test/v1"] for tenant in (2, 3)
    })

    async def resolve_public_fixture(host):
        assert host in {"tenant-2.example.test", "tenant-3.example.test"}
        return "8.8.8.8"

    monkeypatch.setattr(router, "resolve_public_model_address", resolve_public_fixture)
    monkeypatch.setenv("OPENAI_API_KEY", "ambient-fixture-key")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://ambient.example.test/v1")
    monkeypatch.setenv("OPENAI_ORG_ID", "ambient-fixture-org")
    monkeypatch.setenv("OPENAI_PROJECT_ID", "ambient-fixture-project")
    monkeypatch.setenv(
        "OPENAI_CUSTOM_HEADERS",
        "X-Host-Fixture: ambient\nAuthorization: Bearer ambient\nHost: ambient.example.test",
    )

    intercepted = []

    def endpoint(request):
        body = json.loads(request.content)
        intercepted.append((
            request.headers["host"], request.headers["authorization"],
            body["model"], body["max_tokens"],
        ))
        return model_response(request)

    sdk_class = router.AsyncOpenAI

    def isolated_sdk(**options):
        client = options["http_client"]
        assert not client.trust_env and not client.follow_redirects
        options["http_client"] = httpx.AsyncClient(
            transport=httpx.MockTransport(endpoint), trust_env=False,
            event_hooks=client.event_hooks,
        )
        unused_transport_clients.append(client)
        return sdk_class(**options)

    unused_transport_clients = []
    monkeypatch.setattr(router, "AsyncOpenAI", isolated_sdk)

    app = create_app()
    async with (
        tenant_store(service) as store,
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver",
        ) as rest,
    ):
        await store.init_tables()
        monkeypatch.setattr(MySQLStore, "_instance", store)
        await store.save_setting("llm", {"default_model": "default-fixture"})
        default_router = factory.current_llm_router()
        headers = {}
        users = {}
        target_schemas = {}
        profiles = {}
        db_id = "tenant-model-db"
        request = {
            "nl_text": "list order ids", "database": db_id, "domain": "",
            "num_candidates": 1, "execute": True,
        }
        try:
            for tenant, role in ((2, "admin"), (3, "admin"), (2, "member")):
                username = f"model-tenant-{tenant}-{role}"
                email = f"{username}@example.test"
                await admin_sql(
                    service, auth_schema,
                    "INSERT INTO users(tenant_id,username,email,password_hash) "
                    "VALUES (%s,%s,%s,%s)",
                    (tenant, username, email, hash_password(PASSWORD)),
                )
                login = await rest.post(
                    "/api/v1/auth/login", json={"email": email, "password": PASSWORD},
                )
                assert login.status_code == 200, login.text
                assert login.json()["user"]["tenant_id"] == tenant
                bearer = {"Authorization": f"Bearer {login.json()['access_token']}"}
                denied = await rest.get("/api/v1/settings/llm", headers=bearer)
                assert denied.status_code == 403
                if role == "member":
                    member = bearer
                    users[3].append(login.json()["user"]["id"])
                    continue
                headers[tenant] = bearer
                users[tenant] = [login.json()["user"]["id"]]
                target = f"{settings.auth_mysql_database}_model_{tenant}"
                await admin_sql(service, auth_schema, f"CREATE DATABASE `{target}`")
                target_schemas[tenant] = target
                await admin_sql(service, auth_schema, f"""
                    CREATE TABLE `{target}`.orders (id INTEGER PRIMARY KEY)
                """)
                await admin_sql(
                    service, auth_schema, f"INSERT INTO `{target}`.orders VALUES (%s)", (tenant,),
                )
                await admin_sql(
                    service, auth_schema, f"GRANT SELECT ON `{target}`.* TO 'stage40_reader'@'%'"
                )
                profiles[tenant] = [{
                    "db_id": db_id, "db_type": "mysql",
                    "config": {**service.config(), "database": target},
                }]
                if tenant == 3:
                    profiles[tenant].append({**profiles[tenant][0], "db_id": "only-tenant-three"})

            monkeypatch.setattr(settings, "tenant_admin_user_ids", users)
            for identity, expected in ((headers[2], "tenant"), (headers[3], "tenant"),
                                       (member, "account")):
                workspace = await rest.get("/api/v1/auth/workspace", headers=identity)
                assert workspace.status_code == 200 and workspace.json()["access"] == expected
            path = tmp_path / "profiles.json"
            path.write_text(json.dumps(profiles), encoding="utf-8")
            monkeypatch.setattr(settings, "tenant_query_profiles_path", str(path))
            await load_tenant_query_profiles()
            assert (await rest.get("/api/v1/databases", headers=member)).status_code == 403
            assert (await rest.get("/api/v1/settings", headers=headers[2])).status_code == 403
            probe = await rest.post("/api/v1/databases/test", headers=headers[2], json={})
            assert probe.status_code == 403
            assert (await rest.get(
                "/api/v1/schema/only-tenant-three", headers=headers[2],
            )).status_code == 404
            assert (await rest.get(
                "/api/v1/schema/only-tenant-three", headers=headers[3],
            )).status_code == 200
            cross_endpoint = await rest.put("/api/v1/settings/llm", headers=headers[2], json={
                "providers": {"shared-provider": {
                    "base_url": "https://tenant-3.example.test/v1",
                    "api_key": "fixture-key", "models": ["fixture-model"],
                }},
            })
            assert cross_endpoint.status_code == 400 and not intercepted
            blank = await rest.put("/api/v1/settings/llm", headers=headers[2], json={
                "providers": {"openai": {"models": ["host-model"]}},
            })
            assert blank.status_code == 200 and not blank.json()["runtime"]["available"]
            models = await rest.get("/api/v1/settings/available-models", headers=headers[2])
            assert models.json()["models"][0]["available"] is False
            unavailable = await rest.post("/api/v1/query", headers=headers[2], json=request)
            assert unavailable.status_code == 503
            assert unavailable.json()["detail"]["code"] == "LLM_CONFIG_UNAVAILABLE"
            assert not intercepted

            for tenant in (2, 3):
                endpoint_url = f"https://tenant-{tenant}.example.test/v1"
                key = f"key-fixture-{tenant}"
                save = await rest.put("/api/v1/settings/llm", headers=headers[tenant], json={
                    "default_model": f"model-{tenant}", "max_tokens": 320 + tenant,
                    "providers": {"shared-provider": {
                        "api_key": key, "base_url": endpoint_url, "models": [f"model-{tenant}"],
                    }},
                })
                assert save.status_code == 200, save.text
                assert save.json()["persistence"] == "persisted"
                assert save.json()["active_model"] == f"model-{tenant}"
                assert key not in save.text and "api_key_enc" not in save.text
                token = set_current_tenant_id(tenant)
                try:
                    stored = await store.get_setting("llm")
                    assert stored is not None, (
                        tenant,
                        await store._fetchall("SELECT tenant_id, section FROM user_settings"),
                    )
                    encrypted = stored["providers"]["shared-provider"]["api_key_enc"]
                    assert encrypted != key and decrypt_secret(encrypted) == key
                finally:
                    reset_current_tenant_id(token)

            api_settings._tenant_llm_runtimes.clear()
            first, second = await asyncio.gather(
                rest.post("/api/v1/query", headers=headers[2], json=request),
                rest.post("/api/v1/query/stream", headers=headers[3], json=request),
            )
            assert first.status_code == 200, first.text
            assert first.json()["execution"]["rows"] == [[2]]
            assert second.status_code == 200, second.text
            result = next(
                json.loads(message.split("\ndata: ", 1)[1])
                for message in second.text.split("\n\n") if message.startswith("event: done\n")
            )
            assert result["execution"]["rows"] == [[3]]
            assert set(intercepted) == {
                ("tenant-2.example.test", "Bearer key-fixture-2", "model-2", 322),
                ("tenant-3.example.test", "Bearer key-fixture-3", "model-3", 323),
            }
            call = {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {
                "name": "nl_query", "arguments": {"nl_text": "list order ids", "db_id": db_id},
            }}
            await exercise_tenant_mcp(headers, member, call)
            control_db = await store._fetchone("SELECT DATABASE() AS name")
            config = service.config(admin=True)
            config["db"] = control_db["name"]
            del config["database"]
            with monkeypatch.context() as patch:
                patch.setattr(MySQLStore, "_instance", None)
                patch.setattr(mysql_store, "DEFAULT_MYSQL_CONFIG", config)
                patch.setattr(ConnectionFactory, "_connections", {})
                patch.setattr(ConnectionFactory, "_infos", {})
                await exercise_tenant_mcp_sse(headers, call)
                assert MySQLStore._instance is None and not ConnectionFactory._connections
            attempts = len(intercepted)
            wrong_model = await rest.post(
                "/api/v1/query", headers=headers[2], json={**request, "model": "model-3"},
            )
            assert wrong_model.status_code == 422
            assert wrong_model.json()["detail"]["code"] == "LLM_MODEL_UNAVAILABLE"

            with monkeypatch.context() as patch:
                patch.setattr(api_settings, "_get_store", lambda: None)
                missing_store = await rest.post("/api/v1/query", headers=headers[3], json=request)
            assert missing_store.status_code == 503
            assert missing_store.json()["detail"]["code"] == "LLM_CONFIG_UNAVAILABLE"

            deleted = await rest.delete(
                "/api/v1/settings/llm/providers/shared-provider", headers=headers[2],
            )
            assert deleted.status_code == 200 and not deleted.json()["runtime"]["available"]
            no_provider = await rest.post("/api/v1/query", headers=headers[2], json=request)
            assert no_provider.status_code == 503 and len(intercepted) == attempts
            other = await rest.get("/api/v1/settings/llm", headers=headers[3])
            assert other.json()["runtime"]["active_model"] == "model-3"
            assert await store.get_setting("llm") == {"default_model": "default-fixture"}
            assert factory.current_llm_router() is default_router
        finally:
            await asyncio.gather(*(client.aclose() for client in unused_transport_clients))
            for tenant in (2, 3):
                token = set_current_tenant_id(tenant)
                try:
                    await ConnectionFactory.aclose(db_id)
                    await ConnectionFactory.aclose("only-tenant-three")
                finally:
                    reset_current_tenant_id(token)
            for target in target_schemas.values():
                await admin_sql(service, auth_schema, f"DROP DATABASE `{target}`")


async def test_concurrent_model_setting_edits_and_failed_preparation(service):
    async with tenant_store(service) as store:
        await store.init_tables()
        another_worker = MySQLStore(store._pool)
        token = set_current_tenant_id(2)
        try:
            await asyncio.gather(
                store.edit_setting("llm", lambda raw: ({**raw, "max_tokens": 123}, None)),
                another_worker.edit_setting(
                    "llm", lambda raw: ({**raw, "default_model": "a"}, None),
                ),
            )
            before = {"max_tokens": 123, "default_model": "a"}
            assert await store.get_setting("llm") == before

            def invalid(raw):
                raise ValueError("invalid prepared model")

            with pytest.raises(ValueError, match="invalid prepared model"):
                await another_worker.edit_setting("llm", invalid)
            assert await store.get_setting("llm") == before
        finally:
            reset_current_tenant_id(token)
        assert await store.get_setting("llm") is None
