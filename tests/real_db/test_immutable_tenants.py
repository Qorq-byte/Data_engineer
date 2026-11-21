"""Two real workers take tenant authorization and configuration from one release."""

import json
import os
from contextlib import AsyncExitStack, asynccontextmanager

import httpx
import pytest

from app.auth.security import hash_password
from app.auth.tenant import tenant_scope
from app.config.settings import settings
from app.evaluation.artifacts import write_atomic
from app.evaluation.capacity_acceptance import (
    MULTI_TENANT_PROFILE,
    load_capacity_plan,
    run_capacity_plan,
)
from app.security.crypto import encrypt_secret
from app.storage.mysql_store import MySQLStore
from tests.fixtures.immutable_release import build_release
from tests.fixtures.shared_query_worker import SECRET
from tests.real_db.support import READER_PASSWORD, admin_connection, admin_sql, configured_service
from tests.real_db.test_metric_mcp_network import network_client, tool
from tests.real_db.test_mysql_coordination import coordination as coordination
from tests.real_db.test_shared_metric_catalog import metric_body
from tests.real_db.test_shared_query_runtime import body, launch_worker, worker_environment

pytestmark = [
    pytest.mark.real_database,
    pytest.mark.parametrize("service", ["mysql"], indirect=True),
]


@asynccontextmanager
async def tenant_targets(service, database, target_kind, record_testsuite_property):
    target = service if target_kind == "mysql" else configured_service("postgresql")
    profiles = {}
    async with AsyncExitStack() as stack:
        admin = await stack.enter_async_context(admin_connection(target))
        if target.kind == "postgresql":
            record_testsuite_property("server_immutable_tenant_postgresql", await admin.fetchval(
                "SHOW server_version",
            ))
        for tenant in (2, 3):
            source = f"{database}_t{tenant}"
            config = {**target.config(), "database": source}
            if target.kind == "postgresql":
                role = f"{source}_ro"
                await admin.execute(f"CREATE ROLE {role} LOGIN PASSWORD '{READER_PASSWORD}'")
                stack.push_async_callback(admin.execute, f"DROP ROLE {role}")
                await admin.execute(f"CREATE DATABASE {source}")
                stack.push_async_callback(admin.execute, f"DROP DATABASE {source}")
                await admin.execute(f"REVOKE ALL ON DATABASE {source} FROM PUBLIC")
                await admin.execute(f"GRANT CONNECT ON DATABASE {source} TO {role}")
                async with admin_connection(target, database=source) as setup:
                    await setup.execute("""
                        CREATE TABLE orders (id INT PRIMARY KEY, status TEXT, booked_on DATE,
                                             branch_id INT);
                        CREATE TABLE branches (id INT PRIMARY KEY, label TEXT);
                        INSERT INTO branches VALUES (1, 'North');
                    """)
                    await setup.execute(
                        "INSERT INTO orders VALUES ($1, 'complete', DATE '2026-01-08', 1)",
                        tenant * 100,
                    )
                    await setup.execute(f"GRANT USAGE ON SCHEMA public TO {role}")
                    await setup.execute(f"GRANT SELECT ON orders, branches TO {role}")
                config["user"] = role
            else:
                await admin_sql(target, admin, f"CREATE DATABASE `{source}`")
                stack.push_async_callback(admin_sql, target, admin, f"DROP DATABASE `{source}`")
                await admin_sql(target, admin, f"""
                    CREATE TABLE `{source}`.orders (
                        id INT PRIMARY KEY, status VARCHAR(32) NOT NULL, booked_on DATE,
                        branch_id INT
                    )
                """)
                await admin_sql(
                    target, admin, (
                        f"INSERT INTO `{source}`.orders VALUES (%s, 'complete', '2026-01-08', 1)"
                    ),
                    (tenant * 100,),
                )
                await admin_sql(target, admin, f"""
                    CREATE TABLE `{source}`.branches (id INT PRIMARY KEY, label VARCHAR(32))
                """)
                await admin_sql(
                    target, admin, f"INSERT INTO `{source}`.branches VALUES (1, 'North')",
                )
                await admin_sql(
                    target, admin, f"GRANT SELECT ON `{source}`.* TO 'stage40_reader'@'%'",
                )
            profile = {"db_id": "fixture", "db_type": target.kind, "config": config}
            profiles[str(tenant)] = [profile]
            if tenant == 3:
                profiles[str(tenant)].append({**profile, "db_id": "tenant-three-only"})
        yield profiles


@pytest.mark.parametrize("target_kind", ["mysql", "postgresql"])
async def test_immutable_tenants_queries_sessions_drift_and_restart(
    service, coordination, tmp_path, monkeypatch, target_kind, record_testsuite_property,
):
    database, pool, config, children = coordination
    monkeypatch.setattr(settings, "jwt_secret", SECRET)
    store = MySQLStore(pool)
    await store.init_tables()
    await store.seed_defaults()
    model_config = {}
    endpoints = {}
    for tenant in (1, 2, 3):
        model = f"fixture-model-{tenant}"
        endpoint = f"https://tenant-{tenant}.example.test/v1"
        model_config[tenant] = {
            "default_model": model, "max_tokens": 1024,
            "providers": {"fixture": {
                "enabled": True, "models": [model], "base_url": endpoint,
                "api_key_enc": encrypt_secret("synthetic-provider-key"),
                "api_key_configured": True,
            }},
        }
        with tenant_scope(tenant):
            await store.save_setting("llm", model_config[tenant])
            if tenant > 1:
                endpoints[str(tenant)] = [endpoint]
                await store.save_domain({
                    "name": "ecommerce", "label_en": f"Domain {tenant}",
                    "keywords": [f"tenantkeyword{tenant}"],
                })
                await store.save_glossary_term("ecommerce", {"term": f"tenantterm{tenant}"})
                await store.save_rule("ecommerce", {
                    "id": "fixture", "description": f"Rule {tenant}",
                })

    async with tenant_targets(
        service, database, target_kind, record_testsuite_property,
    ) as profiles:
        try:
            subagents = tmp_path / "subagents.json"
            write_atomic(subagents, {
                "profile": "shared-subagents-v1",
                "subagents": [
                    {"name": name, "tenant_id": tenant, "domain": "ecommerce",
                     "database_ids": ["fixture"], "capabilities": ["metric_query"],
                     "allowed_users": [str(tenant)], "enable_execution": True,
                     "enable_multi_candidate": False}
                    for tenant, name in ((2, "analyst"), (3, "analyst"), (3, "private-three"))
                ],
            })
            release = await build_release(
                config, tmp_path, with_metrics=True, tenant_profiles=profiles,
                subagent_catalog=subagents,
            )
            record_testsuite_property(f"immutable_tenant_release_{target_kind}", database)
            environment = {
                **worker_environment(), **release, "SHARED_WORKER_NAMESPACE": database,
                "TENANT_ADMIN_USER_IDS": '{"2":[2],"3":[3]}',
                "TENANT_LLM_ENDPOINTS": json.dumps(endpoints), "DE_SHARED_METRIC_MCP": "1",
                "DE_SHARED_STATEFUL_MCP": "1",
                "MAX_CONCURRENT_QUERIES": "2", "LLM_MAX_CONCURRENCY": "1",
            }

            async def launch(name, **overrides):
                return await launch_worker(
                    tmp_path / name, environment={**environment, **overrides},
                    port=config["port"], database=database, children=children,
                )

            first, second = await launch("first"), await launch("second")
            password = "fixture-password"
            secret = hash_password(password)
            for user, tenant in ((1, 1), (2, 2), (3, 3), (4, 2)):
                await store._execute(
                    "INSERT INTO users (id,tenant_id,username,email,password_hash) "
                    "VALUES (%s,%s,%s,%s,%s)",
                    (user, tenant, f"operator-{user}", f"operator-{user}@example.test", secret),
                )

            async with httpx.AsyncClient(trust_env=False, timeout=30) as client:
                await verify_queries(
                    client, first, second, launch, store, model_config, password, target_kind,
                    subagents,
                )
        finally:
            for child in children:
                if child.returncode is None:
                    child.kill()
                    await child.wait()


async def verify_queries(
    client, first, second, launch, store, model_config, password, target_kind, subagents,
):
    headers = {}
    tokens = {}
    for user in (1, 2, 3, 4):
        signed = await client.post(first[0] + "/api/v1/auth/login", json={
            "email": f"operator-{user}@example.test", "password": password,
        })
        assert signed.status_code == 200, signed.text
        tokens[user] = signed.json()["access_token"]
        headers[user] = {"Authorization": f"Bearer {tokens[user]}"}
    assert (await client.get(first[0] + "/health/ready")).status_code == 200
    assert (await client.get(second[0] + "/health/ready")).status_code == 200
    assert (await client.get(
        first[0] + "/api/v1/databases", headers=headers[4],
    )).status_code == 403
    scope = await client.get(first[0] + "/api/v1/auth/workspace", headers=headers[2])
    assert scope.json()["access"] == "tenant" and scope.json()["configuration_read_only"] is True
    hidden = await client.get(first[0] + "/api/v1/schema/tenant-three-only", headers=headers[2])
    assert hidden.status_code == 404
    visible = await client.get(second[0] + "/api/v1/schema/tenant-three-only", headers=headers[3])
    assert visible.status_code == 200
    models = await client.get(second[0] + "/api/v1/settings/llm", headers=headers[3])
    assert models.json()["runtime"]["active_model"] == "fixture-model-3"
    detected = await client.post(first[0] + "/api/v1/domains/detect", headers=headers[2],
                                 json={"query_text": "tenantkeyword2 tenantterm2"})
    assert detected.json()["recommendation"] == "ecommerce"
    rules = await client.get(second[0] + "/api/v1/domains/ECOMMERCE/rules", headers=headers[3])
    assert [row["description"] for row in rules.json()["rules"]] == ["Rule 3"]
    for method, path in (("PUT", "/settings/llm"), ("DELETE", "/domains/ecommerce")):
        denied = await client.request(method, first[0] + "/api/v1" + path, headers=headers[2],
                                      json={})
        assert denied.status_code == 409, denied.text
        assert denied.json()["detail"]["code"] == "OFFLINE_MAINTENANCE_REQUIRED"

    request = {**body("same-session"), "domain": "ECOMMERCE"}
    first_query = await client.post(first[0] + "/api/v1/query", headers=headers[2],
                                    json={**request, "nl_text": "find tenant two order ids"})
    assert first_query.status_code == 200, first_query.text
    assert first_query.json()["execution"]["rows"] == [[200]]
    second_query = await client.post(second[0] + "/api/v1/query/stream", headers=headers[3],
                                     json={**request, "nl_text": "find tenant three order ids"})
    assert second_query.status_code == 200, second_query.text
    done = next(
        json.loads(block.split("data: ", 1)[1])
        for block in second_query.text.split("\n\n") if block.startswith("event: done\n")
    )
    assert done["execution"]["rows"] == [[300]]
    denied_model = await client.post(first[0] + "/api/v1/query", headers=headers[2],
                                     json={**request, "model": "fixture-model-3"})
    assert denied_model.status_code == 422
    for user, worker, path in ((2, first, "/query"), (3, second, "/query/stream")):
        catalog = await client.get(worker[0] + "/api/v1/metrics", headers=headers[user],
                                   params={"database": "fixture", "domain": "ecommerce"})
        assert catalog.status_code == 200, catalog.text
        assert {metric["name"] for metric in catalog.json()["metrics"]} == {"order_count", "id_sum"}
        response = await client.post(
            worker[0] + "/api/v1" + path, headers=headers[user], json=metric_body(),
        )
        assert response.status_code == 200, response.text
        result = response.json() if path == "/query" else next(
            json.loads(block.split("data: ", 1)[1])
            for block in response.text.split("\n\n") if block.startswith("event: done\n")
        )
        assert result["status"] == "completed", result
        total = str(user * 100) if target_kind == "mysql" else user * 100
        assert result["execution"]["rows"] == [[1, total]]
        assert result["llm_calls"] == 0 and result["validation"]["passed"] is True
        assert result["plan_output"]["metric_catalog"]["sha256"] == catalog.json()["sha256"]
    hidden_catalog = await client.get(
        first[0] + "/api/v1/metrics", headers=headers[2],
        params={"database": "tenant-three-only", "domain": "ecommerce"},
    )
    assert hidden_catalog.status_code == 400, hidden_catalog.text
    assert hidden_catalog.json()["detail"]["code"] == "INVALID_REQUEST"
    async with network_client(second[0], tokens[2]) as mcp:
        response = await tool(mcp, "nl_query", {
            "db_id": "fixture", "domain": "ecommerce", "nl_text": "find order ids",
        })
        assert response["rows"] == [[200]], response
        metric = await tool(mcp, "metric_query", {
            "db_id": "fixture", "domain": "ecommerce", "metrics": ["id_sum"],
            "metric_catalog_version": "shared-fixture-v1", "execute": True,
        })
        expected_sum = "200" if target_kind == "mysql" else 200
        assert metric["status"] == "completed", metric.get("errors", metric)
        assert metric["rows"] == [[expected_sum]] and metric["llm_calls"] == 0, metric

    await verify_capacity(first[0], first[1].parent, headers)

    first[2].kill()
    await first[2].wait()
    replacement = await launch("replacement")
    for user, label, other in ((2, "two", second), (3, "three", replacement)):
        response = await client.get(
            other[0] + "/api/v1/sessions/same-session", headers=headers[user],
        )
        turns = response.json()["session"]["turns"]
        assert [turn["nl_input"] for turn in turns] == [f"find tenant {label} order ids"]
        other_session = await client.get(
            other[0] + "/api/v1/sessions/same-session", headers=headers[1],
        )
        assert other_session.status_code == 404
    assert (await client.get(replacement[0] + "/health/ready")).status_code == 200
    second[2].kill()
    await second[2].wait()
    second = await launch("replacement-second")
    restored = await client.get(
        second[0] + "/api/v1/sessions/same-session", headers=headers[2],
    )
    assert restored.json()["session"]["turns"][0]["nl_input"] == "find tenant two order ids"
    with pytest.raises(AssertionError, match="Immutable worker configuration mismatch"):
        await launch("different-admins", TENANT_ADMIN_USER_IDS='{"2":[2,4],"3":[3]}')
    standalone = await launch("independent-mcp", DE_SHARED_STANDALONE_MCP="1")
    async with network_client(standalone[0], tokens[3]) as mcp:
        response = await tool(mcp, "nl_query", {
            "db_id": "fixture", "domain": "ecommerce", "nl_text": "find order ids",
        })
        assert response["rows"] == [[300]], response
    await verify_subagent_release(
        client, replacement, second, standalone, headers, tokens, target_kind, subagents,
    )

    for source in ("domain", "llm"):
        with tenant_scope(2):
            if source == "domain":
                await store.save_rule("ecommerce", {"id": "fixture", "description": "drift"})
            else:
                await store.save_setting("llm", {**model_config[2], "max_tokens": 2048})
        for url, directory, _ in (replacement, second):
            count_file = directory / "model-calls.txt"
            before = count_file.read_bytes() if count_file.exists() else None
            denied = await client.post(url + "/api/v1/query", headers=headers[3], json=request)
            assert denied.status_code == 503, denied.text
            assert denied.json()["detail"]["code"] == "SHARED_STATE_UNAVAILABLE"
            assert (count_file.read_bytes() if count_file.exists() else None) == before
            read = await client.get(url + "/api/v1/domains", headers=headers[2])
            assert read.status_code == 503
            assert (await client.get(url + "/health/ready")).status_code == 503
        with tenant_scope(2):
            if source == "domain":
                await store.save_rule("ecommerce", {"id": "fixture", "description": "Rule 2"})
            else:
                await store.save_setting("llm", model_config[2])
        assert (await client.get(replacement[0] + "/health/ready")).status_code == 200

    await verify_mcp_identity(client, replacement[0], headers, store)


async def verify_subagent_release(
    client, first, second, standalone, headers, tokens, target_kind, catalog,
):
    path = "/api/v1/subagents"
    values = {"nl_text": "Check tenant orders", "execute": True, "metrics": ["id_sum"],
              "metric_catalog_version": "shared-fixture-v1", "metric_grain": "week",
              "metric_dimensions": ["branch"]}
    for user, worker in ((2, first), (3, second)):
        listing = await client.get(worker[0] + path, headers=headers[user])
        names = [entry["name"] for entry in listing.json()["subagents"]]
        assert names == (["analyst"] if user == 2 else ["analyst", "private-three"])
        result = await client.post(worker[0] + path + "/analyst/query", headers=headers[user],
                                   json=values)
        assert result.status_code == 200, result.text
        data = result.json()["data"]
        total = str(user * 100) if target_kind == "mysql" else user * 100
        assert data["execution"]["rows"] == [["2026-01-05", "North", total]]
        assert data["llm_calls"] == 0
    hidden = await client.post(first[0] + path + "/private-three/query", headers=headers[2],
                               json=values)
    denied = await client.post(first[0] + path + "/analyst/query", headers=headers[4], json=values)
    assert hidden.status_code == 404 and denied.status_code == 403
    async with network_client(standalone[0], tokens[3]) as mcp:
        response = await tool(mcp, "subagent_query", {"name": "analyst", **values})
        expected = "300" if target_kind == "mysql" else 300
        assert response["data"]["execution"]["rows"] == [
            ["2026-01-05", "North", expected],
        ], response

    original = catalog.read_bytes()
    stamp = catalog.stat()
    altered = original.replace(b'"analyst"', b'"altered"', 1)
    assert altered != original and len(altered) == len(original)
    try:
        catalog.write_bytes(altered)
        os.utime(catalog, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
        for worker in (first, second):
            readiness = await client.get(worker[0] + "/health/ready")
            query = await client.post(worker[0] + path + "/analyst/query", headers=headers[2],
                                      json=values)
            assert readiness.status_code == query.status_code == 503
            assert query.json()["detail"]["code"] == "SHARED_STATE_UNAVAILABLE"
    finally:
        catalog.write_bytes(original)
        os.utime(catalog, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))


async def verify_capacity(url, directory, headers):
    manifest = directory / "multitenant-capacity.json"
    write_atomic(manifest, {
        "profile": MULTI_TENANT_PROFILE,
        "tenant_ids": [2, 3],
        "workload": [
            {"id": protocol, "tenant_id": tenant, "transport": protocol, "database": "fixture",
             "domain": "ecommerce", "nl_text": "find order ids"}
            for tenant, protocol in ((2, "json"), (3, "sse"))
        ],
        "phases": [
            {
                "id": name, "requests": 2, "concurrency": concurrency, "timeout_seconds": 20,
                "slo": {
                    "min_success_rate": minimum, "max_success_p95_ms": 20000,
                    "max_sse_first_event_p95_ms": None,
                    "min_success_rate_by_tenant": {"2": 1, "3": 1} if concurrency == 1 else {},
                },
            }
            for name, concurrency, minimum in (("peak", 2, 0.5), ("recovery", 1, 1))
        ],
    })
    async with AsyncExitStack() as stack:
        clients = {
            tenant: await stack.enter_async_context(httpx.AsyncClient(
                base_url=url, headers=headers[tenant], trust_env=False, timeout=None,
            ))
            for tenant in (2, 3)
        }
        report = await run_capacity_plan(clients, load_capacity_plan(manifest))
    assert report["passed"] is True, report
    peak, recovery = report["phases"]
    assert peak["by_tenant"]["2"]["requests"] == peak["by_tenant"]["3"]["requests"] == 1
    assert recovery["tenant_slo_checks"] == {"2": True, "3": True}
    assert recovery["by_transport"]["sse"]["successful"] == 1


async def verify_mcp_identity(client, url, headers, store):
    mcp_url = url + "/metric-mcp/"
    accept = {"Accept": "application/json, text/event-stream"}
    initialized = await client.post(mcp_url, headers={**accept, **headers[2]}, json={
        "jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": "2025-06-18", "capabilities": {},
            "clientInfo": {"name": "tenant-fixture", "version": "1"},
        },
    })
    assert initialized.status_code == 200, initialized.text
    session = {**accept, "Mcp-Session-Id": initialized.headers["mcp-session-id"]}
    request = {"jsonrpc": "2.0", "id": 2, "method": "resources/list"}
    response = await client.post(mcp_url, headers={**session, **headers[3]}, json=request)
    assert response.status_code == 404, response.text

    await store._execute("UPDATE users SET token_version=token_version+1 WHERE id=2")
    denied = await client.post(mcp_url, headers={**session, **headers[2]}, json=request)
    assert denied.status_code == 401, denied.text
    denied = await client.get(url + "/api/v1/databases", headers=headers[2])
    assert denied.status_code == 401, denied.text
