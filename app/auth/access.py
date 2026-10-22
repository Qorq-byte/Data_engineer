"""The MVP is a private shared workbench, restricted to configured operators."""

from typing import Annotated, Any

from fastapi import Depends, HTTPException

from app.api.auth import get_current_user
from app.config.settings import settings


async def require_operator(
    user: Annotated[dict[str, Any], Depends(get_current_user)],
) -> dict[str, Any]:
    if user["user_id"] not in settings.admin_user_ids:
        raise HTTPException(status_code=403, detail="Administrator access required")
    return user
