"""Database settings cannot claim unsupported connectors or unwired writes."""

import copy

import pytest
from fastapi.testclient import TestClient

from app.api import create_app
from app.api import settings as api_settings
from app.auth.security import create_access_token
from app.db.connections import ConnectionFactory, DatabaseType
from tests.api_client import authenticated_client


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(api_settings, "_get_store", lambda: None)
    monkeypatch.setattr(
        api_settings, "_settings_store", copy.deepcopy(api_settings._DEFAULT_SETTINGS)
    )
    return authenticated_client(create_app())


def test_unwired_database_write_rejected(client):
    before = copy.deepcopy(api_settings._settings_store)
    response = client.put("/api/v1/settings/database", json={"connection_pool_size": 19})
    assert response.status_code == 409
    assert api_settings._settings_store == before


def test_real_connectors_not_legacy_dialect_switches(client):
    response = client.get("/api/v1/settings/database")
    assert response.status_code == 200
    data = response.json()
    assert data["mode"] == "read_only"
    implemented = {row["type"] for row in data["connectors"] if row["implemented"]}
    assert implemented == {"sqlite", "duckdb", "postgresql", "mysql"}


@pytest.mark.parametrize(
    "payload", [{}, None, {"unknown": 1}, {"dialects_enabled": {"sqlite": False}}]
)
def test_all_legacy_writes_rejected_without_side_effects(client, payload):
    before = copy.deepcopy(api_settings._settings_store)
    response = client.put("/api/v1/settings/database", json=payload)
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "DATABASE_SETTINGS_READ_ONLY"
    assert api_settings._settings_store == before


@pytest.mark.parametrize(
    "db_type",
    [
        kind
        for kind in DatabaseType
        if kind
        not in {
            DatabaseType.SQLITE,
            DatabaseType.DUCKDB,
            DatabaseType.MYSQL,
            DatabaseType.POSTGRESQL,
        }
    ],
)
async def test_unimplemented_capabilities_match_real_dispatch(client, db_type):
    row = next(
        row
        for row in ConnectionFactory.describe_capabilities()["connectors"]
        if row["type"] == db_type
    )
    assert row["implemented"] is False
    assert row["driver_module"] is None
    assert row["module_discoverable"] is None
    assert row["management"] == "not_implemented"
    with pytest.raises(NotImplementedError):
        await ConnectionFactory._connect(db_type, {})


@pytest.fixture
async def isolated_registry(monkeypatch):
    for name in ("_connections", "_infos", "_offload_connections", "_mysql_pools"):
        monkeypatch.setattr(ConnectionFactory, name, {})
    yield
    await ConnectionFactory.aclose_all()


@pytest.mark.parametrize("db_type", [DatabaseType.SQLITE, DatabaseType.DUCKDB])
async def test_real_native_lifecycle_reported_without_identifiers(
    client, isolated_registry, db_type
):
    def registered():
        response = client.get("/api/v1/settings/database")
        assert response.status_code == 200
        assert "do-not-report" not in response.text
        return next(row for row in response.json()["connectors"] if row["type"] == db_type)

    assert registered()["registered_connections"] == 0
    connection = await ConnectionFactory.create(
        db_type,
        {"path": ":memory:", "host": "do-not-report-host", "alias": "do-not-report-alias"},
        "do-not-report-id",
    )
    assert connection.execute("SELECT 1").fetchone()[0] == 1
    row = registered()
    assert row["implemented"] is True
    assert row["module_discoverable"] is True
    assert row["management"] == "native_single_connection"
    assert row["registered_connections"] == 1
    await ConnectionFactory.aclose("do-not-report-id")
    assert registered()["registered_connections"] == 0


@pytest.mark.parametrize("failure", [False, True])
def test_driver_missing_and_unknown_not_claimed_usable(client, monkeypatch, failure):
    def discovery(module):
        if failure:
            raise ValueError("private-discovery-detail")
        return None

    monkeypatch.setattr("importlib.util.find_spec", discovery)
    response = client.get("/api/v1/settings/database")
    assert response.status_code == 200
    for row in response.json()["connectors"]:
        if row["implemented"]:
            assert row["module_discoverable"] is (None if failure else False)
    assert "private-discovery-detail" not in response.text


def test_snapshot_failure_unknown_and_sanitized(client, monkeypatch):
    def failed(cls):
        raise RuntimeError("private-connection-details")

    monkeypatch.setattr(ConnectionFactory, "describe_capabilities", classmethod(failed))
    response = client.get("/api/v1/settings/database")
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "DATABASE_CAPABILITIES_UNAVAILABLE"
    assert "connectors" not in response.json()
    assert "private-connection-details" not in response.text


@pytest.mark.parametrize("method", ["get", "put"])
@pytest.mark.parametrize("user_id,expected", [(None, 401), (999999, 403)])
def test_operator_boundary(client, method, user_id, expected):
    headers = (
        {}
        if user_id is None
        else {"Authorization": f"Bearer {create_access_token(user_id, 'non-operator')}"}
    )
    response = getattr(TestClient(create_app()), method)(
        "/api/v1/settings/database", headers=headers
    )
    assert response.status_code == expected


def test_aggregate_cannot_reintroduce_stored_values(client):
    api_settings._settings_store["database"] = {
        "connection_pool_size": 49,
        "private": "legacy-only",
    }
    view = client.get("/api/v1/settings").json()["settings"]["database"]
    assert view == client.get("/api/v1/settings/database").json()
    assert view["connection_pool_size"] is None
    assert view["connection_timeout_s"] is None
    assert "legacy-only" not in str(view)


def test_read_and_rejected_write_do_not_access_storage_or_connect(client, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Must not touch storage or connect")

    monkeypatch.setattr(api_settings, "_get_store", forbidden)
    monkeypatch.setattr(ConnectionFactory, "_connect", forbidden)
    assert client.get("/api/v1/settings/database").status_code == 200
    assert (
        client.put("/api/v1/settings/database", json={"connection_pool_size": 2}).status_code == 409
    )
