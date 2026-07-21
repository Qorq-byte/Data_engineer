"""Pydantic models for auth API requests and responses."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from pydantic import BaseModel, Field

# ── Request models ──────────────────────────────────────────────


class RegisterRequest(BaseModel):
    """Request body for POST /api/v1/auth/register."""

    username: str = Field(..., min_length=2, max_length=50, description="用户名")
    email: str = Field(..., min_length=5, max_length=120, description="邮箱地址")
    password: str = Field(..., min_length=6, max_length=128, description="密码")


class LoginRequest(BaseModel):
    """Request body for POST /api/v1/auth/login."""

    email: str = Field(..., min_length=5, max_length=120, description="邮箱地址")
    password: str = Field(..., min_length=1, max_length=128, description="密码")


# ── Response models ─────────────────────────────────────────────


class UserResponse(BaseModel):
    """Public user profile — never includes password_hash."""

    id: int
    username: str
    email: str
    avatar_url: str = ""
    created_at: str  # ISO-8601 string


class TokenResponse(BaseModel):
    """Returned after successful login or registration."""

    access_token: str
    token_type: str = "bearer"
    user: UserResponse


class ChangePasswordRequest(BaseModel):
    """Request body for PUT /api/v1/auth/password."""

    old_password: str = Field(..., min_length=1, max_length=128, description="当前密码")
    new_password: str = Field(..., min_length=6, max_length=128, description="新密码")


class DeleteAccountRequest(BaseModel):
    """Request body for DELETE /api/v1/auth/account."""

    password: str = Field(..., min_length=1, max_length=128, description="当前密码（确认身份）")


# ── Internal domain model ───────────────────────────────────────


@dataclass
class User:
    """Internal user dataclass (matches the users table row)."""

    id: int
    username: str
    email: str
    password_hash: str
    created_at: datetime
    updated_at: datetime
