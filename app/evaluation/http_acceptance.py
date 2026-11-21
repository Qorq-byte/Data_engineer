"""Destination and credential handling shared by deployment acceptance commands."""

import ipaddress
import os

import httpx


def target_origin(value: str, *, allow_path: bool = False) -> httpx.URL:
    origin = httpx.URL(value)
    if (
        not origin.host or (not allow_path and origin.path != "/")
        or origin.query or origin.fragment or origin.username or origin.password
    ):
        raise ValueError("Invalid deployment URL")
    if origin.scheme == "https":
        return origin
    if origin.scheme == "http" and ipaddress.ip_address(origin.host).is_loopback:
        return origin
    raise ValueError("Use HTTPS for deployments or a loopback IP for local HTTP")


def bearer_headers(env_name: str) -> dict[str, str]:
    token = os.environ.get(env_name, "")
    if not token or len(token) > 4096 or any(not 33 <= ord(char) <= 126 for char in token):
        raise ValueError("A valid operator token is required in the environment")
    return {"Authorization": f"Bearer {token}"}
