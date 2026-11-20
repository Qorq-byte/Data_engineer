"""Tenant identity propagation tests for the authentication boundary."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import jwt
import pytest

from app.auth.security import create_access_token, decode_access_token
from app.auth.tenant import (
    DEFAULT_TENANT_ID,
    get_current_tenant_id,
    normalize_tenant_id,
    reset_current_tenant_id,
    set_current_tenant_id,
)
from app.config.settings import settings


def test_access_token_contains_tenant_claim() -> None:
    payload = decode_access_token(create_access_token(7, "alice", tenant_id=12))

    assert payload["sub"] == "7"
    assert payload["tenant_id"] == 12


def test_access_token_defaults_to_legacy_tenant() -> None:
    payload = decode_access_token(create_access_token(7, "alice"))

    assert payload["tenant_id"] == DEFAULT_TENANT_ID


@pytest.mark.parametrize("value", [0, -1, "invalid"])
def test_tenant_id_must_be_positive(value: object) -> None:
    with pytest.raises(ValueError, match="positive integer"):
        normalize_tenant_id(value)


def test_legacy_token_without_tenant_claim_remains_decodable() -> None:
    token = jwt.encode(
        {
            "sub": "7",
            "username": "alice",
            "iat": datetime.now(UTC),
            "exp": datetime.now(UTC) + timedelta(minutes=5),
        },
        settings.jwt_secret,
        algorithm=settings.jwt_algorithm,
    )

    payload = decode_access_token(token)
    assert "tenant_id" not in payload


def test_tenant_context_can_be_scoped_and_restored() -> None:
    original = get_current_tenant_id()
    token = set_current_tenant_id(12)
    try:
        assert get_current_tenant_id() == 12
    finally:
        reset_current_tenant_id(token)

    assert get_current_tenant_id() == original
