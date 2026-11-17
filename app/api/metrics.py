"""Operator-only metric discovery; explicit domain/database binding, no execution."""

from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query

from app.workflow.contracts import classify_query_failure
from app.workflow.metric_runtime import metric_catalog_view

router = APIRouter()


@router.get("/metrics")
async def list_metrics(
    domain: Annotated[str, Query(min_length=1, max_length=128)],
    database: Annotated[str, Query(min_length=1, max_length=128)],
) -> dict[str, Any]:
    try:
        return await metric_catalog_view(domain, database)
    except Exception as exc:
        failure = classify_query_failure(exc)
        raise HTTPException(
            status_code=failure.http_status, detail=failure.payload(),
            headers={"Retry-After": str(failure.retry_after_seconds)}
            if failure.retry_after_seconds is not None else None,
        ) from None
