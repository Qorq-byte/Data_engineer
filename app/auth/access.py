"""Operator access for the default workbench and opt-in tenant routes."""

from collections.abc import AsyncIterator
from typing import Annotated, Any, Literal

from fastapi import Depends, HTTPException, Request

from app.api.auth import get_current_user
from app.auth.tenant import DEFAULT_TENANT_ID, reset_current_tenant_id, set_current_tenant_id
from app.config.settings import settings
from app.harness.mysql_coordination import CoordinationUnavailableError
from app.harness.shared_query_runtime import current_shared_runtime
from app.workflow.contracts import classify_query_failure

_TENANT_ROUTES = {
    "GET": {
        "/databases", "/schema/{db_id}", "/metrics", "/subagents", "/subagents/{name}",
        "/domains", "/domains/{domain_id}/glossary", "/domains/{domain_id}/rules",
        "/settings/llm", "/settings/available-models",
        "/sessions/{session_id}", "/sessions/{session_id}/versions",
        "/sessions/{session_id}/versions/diff",
    },
    "POST": {
        "/query", "/query/stream", "/query/generate", "/query/explain", "/query/candidates",
        "/subagents/{name}/query",
        "/domains", "/domains/detect",
        "/domains/{domain_id}/glossary", "/domains/{domain_id}/rules",
    },
    "PUT": {
        "/settings/llm", "/domains/{domain_id}",
        "/domains/{domain_id}/glossary/{term}", "/domains/{domain_id}/rules/{rule_id}",
    },
    "DELETE": {
        "/settings/llm/providers/{provider_name}", "/domains/{domain_id}",
        "/domains/{domain_id}/glossary/{term}", "/domains/{domain_id}/rules/{rule_id}",
    },
}


def _tenant_is_enrolled(tenant_id: int) -> bool:
    if not settings.tenant_admin_user_ids.get(tenant_id):
        return False
    if not settings.shared_worker_release:
        return True
    runtime = current_shared_runtime()
    return runtime is not None and runtime.ready and tenant_id in runtime.tenant_snapshots


def workspace_access(user: dict[str, Any]) -> Literal["system", "tenant", "account"]:
    """Project the same operator policy used by REST and MCP onto the workbench."""
    tenant_id = user["tenant_id"]
    if tenant_id == DEFAULT_TENANT_ID:
        return "system" if user["user_id"] in settings.admin_user_ids else "account"
    if _tenant_is_enrolled(tenant_id) and (
        user["user_id"] in settings.tenant_admin_user_ids[tenant_id]
    ):
        return "tenant"
    return "account"


def require_operator_identity(user: dict[str, Any]) -> None:
    if workspace_access(user) != "account":
        return
    tenant_id = user["tenant_id"]
    if tenant_id != DEFAULT_TENANT_ID and not _tenant_is_enrolled(tenant_id):
        raise HTTPException(status_code=403, detail="This workspace does not support this tenant")
    raise HTTPException(status_code=403, detail="Administrator access required")


async def verify_tenant_worker_configuration(tenant_id: int) -> None:
    if tenant_id == DEFAULT_TENANT_ID or not settings.shared_worker_release:
        return
    runtime = current_shared_runtime()
    try:
        if runtime is None or not runtime.ready or runtime.verify_configuration is None:
            raise CoordinationUnavailableError("Tenant worker release is unavailable")
        await runtime.verify_configuration()
    except CoordinationUnavailableError as exc:
        failure = classify_query_failure(exc)
        raise HTTPException(status_code=failure.http_status, detail=failure.payload()) from None


async def require_operator(
    user: Annotated[dict[str, Any], Depends(get_current_user)],
    request: Request,
) -> AsyncIterator[dict[str, Any]]:
    require_operator_identity(user)
    tenant_id = user["tenant_id"]
    route_path = getattr(request.scope.get("route"), "path", "").removeprefix("/api/v1")
    if tenant_id != DEFAULT_TENANT_ID and route_path not in _TENANT_ROUTES.get(
        request.method, set(),
    ):
        raise HTTPException(status_code=403, detail="Route unavailable for this tenant")
    if settings.shared_worker_release:
        from app.harness.worker_bootstrap import mutable_request_allowed

        tenant_domain_detection = (
            tenant_id != DEFAULT_TENANT_ID
            and request.method == "POST" and route_path == "/domains/detect"
        )
        if not tenant_domain_detection and not mutable_request_allowed(
            request.method, request.url.path,
        ):
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "OFFLINE_MAINTENANCE_REQUIRED",
                    "message": "Stop all workers before maintenance",
                },
            )
        if not route_path.startswith(("/query", "/sessions/")) and route_path != "/metrics":
            await verify_tenant_worker_configuration(tenant_id)
    token = set_current_tenant_id(tenant_id)
    try:
        yield user
    finally:
        reset_current_tenant_id(token)
