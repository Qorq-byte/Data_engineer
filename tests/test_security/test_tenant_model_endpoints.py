"""Exercise tenant model allow-listing and DNS checks without external traffic."""

import asyncio
import socket

import pytest

from app.auth.tenant import reset_current_tenant_id, set_current_tenant_id
from app.config.settings import settings
from app.llm.tenant_endpoints import approved_tenant_endpoint, resolve_public_model_address


def test_endpoint_approval_is_tenant_and_path_specific(monkeypatch):
    monkeypatch.setattr(settings, "tenant_llm_endpoints", {
        2: ["https://retail.example.test/v1/"],
        3: ["https://finance.example.test/v1"],
    })
    token = set_current_tenant_id(2)
    try:
        assert approved_tenant_endpoint("https://RETAIL.example.test:443/v1") == (
            "https://retail.example.test/v1"
        )
        with pytest.raises(ValueError, match="not approved"):
            approved_tenant_endpoint("https://finance.example.test/v1")
        with pytest.raises(ValueError, match="not approved"):
            approved_tenant_endpoint("https://retail.example.test/v2")
    finally:
        reset_current_tenant_id(token)


@pytest.mark.parametrize("url", [
    "http://retail.example.test/v1",
    "https://retail.example.test:8443/v1",
    "https://127.0.0.1/v1",
    "https://[::1]/v1",
    "https://user@retail.example.test/v1",
    "https://retail.example.test/v1?token=x",
    "https://retail.example.test/v1/%2e%2e",
    "https://retail.example.test/v1/../v2",
])
def test_unsafe_endpoint_form_is_rejected(url):
    with pytest.raises(ValueError, match="Invalid tenant model endpoint"):
        approved_tenant_endpoint(url)


@pytest.mark.parametrize("address", [
    "127.0.0.1", "10.0.0.9", "169.254.10.10", "100.64.0.1",
    "224.0.0.1", "::1", "fe80::1", "::ffff:10.0.0.1",
])
async def test_mixed_dns_response_never_selects_an_address(monkeypatch, address):
    async def lookup(host, port, *, type):
        assert (host, port, type) == ("retail.example.test", 443, socket.SOCK_STREAM)
        return [(0, 0, 0, "", (ip, port)) for ip in ("8.8.8.8", address)]

    monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", lookup)
    with pytest.raises(ValueError, match="tenant model address|public addresses"):
        await resolve_public_model_address("retail.example.test")


async def test_validated_address_is_returned_for_connection(monkeypatch):
    async def lookup(host, port, *, type):
        return [(socket.AF_INET, type, 0, "", ("8.8.8.8", port))]

    monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", lookup)
    assert await resolve_public_model_address("retail.example.test") == "8.8.8.8"
