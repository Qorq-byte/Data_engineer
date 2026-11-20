"""Two real workers take tenant authorization and configuration from one release."""

import json

import httpx
import pytest

from app.auth.security import hash_password
from app.auth.tenant import tenant_scope
from app.config.settings import settings
from app.security.crypto import encrypt_secret
from app.storage.mysql_store import MySQLStore
from tests.fixtures.immutable_release import build_release
from tests.fixtures.shared_query_worker import SECRET
from tests.real_db.support import admin_connection, admin_sql
from tests.real_db.test_metric_mcp_network import network_client, tool
from tests.real_db.test_mysql_coordination import coordination as coordination
from tests.real_db.test_shared_query_runtime import body, launch_worker, worker_environment

pytestmark = [
    pytest.mark.real_database,
    pytest.mark.parametrize("service", ["mysql"], indirect=True),
]


async def test_immutable_tenants_queries_sessions_drift_and_restart(
    service, coordination, tmp_path, monkeypatch,
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

    profiles = {}
    sources = []
    async with admin_connection(service) as admin:
        try:
            for tenant in (2, 3):
                source = f"{database}_t{tenant}"
                await admin_sql(service, admin, f"CREATE DATABASE `{source}`")
                sources.append(source)
                await admin_sql(service, admin, f"""
                    CREATE TABLE `{source}`.orders (
                        id INT PRIMARY KEY, status VARCHAR(32) NOT NULL
                    )
                """)
                await admin_sql(
                    service, admin, f"INSERT INTO `{source}`.orders VALUES (%s, 'complete')",
                    (tenant * 100,),
                )
                await admin_sql(
                    service, admin, f"GRANT SELECT ON `{source}`.* TO 'stage40_reader'@'%'",
                )
                profiles[str(tenant)] = [{
                    "db_id": "fixture", "db_type": "mysql",
                    "config": {**service.config(), "database": source},
                }]
                if tenant == 3:
                    profiles[str(tenant)].append({
                        **profiles[str(tenant)][0], "db_id": "tenant-three-only",
                    })

            release = await build_release(
                config, tmp_path, with_metrics=True, tenant_profiles=profiles,
            )
            environment = {
                **worker_environment(), **release, "SHARED_WORKER_NAMESPACE": database,
                "TENANT_ADMIN_USER_IDS": '{"2":[2],"3":[3]}',
                "TENANT_LLM_ENDPOINTS": json.dumps(endpoints), "DE_SHARED_METRIC_MCP": "1",
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
                await verify_queries(client, first, second, launch, store, model_config, password)
        finally:
            for child in children:
                if child.returncode is None:
                    child.kill()
                    await child.wait()
            for source in sources:
                await admin_sql(service, admin, f"DROP DATABASE `{source}`")


async def verify_queries(client, first, second, launch, store, model_config, password):
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
    async with network_client(second[0], tokens[2]) as mcp:
        response = await tool(mcp, "nl_query", {
            "db_id": "fixture", "domain": "ecommerce", "nl_text": "find order ids",
        })
        assert response["rows"] == [[200]], response
        metric = await tool(mcp, "metric_query", {
            "db_id": "fixture", "domain": "ecommerce", "metrics": ["id_sum"],
            "metric_catalog_version": "shared-fixture-v1", "execute": True,
        })
        assert metric["rows"] == [["200"]] and metric["llm_calls"] == 0, metric

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
    with pytest.raises(AssertionError, match="Immutable worker configuration mismatch"):
        await launch("different-admins", TENANT_ADMIN_USER_IDS='{"2":[2,4],"3":[3]}')
    standalone = await launch("independent-mcp", DE_SHARED_STANDALONE_MCP="1")
    async with network_client(standalone[0], tokens[3]) as mcp:
        response = await tool(mcp, "nl_query", {
            "db_id": "fixture", "domain": "ecommerce", "nl_text": "find order ids",
        })
        assert response["rows"] == [[300]], response

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
