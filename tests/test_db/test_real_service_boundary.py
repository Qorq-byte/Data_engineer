"""Offline authorization boundaries for fixture services and owned-container cleanup."""

import copy

import pytest

from scripts.run_real_db_tests import LABEL, validate_owned
from scripts.run_real_db_tests import test_environment as build_environment
from tests.real_db.support import configured_service


def test_real_services_require_explicit_opt_in(monkeypatch):
    monkeypatch.delenv("DE_REAL_DB_TESTS", raising=False)
    with pytest.raises(ValueError, match="opt-in"):
        configured_service("postgresql")


@pytest.mark.parametrize("value", [None, "5432", "3306", "-1", "65536", "not-a-port"])
def test_enabled_services_cannot_guess_host_ports(monkeypatch, value):
    monkeypatch.setenv("DE_REAL_DB_TESTS", "1")
    if value is None:
        monkeypatch.delenv("DE_REAL_PG_PORT", raising=False)
    else:
        monkeypatch.setenv("DE_REAL_PG_PORT", value)
    with pytest.raises(ValueError):
        configured_service("postgresql")


@pytest.mark.parametrize("kind,key", [("postgresql", "DE_REAL_PG_PORT"),
                                     ("mysql", "DE_REAL_MYSQL_PORT")])
def test_valid_test_service_is_fixed_to_loopback_fixture(monkeypatch, kind, key):
    monkeypatch.setenv("DE_REAL_DB_TESTS", "1")
    monkeypatch.setenv(key, "15439")
    service = configured_service(kind)
    assert service.config()["host"] == "127.0.0.1"
    assert service.config()["database"] == "de_stage40_fixture"
    assert service.config()["user"] == "stage40_reader"
    assert "password" not in repr(service)


@pytest.mark.parametrize("change", ["id", "token", "labels", "metadata", "short_id"])
def test_cleanup_refuses_any_unowned_container(change):
    identity, token = "a" * 64, "b" * 32
    metadata = {"Id": identity, "Config": {"Labels": {LABEL: token}}}
    invalid = copy.deepcopy(metadata)
    if change == "id":
        invalid["Id"] = "c" * 64
    elif change == "token":
        invalid["Config"]["Labels"][LABEL] = "c" * 32
    elif change == "labels":
        invalid["Config"]["Labels"] = None
    elif change == "metadata":
        invalid = None
    else:
        identity = "a" * 12
    with pytest.raises(ValueError, match="identity") if change == "short_id" else pytest.raises(
        (ValueError, RuntimeError)
    ):
        validate_owned(invalid, identity, token)


def test_cleanup_accepts_only_exact_owned_id_and_run_token():
    identity, token = "a" * 64, "b" * 32
    metadata = {"Id": identity, "Config": {"Labels": {LABEL: token}}}
    assert validate_owned(metadata, identity, token) is metadata


def test_child_environment_does_not_inherit_business_settings(monkeypatch):
    for key in ("DATABASE_URL", "MYSQL_HOST", "MYSQL_PASSWORD", "DEEPSEEK_API_KEY",
                "LLM_AUDIT_PATH", "PYTHONPATH"):
        monkeypatch.setenv(key, "PRIVATE_HOST_VALUE")
    result = build_environment({"postgresql": "15439", "mysql": "13439"})
    assert "PRIVATE_HOST_VALUE" not in result.values()
    assert "MYSQL_HOST" not in result and "DEEPSEEK_API_KEY" not in result
    assert result["LLM_MOCK_MODE"] == "1"
    assert result["DE_REAL_PG_PORT"] == "15439" and result["DE_REAL_MYSQL_PORT"] == "13439"
