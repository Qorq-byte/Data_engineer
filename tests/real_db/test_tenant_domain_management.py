"""Exercise scoped domain administration with two authenticated MySQL tenants."""

import asyncio
import json

import httpx
import pytest

from app.api import create_app, domains, tenant_domains
from app.auth.security import hash_password
from app.auth.tenant import reset_current_tenant_id, set_current_tenant_id
from app.config.settings import settings
from app.knowledge.domain_manager import DomainManager
from app.mcp.fastmcp_server import create_nl2sql_server
from app.storage.mysql_store import MySQLStore
from app.workflow.query_domain import resolve_query_domain
from tests.real_db.support import admin_sql
from tests.real_db.test_tenant_auth_mysql import PASSWORD
from tests.real_db.test_tenant_auth_mysql import auth_schema as auth_schema
from tests.real_db.test_tenant_mysql_migration import tenant_store

pytestmark = [
    pytest.mark.real_database,
    pytest.mark.parametrize("service", ["mysql"], indirect=True),
]


async def test_tenant_domain_management_matches_query_catalog(
    service, auth_schema, monkeypatch, tmp_path,
):
    from app.auth import database

    await database.init_auth_db()
    password = hash_password(PASSWORD)
    for tenant_id in (2, 3):
        await admin_sql(
            service, auth_schema,
            "INSERT INTO users(tenant_id,username,email,password_hash) VALUES (%s,%s,%s,%s)",
            (tenant_id, f"tenant-{tenant_id}", f"tenant-{tenant_id}@example.test", password),
        )

    monkeypatch.setattr(settings, "tenant_admin_user_ids", {})
    app = create_app()
    name = "ecommerce"
    base = "/api/v1/domains"
    target = f"{base}/{name}"
    yaml = tmp_path / f"{name}.yml"
    legacy_yaml = b"name: ecommerce\nlabel: {en: Legacy catalog}\nkeywords: [legacy]\n"
    yaml.write_bytes(legacy_yaml)
    monkeypatch.setattr(domains, "DOMAINS_DIR", tmp_path)
    monkeypatch.setattr(domains, "_domain_manager", DomainManager(str(tmp_path)))
    async with (
        tenant_store(service) as store,
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://fixture",
        ) as http,
    ):
        await store.init_tables()
        monkeypatch.setattr(MySQLStore, "_instance", store)
        auth = []
        admins = {}
        for tenant_id in (2, 3):
            login = await http.post("/api/v1/auth/login", json={
                "email": f"tenant-{tenant_id}@example.test", "password": PASSWORD,
            })
            assert login.status_code == 200
            auth.append({"Authorization": "Bearer " + login.json()["access_token"]})
            admins[tenant_id] = [login.json()["user"]["id"]]
        first, other = auth
        assert (await http.get(base, headers=first)).status_code == 403
        monkeypatch.setattr(settings, "tenant_admin_user_ids", admins)
        assert (await http.get(base, headers=first)).json() == {
            "domains": [], "active": None, "total": 0,
        }

        for headers, label in ((first, "retail"), (other, "wholesale")):
            response = await http.post(base, headers=headers, json={
                "name": name, "label_en": label, "keywords": [label],
                "databases": [{"db_id": label, "tables": ["orders"]}],
            })
            assert response.status_code == 201, response.text
        assert (await http.post(base, headers=first, json={"name": name})).status_code == 409
        private = await http.post(base, headers=first, json={"name": "private_domain"})
        assert private.status_code == 201
        assert (await http.get(f"{base}/private_domain/rules", headers=other)).status_code == 404
        assert (await http.delete(f"{base}/private_domain", headers=other)).status_code == 404

        def term(identifier, **changes):
            return {
                "term": identifier, "expression": "orders.total", "table": "orders",
                "condition": "total IS NOT NULL", "precision": 2, "tags": ["derived"],
                **changes,
            }

        glossary = target + "/glossary"
        rules = target + "/rules"
        duplicates = await asyncio.gather(*(
            http.post(glossary, headers=first, json=term("revenue")) for _ in range(2)
        ))
        assert sorted(result.status_code for result in duplicates) == [201, 409]
        assert (await http.post(glossary, headers=other, json=term("revenue"))).status_code == 201
        assert (await http.post(glossary, headers=first, json=term("taken"))).status_code == 201
        assert (await http.put(glossary + "/revenue", headers=first,
                               json=term("taken"))).status_code == 409
        renamed = await http.put(glossary + "/revenue", headers=first, json=term("net_revenue"))
        assert renamed.status_code == 200, renamed.text
        assert (await http.delete(glossary + "/taken", headers=first)).status_code == 200
        terms = (await http.get(glossary, headers=first)).json()["terms"]
        assert [entry["term"] for entry in terms] == ["net_revenue"]
        assert terms[0]["mapping"] == {
            "expression": "orders.total", "type": "derived_column", "table": "orders",
            "condition": "total IS NOT NULL", "precision": 2,
        }

        for headers in (first, other):
            assert (await http.post(rules, headers=headers, json={"id": "same"})).status_code == 201
        assert (await http.post(rules, headers=first, json={"id": "taken"})).status_code == 201
        collision = await http.put(rules + "/same", headers=first, json={"id": "taken"})
        assert collision.status_code == 409
        result = await http.put(rules + "/same", headers=first, json={
            "id": "reviewed", "description": "New scoped guidance",
            "enforce": ["require_where=true"],
        })
        assert result.status_code == 200, result.text
        assert (await http.delete(rules + "/taken", headers=first)).status_code == 200
        current_rules = (await http.get(rules, headers=first)).json()["rules"]
        assert [entry["id"] for entry in current_rules] == ["reviewed"]

        # Independent patches retain unspecified fields even when committed concurrently.
        updates = await asyncio.gather(
            http.put(target, headers=first, json={"label_en": "retail updated"}),
            http.put(target, headers=first, json={"currency": "EUR"}),
        )
        assert all(result.status_code == 200 for result in updates)
        catalog = (await http.get(base, headers=first)).json()
        retail = next(entry for entry in catalog["domains"] if entry["name"] == name)
        assert retail["label"]["en"] == "retail updated" and retail["currency"] == "EUR"
        assert retail["glossary_count"] == retail["rules_count"] == 1
        detections = await http.post(base + "/detect", headers=first, json={
            "query_text": "retail net_revenue",
        })
        assert detections.json()["recommendation"] == name
        detections = await http.post(base + "/detect", headers=other, json={
            "query_text": "retail net_revenue",
        })
        assert detections.json()["recommendation"] is None

        token = set_current_tenant_id(2)
        try:
            resolved = await resolve_query_domain(name)
            assert resolved.databases == [{"db_id": "retail", "tables": ["orders"]}]
            assert resolved.glossary[0].mapping.condition == "total IS NOT NULL"
            assert resolved.rules[0].description == "New scoped guidance"
            resource = await create_nl2sql_server().read_resource("nlsql://domains/")
            exposed = json.loads(resource.contents[0].content)
            assert {entry["name"] for entry in exposed["domains"]} == {name, "private_domain"}
            assert "path" not in exposed["domains"][0]
        finally:
            reset_current_tenant_id(token)

        assert (await http.delete(target, headers=first)).status_code == 200
        assert (await http.get(glossary, headers=first)).status_code == 404
        assert (await http.delete(target, headers=first)).status_code == 404
        assert (await http.get(glossary, headers=other)).json()["terms"][0]["term"] == "revenue"
        assert (await http.get(rules, headers=other)).json()["rules"][0]["id"] == "same"
        assert yaml.read_bytes() == legacy_yaml
        token = set_current_tenant_id(2)
        try:
            assert not await store.get_glossary_terms(name)
            assert not await store.get_rules(name)
        finally:
            reset_current_tenant_id(token)

        with monkeypatch.context() as patch:
            patch.setattr(tenant_domains, "get_store", lambda: None)
            assert (await http.get(base, headers=first)).status_code == 503
            assert (await http.post(base, headers=first, json={"name": name})).status_code == 503
