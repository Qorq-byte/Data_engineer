"""Deployment-approved HTTPS endpoints for tenant model traffic."""

import asyncio
import ipaddress
import re
import socket
from urllib.parse import urlsplit

from app.auth.tenant import get_current_tenant_id
from app.config.settings import settings

_DNS_NAME = re.compile(r"(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z][a-z0-9-]{0,62}")
_PATH = re.compile(r"(?:/[a-zA-Z0-9._~-]+)*/?")


def approved_tenant_endpoint(url: str) -> str:
    """Return the exact normalized base URL when approved for this tenant."""
    normalized = _normalize_url(url)
    configured = settings.tenant_llm_endpoints.get(get_current_tenant_id(), ())
    if normalized not in {_normalize_url(endpoint) for endpoint in configured}:
        raise ValueError("Model endpoint is not approved for this tenant")
    return normalized


def _normalize_url(url: str) -> str:
    if any(ord(char) <= 32 or ord(char) >= 127 or char in "?#" for char in url):
        raise ValueError("Invalid tenant model endpoint")
    parts = urlsplit(url)
    host = parts.hostname or ""
    if (
        parts.scheme != "https" or len(host) > 253 or not _DNS_NAME.fullmatch(host)
        or parts.netloc.lower() not in (host, f"{host}:443")
        or not _PATH.fullmatch(parts.path)
        or any(part in (".", "..") for part in parts.path.split("/"))
    ):
        raise ValueError("Invalid tenant model endpoint")
    return f"https://{host}{parts.path.rstrip('/')}"


async def resolve_public_model_address(host: str) -> str:
    """Reject mixed public/private answers and return an IP to use for the socket."""
    results = await asyncio.get_running_loop().getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    addresses = [ipaddress.ip_address(item[4][0]) for item in results]
    for address in addresses:
        if isinstance(address, ipaddress.IPv6Address) and (
            address.ipv4_mapped or address.sixtofour or address.teredo or address.scope_id
        ):
            raise ValueError("Invalid tenant model address")
        if not address.is_global or address.is_multicast or address.is_reserved:
            raise ValueError("Tenant model endpoint requires public addresses")
    if not addresses:
        raise ValueError("Tenant model address unavailable")
    return str(addresses[0])
