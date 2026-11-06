"""The MVP is a private shared workbench, restricted to configured operators."""

from typing import Annotated, Any

from fastapi import Depends, HTTPException, Request

from app.api.auth import get_current_user
from app.config.settings import settings


async def require_operator(
    user: Annotated[dict[str, Any], Depends(get_current_user)],
    request: Request,
) -> dict[str, Any]:
    if user["user_id"] not in settings.admin_user_ids:
        raise HTTPException(status_code=403, detail="Administrator access required")
    if settings.shared_worker_release:
        from app.harness.worker_bootstrap import mutable_request_allowed

        if not mutable_request_allowed(request.method, request.url.path):
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "OFFLINE_MAINTENANCE_REQUIRED",
                    "message": "Stop all workers before maintenance",
                },
            )
    return user
