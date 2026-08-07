"""Secret encryption — AES-256-GCM with a key derived from JWT_SECRET.

Used to persist user-supplied LLM API keys in MySQL without storing them in
plain text. Any API response must never return decrypted values.

Security note: the ciphertext and JWT_SECRET live in different places
(MySQL vs .env), so a database leak alone does not expose the keys. If both
leak together this scheme provides no protection — document honestly.
"""

from __future__ import annotations

import base64
import hashlib
import logging
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

logger = logging.getLogger(__name__)

_NONCE_BYTES = 12
_DEFAULT_JWT_SECRET = "change-me-in-production"

_warned_default_secret = False


def _derive_key() -> bytes:
    """Derive a 32-byte AES key from JWT_SECRET (SHA-256)."""
    global _warned_default_secret
    from app.config.settings import settings

    secret = settings.jwt_secret or os.getenv("JWT_SECRET", "") or _DEFAULT_JWT_SECRET
    if secret == _DEFAULT_JWT_SECRET and not _warned_default_secret:
        logger.warning(
            "JWT_SECRET uses the default value — persisted API keys are only "
            "weakly protected. Set a strong JWT_SECRET in .env."
        )
        _warned_default_secret = True
    return hashlib.sha256(secret.encode("utf-8")).digest()


def encrypt_secret(plaintext: str) -> str:
    """Encrypt *plaintext* into a base64 token (nonce || ciphertext+tag).

    Raises:
        ValueError: If plaintext is empty.
    """
    if not plaintext:
        raise ValueError("plaintext must not be empty")
    nonce = os.urandom(_NONCE_BYTES)
    ciphertext = AESGCM(_derive_key()).encrypt(nonce, plaintext.encode("utf-8"), None)
    return base64.b64encode(nonce + ciphertext).decode("ascii")


def decrypt_secret(token: str) -> str | None:
    """Decrypt a token produced by :func:`encrypt_secret`.

    Returns ``None`` on any failure (bad token, tampered ciphertext, wrong
    key) instead of raising, so callers can degrade gracefully.
    """
    if not token:
        return None
    try:
        raw = base64.b64decode(token.encode("ascii"), validate=True)
        nonce, ciphertext = raw[:_NONCE_BYTES], raw[_NONCE_BYTES:]
        if not ciphertext:
            return None
        return AESGCM(_derive_key()).decrypt(nonce, ciphertext, None).decode("utf-8")
    except Exception:  # noqa: BLE001 — any failure means "not decryptable"
        return None
