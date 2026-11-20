"""Storage and lock keys for tenant-aware conversation sessions."""

from hashlib import sha256

from app.auth.tenant import DEFAULT_TENANT_ID, get_current_tenant_id


def scoped_session_key(session_id: str) -> str | bytes:
    """Keep legacy text keys separate from all non-default tenant keys."""
    tenant_id = get_current_tenant_id()
    if tenant_id == DEFAULT_TENANT_ID:
        return session_id
    # Invalid as a UTF-8 leading byte, so no legacy text ID can hash to this input.
    return b"\xff" + f"{tenant_id}:{session_id}".encode()


def shared_session_digest(session_id: str) -> str:
    """Use the same digest for MySQL snapshots and their coordination locks."""
    key = scoped_session_key(session_id)
    return sha256(key.encode() if isinstance(key, str) else key).hexdigest()
