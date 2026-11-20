"""Operator authentication for the NL2SQL MCP HTTP transports."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from typing import Any

from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from mcp.server.auth.middleware.bearer_auth import AuthenticatedUser
from mcp.server.auth.provider import AccessToken
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from app.api.auth import get_current_user
from app.auth import database
from app.auth.access import require_operator_identity, verify_tenant_worker_configuration
from app.auth.tenant import reset_current_tenant_id, set_current_tenant_id
from app.config.settings import settings
from app.harness.shared_query_runtime import current_shared_runtime


@asynccontextmanager
async def mcp_auth_lifespan() -> AsyncIterator[None]:
    """Initialize auth for a standalone HTTP host or borrow its parent app's pool."""
    if database.auth_database_ready():
        yield
        return
    try:
        # Auth requests return 503 until a successful initialization.
        with suppress(Exception):
            await asyncio.wait_for(database.init_auth_db(), timeout=10)
        yield
    finally:
        await database.close_auth_pool()


@asynccontextmanager
async def mcp_tenant_lifespan() -> AsyncIterator[None]:
    """Provide model storage and targets when running a tenant MCP host by itself."""
    from app.db.tenant_profiles import close_tenant_query_profiles, load_tenant_query_profiles
    from app.storage.mysql_store import get_store, init_db

    if settings.shared_worker_release:
        if current_shared_runtime() is not None:
            yield
        else:
            from app.api import create_app

            host = create_app()
            async with host.router.lifespan_context(host):
                yield
        return
    if not settings.tenant_admin_user_ids or get_store():
        yield
        return
    opened: list[tuple[int, str]] = []
    try:
        await asyncio.wait_for(init_db(), timeout=10)
        opened = await load_tenant_query_profiles()
        yield
    finally:
        try:
            await close_tenant_query_profiles(opened)
        finally:
            store = get_store()
            if store is not None:
                await store.close()


async def require_mcp_http_operator(request: Request) -> dict[str, Any]:
    values = request.headers.getlist("authorization")
    parts = values[0].split() if len(values) == 1 else []
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise HTTPException(status_code=401, detail="Operator Bearer token required")
    credentials = HTTPAuthorizationCredentials(scheme="Bearer", credentials=parts[1])
    user = await get_current_user(credentials)
    require_operator_identity(user)
    return user


class OperatorHTTPMiddleware:
    """Authenticate before FastMCP accepts a session or dispatches a request."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        try:
            user = await require_mcp_http_operator(Request(scope))
            await verify_tenant_worker_configuration(user["tenant_id"])
        except HTTPException as exc:
            headers = {"WWW-Authenticate": "Bearer"} if exc.status_code == 401 else None
            response = JSONResponse(
                {"detail": exc.detail}, status_code=exc.status_code, headers=headers
            )
            await response(scope, receive, send)
            return

        # The SDK compares this principal on both Streamable HTTP and SSE sessions.
        tenant_id, user_id, version = user["tenant_id"], user["user_id"], user["token_version"]
        principal = AuthenticatedUser(AccessToken(
            token="", client_id="nl2sql-workbench", scopes=[],
            subject=f"{tenant_id}:{user_id}:{version}",
        ))
        token = set_current_tenant_id(tenant_id)
        try:
            await self.app(dict(scope, user=principal), receive, send)
        finally:
            reset_current_tenant_id(token)
