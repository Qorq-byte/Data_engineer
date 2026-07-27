"""Auth API endpoints — register, login, current-user, password change, avatar, account deletion.

POST   /api/v1/auth/register  — create account (returns JWT)
POST   /api/v1/auth/login     — authenticate (returns JWT)
GET    /api/v1/auth/me        — get current user (requires Bearer token)
PUT    /api/v1/auth/password  — change password
POST   /api/v1/auth/avatar    — upload avatar image
DELETE /api/v1/auth/account   — delete account
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.auth.database import get_connection
from app.auth.models import (
    ChangePasswordRequest,
    DeleteAccountRequest,
    LoginRequest,
    RegisterRequest,
    TokenResponse,
    UserResponse,
)
from app.auth.security import (
    create_access_token,
    decode_access_token,
    hash_password,
    verify_password,
)

router = APIRouter()
_security = HTTPBearer()

# Avatar upload directory
_AVATAR_DIR = Path(__file__).parent.parent.parent / "data" / "avatars"


def _ensure_avatar_dir() -> None:
    """Ensure the avatar upload directory exists."""
    _AVATAR_DIR.mkdir(parents=True, exist_ok=True)


# ── Helper: build response from a row ───────────────────────────


def _row_to_response(row: dict) -> UserResponse:
    """Convert a users table row dict to a UserResponse."""
    return UserResponse(
        id=row["id"],
        username=row["username"],
        email=row["email"],
        avatar_url=row.get("avatar_url", ""),
        created_at=row["created_at"].isoformat() if hasattr(row["created_at"], "isoformat") else str(row["created_at"]),
    )


def _row_to_token(row: dict) -> TokenResponse:
    """Convert a users table row dict to a TokenResponse with JWT."""
    user_id = row["id"]
    username = row["username"]
    token = create_access_token(user_id, username)
    return TokenResponse(
        access_token=token,
        user=_row_to_response(row),
    )


# ── Auth dependency ─────────────────────────────────────────────


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(_security),
) -> dict:
    """FastAPI dependency that validates the Bearer token and returns user info."""
    try:
        payload = decode_access_token(credentials.credentials)
    except Exception:
        raise HTTPException(status_code=401, detail="登入已过期，请重新登入") from None

    user_id = payload.get("sub")
    username = payload.get("username")
    if not user_id or not username:
        raise HTTPException(status_code=401, detail="无效的认证令牌")

    return {"user_id": int(user_id), "username": username}


# ── Endpoints ───────────────────────────────────────────────────


@router.post("/register", response_model=TokenResponse, status_code=201)
async def register(body: RegisterRequest) -> TokenResponse:
    """Register a new user account. Returns a JWT token on success."""
    try:
        async with get_connection() as conn, conn.cursor() as cur:
            # Check for duplicate email
            await cur.execute("SELECT id FROM users WHERE email = %s", (body.email,))
            if await cur.fetchone():
                raise HTTPException(status_code=409, detail="该邮箱已被注册")

            # Check for duplicate username
            await cur.execute("SELECT id FROM users WHERE username = %s", (body.username,))
            if await cur.fetchone():
                raise HTTPException(status_code=409, detail="该用户名已被使用")

            # Insert new user
            pw_hash = hash_password(body.password)
            await cur.execute(
                "INSERT INTO users (username, email, password_hash) VALUES (%s, %s, %s)",
                (body.username, body.email, pw_hash),
            )
            user_id = cur.lastrowid

            # Fetch back the inserted row
            await cur.execute("SELECT * FROM users WHERE id = %s", (user_id,))
            row = await cur.fetchone()
            if row is None:
                raise HTTPException(status_code=500, detail="注册失败，请稍后重试")

            # Build response (dict keyed by column name)
            cols = [desc[0] for desc in cur.description]
            user_row = dict(zip(cols, row, strict=True))

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail="数据库连接失败，请稍后重试") from e

    return _row_to_token(user_row)


@router.post("/login", response_model=TokenResponse)
async def login(body: LoginRequest) -> TokenResponse:
    """Authenticate with email + password. Returns a JWT token."""
    try:
        async with get_connection() as conn, conn.cursor() as cur:
            await cur.execute(
                "SELECT * FROM users WHERE email = %s", (body.email,)
            )
            row = await cur.fetchone()
            if row is None:
                raise HTTPException(status_code=401, detail="邮箱或密码错误")

            cols = [desc[0] for desc in cur.description]
            user_row = dict(zip(cols, row, strict=True))

            if not verify_password(body.password, user_row["password_hash"]):
                raise HTTPException(status_code=401, detail="邮箱或密码错误")

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail="数据库连接失败，请稍后重试") from e

    return _row_to_token(user_row)


@router.get("/me", response_model=UserResponse)
async def me(current_user: dict = Depends(get_current_user)) -> UserResponse:
    """Return the currently authenticated user's profile."""
    try:
        async with get_connection() as conn, conn.cursor() as cur:
            await cur.execute(
                "SELECT * FROM users WHERE id = %s", (current_user["user_id"],)
            )
            row = await cur.fetchone()
            if row is None:
                raise HTTPException(status_code=404, detail="用户不存在")

            cols = [desc[0] for desc in cur.description]
            user_row = dict(zip(cols, row, strict=True))

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail="数据库连接失败，请稍后重试") from e

    return _row_to_response(user_row)


@router.put("/password")
async def change_password(
    body: ChangePasswordRequest,
    current_user: dict = Depends(get_current_user),
) -> dict:
    """Change the current user's password."""
    try:
        async with get_connection() as conn, conn.cursor() as cur:
            # Fetch current user
            await cur.execute(
                "SELECT * FROM users WHERE id = %s", (current_user["user_id"],)
            )
            row = await cur.fetchone()
            if row is None:
                raise HTTPException(status_code=404, detail="用户不存在")

            cols = [desc[0] for desc in cur.description]
            user_row = dict(zip(cols, row, strict=True))

            # Verify old password
            if not verify_password(body.old_password, user_row["password_hash"]):
                raise HTTPException(status_code=400, detail="当前密码错误")

            # Update password
            new_hash = hash_password(body.new_password)
            await cur.execute(
                "UPDATE users SET password_hash = %s WHERE id = %s",
                (new_hash, current_user["user_id"]),
            )

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail="密码修改失败，请稍后重试") from e

    return {"status": "ok", "message": "密码修改成功"}


@router.post("/avatar")
async def upload_avatar(
    file: UploadFile = File(...),
    current_user: dict = Depends(get_current_user),
) -> dict:
    """Upload a new avatar image for the current user."""
    # Validate file type
    allowed_types = {"image/jpeg", "image/png", "image/gif", "image/webp"}
    if file.content_type not in allowed_types:
        raise HTTPException(status_code=400, detail="仅支持 JPEG、PNG、GIF、WebP 格式的图片")

    # Validate file size (max 5MB)
    contents = await file.read()
    if len(contents) > 5 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="图片大小不能超过 5MB")

    _ensure_avatar_dir()

    # Generate unique filename
    ext = os.path.splitext(file.filename or "avatar.png")[1] or ".png"
    filename = f"{current_user['user_id']}_{uuid.uuid4().hex[:8]}{ext}"
    filepath = _AVATAR_DIR / filename

    # Save file
    filepath.write_bytes(contents)

    # Build avatar URL
    avatar_url = f"/avatars/{filename}"

    # Update database
    try:
        async with get_connection() as conn, conn.cursor() as cur:
            await cur.execute(
                "UPDATE users SET avatar_url = %s WHERE id = %s",
                (avatar_url, current_user["user_id"]),
            )
    except Exception as e:
        # Clean up uploaded file on DB failure
        if filepath.exists():
            filepath.unlink()
        raise HTTPException(status_code=500, detail="头像更新失败，请稍后重试") from e

    return {"status": "ok", "avatar_url": avatar_url, "message": "头像更新成功"}


@router.delete("/account")
async def delete_account(
    body: DeleteAccountRequest,
    current_user: dict = Depends(get_current_user),
) -> dict:
    """Permanently delete the current user's account."""
    try:
        async with get_connection() as conn, conn.cursor() as cur:
            # Fetch current user
            await cur.execute(
                "SELECT * FROM users WHERE id = %s", (current_user["user_id"],)
            )
            row = await cur.fetchone()
            if row is None:
                raise HTTPException(status_code=404, detail="用户不存在")

            cols = [desc[0] for desc in cur.description]
            user_row = dict(zip(cols, row, strict=True))

            # Verify password
            if not verify_password(body.password, user_row["password_hash"]):
                raise HTTPException(status_code=400, detail="密码错误，无法注销账号")

            # Delete user
            await cur.execute(
                "DELETE FROM users WHERE id = %s", (current_user["user_id"],)
            )

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail="账号注销失败，请稍后重试") from e

    return {"status": "ok", "message": "账号已注销"}
