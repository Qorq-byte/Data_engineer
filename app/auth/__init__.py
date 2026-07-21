"""Authentication module — MySQL-backed user accounts, bcrypt hashing, JWT tokens."""

from app.auth.database import close_auth_pool, init_auth_db

__all__ = ["init_auth_db", "close_auth_pool"]
