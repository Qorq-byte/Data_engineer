"""Tenant identity primitives shared by authentication and storage layers.

The first migration slice carries tenant identity through authentication while
the existing single-workspace deployment remains on tenant ``1``. Storage
filters are introduced in a later slice once every persistence path can use
the same context.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar, Token

DEFAULT_TENANT_ID = 1
_current_tenant_id: ContextVar[int] = ContextVar(
    "current_tenant_id", default=DEFAULT_TENANT_ID
)


def normalize_tenant_id(value: object, *, default: int = DEFAULT_TENANT_ID) -> int:
    """Return a positive tenant id, falling back for legacy auth records."""
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        raise ValueError("tenant_id must be a positive integer")
    if isinstance(value, int):
        tenant_id = value
    elif isinstance(value, str):
        try:
            tenant_id = int(value)
        except ValueError:
            raise ValueError("tenant_id must be a positive integer") from None
    else:
        raise ValueError("tenant_id must be a positive integer")
    if tenant_id < 1:
        raise ValueError("tenant_id must be a positive integer")
    return tenant_id


def get_current_tenant_id() -> int:
    """Return the tenant bound to the current request context."""
    return _current_tenant_id.get()


def set_current_tenant_id(value: object) -> Token[int]:
    """Bind a validated tenant id to the current async request context."""
    return _current_tenant_id.set(normalize_tenant_id(value))


def reset_current_tenant_id(token: Token[int]) -> None:
    """Restore the tenant context captured before a request started."""
    _current_tenant_id.reset(token)


@contextmanager
def tenant_scope(tenant_id: int) -> Iterator[None]:
    """Temporarily select a tenant for trusted deployment operations."""
    token = set_current_tenant_id(tenant_id)
    try:
        yield
    finally:
        reset_current_tenant_id(token)


__all__ = [
    "DEFAULT_TENANT_ID",
    "get_current_tenant_id",
    "normalize_tenant_id",
    "reset_current_tenant_id",
    "set_current_tenant_id",
    "tenant_scope",
]
