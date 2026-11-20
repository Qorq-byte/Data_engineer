"""The workbench uses the same verified identity as protected REST routes."""

import pytest
from fastapi.testclient import TestClient

from app.api import create_app
from app.auth.security import create_access_token
from app.config.settings import settings


@pytest.mark.parametrize(
    ("user_id", "tenant_id", "shared_worker", "access", "route_status"),
    [
        (1, 1, False, "system", 200),
        (2, 2, False, "tenant", 200),
        (3, 2, False, "account", 403),
        (2, 2, True, "account", 403),
        (4, 3, False, "account", 403),
    ],
)
def test_workspace_access_matches_operator_gate(
    auth_token_states, monkeypatch, user_id, tenant_id, shared_worker, access, route_status,
):
    auth_token_states[user_id] = (tenant_id, 0)
    monkeypatch.setattr(settings, "admin_user_ids", [1, 4])
    monkeypatch.setattr(settings, "tenant_admin_user_ids", {2: [2], 3: [3]})
    monkeypatch.setattr(settings, "shared_worker_release", "fixture" if shared_worker else "")
    client = TestClient(create_app())
    endpoint = "/api/v1/auth/workspace"
    assert client.get(endpoint).status_code == 401
    expired = create_access_token(user_id, "fixture", tenant_id, token_version=1)
    assert client.get(endpoint, headers={"Authorization": f"Bearer {expired}"}).status_code == 401
    headers = {"Authorization": f"Bearer {create_access_token(user_id, 'fixture', tenant_id)}"}
    session = client.get(endpoint, headers=headers)
    assert session.headers["cache-control"] == "no-store"
    assert session.json() == {
        "id": user_id, "tenant_id": tenant_id, "username": "fixture", "access": access,
    }
    assert client.get("/api/v1/databases", headers=headers).status_code == route_status
