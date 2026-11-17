"""Scoped read-only catalog discovery with real JWTs and connected SQLite."""

import hashlib
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from app.api import create_app, query
from app.auth.security import create_access_token
from app.config.settings import settings
from app.db.connections import ConnectionFactory
from tests.test_api.test_metric_query import body
from tests.test_api.test_metric_query import metric_env as metric_env

PARAMS = {"domain": "ecommerce", "database": "metric-db"}


async def test_discover_then_execute_is_one_versioned_contract(metric_env):
    client, model, path, _ = metric_env
    response = client.get("/api/v1/metrics", params=PARAMS)
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert data["version"] == "synthetic-ecommerce-v1"
    assert data["domain"] == "ecommerce" and data["database"] == "metric-db"
    revenue = next(m for m in data["metrics"] if m["name"] == "revenue")
    assert revenue == {
        "name": "revenue", "table": "orders", "column": "amount", "aggregation": "sum",
        "dimensions": ["region", "status"],
    }
    assert "unverified" in data["evidence"]
    assert query._SESSION_STORE == {} and model.calls == []
    result = client.post("/api/v1/query", json=body(
        metrics=[revenue["name"]], metric_catalog_version=data["version"], execute=True,
    )).json()
    assert result["status"] == "completed"
    assert result["plan_output"]["metric_catalog"]["sha256"] == data["sha256"]
    assert model.calls == []


async def test_catalog_is_configuration_not_schema_or_business_certification(metric_env):
    client, _, path, _ = metric_env
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    other = {"domain": "private-finance", "database": "private-target", "metrics": [
        {"name": "private_metric", "table": "secret", "column": "*", "aggregation": "count",
         "dimensions": []},
    ]}
    raw["bindings"].append(other)
    raw["bindings"][0]["metrics"][0]["column"] = "not_yet_verified"
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    response = client.get("/api/v1/metrics", params=PARAMS)
    assert response.status_code == 200
    assert "not_yet_verified" in response.text
    assert all(
        word not in response.text
        for word in ("private-finance", "private-target", "private_metric")
    )
    assert str(path) not in response.text
    assert "unverified" in response.json()["evidence"]


@pytest.mark.parametrize("params, status", [
    ({}, 422), ({"domain": "ecommerce"}, 422), ({"database": "metric-db"}, 422),
    ({**PARAMS, "domain": " "}, 400), ({**PARAMS, "database": "missing"}, 400),
    ({**PARAMS, "domain": "finance"}, 400), ({**PARAMS, "domain": "a" * 129}, 422),
])
async def test_discovery_requires_explicit_valid_scope(metric_env, params, status):
    client, model, *_ = metric_env
    assert client.get("/api/v1/metrics", params=params).status_code == status
    assert query._SESSION_STORE == {} and model.calls == []


async def test_disabled_invalid_disconnected_catalog_fails_closed(metric_env, monkeypatch):
    client, model, path, _ = metric_env
    path.write_text("secret-invalid-yaml: [", encoding="utf-8")
    response = client.get("/api/v1/metrics", params=PARAMS)
    assert response.status_code == 400
    assert "secret-invalid-yaml" not in response.text
    path.write_bytes(Path("tests/fixtures/metric_catalog.yml").read_bytes())
    await ConnectionFactory.aclose("metric-db")
    assert client.get("/api/v1/metrics", params=PARAMS).status_code == 400
    monkeypatch.setattr(settings, "metric_catalog_path", "")
    assert client.get("/api/v1/metrics", params=PARAMS).status_code == 400
    assert model.calls == []


def test_discovery_requires_authenticated_operator():
    client = TestClient(create_app())
    assert client.get("/api/v1/metrics", params=PARAMS).status_code == 401
    client.headers["Authorization"] = f"Bearer {create_access_token(99, 'outsider')}"
    assert client.get("/api/v1/metrics", params=PARAMS).status_code == 403
