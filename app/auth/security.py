"""Password hashing (bcrypt) and JWT token management."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import bcrypt
import jwt

from app.config.settings import settings

# ── Password hashing ────────────────────────────────────────────


def hash_password(password: str) -> str:
    """Return bcrypt hash of *password* as a string."""
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    """Check *plain* against the bcrypt *hashed* digest."""
    return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))


# ── JWT ─────────────────────────────────────────────────────────


def create_access_token(user_id: int, username: str) -> str:
    """Encode a JWT with ``sub``, ``username``, and ``exp`` claims."""
    now = datetime.now(UTC)
    payload = {
        "sub": str(user_id),
        "username": username,
        "iat": now,
        "exp": now + timedelta(minutes=settings.jwt_expire_minutes),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_access_token(token: str) -> dict:
    """Decode and validate *token*. Raises ``jwt.PyJWTError`` on failure."""
    return jwt.decode(
        token,
        settings.jwt_secret,
        algorithms=[settings.jwt_algorithm],
    )
