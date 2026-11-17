"""Shared query projection and redacted transport failures for MCP query tools."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from fastapi import HTTPException
from pydantic import ValidationError

from app.workflow.contracts import (
    QueryCapacityError,
    QueryFailure,
    classify_query_failure,
)

if TYPE_CHECKING:
    from app.api.query import QueryResponse


def project_query(
    response: QueryResponse, *, require_execution: bool = True, include_plan_output: bool = False,
) -> dict[str, Any]:
    execution = response.execution or {}
    errors = list(response.errors)
    if require_execution and not execution and not errors:
        errors.append("Query did not produce an execution result")
    return {
        "status": "failed" if errors else response.status,
        "query_id": response.query_id, "session_id": response.session_id,
        "plan_id": response.plan_id, "workflow_ids": response.workflow_ids,
        "llm_calls": response.llm_calls, "llm_usage": response.llm_usage,
        "llm_audit": response.llm_audit,
        "sql": response.primary_sql or "",
        "candidates": [
            candidate.model_dump(exclude={"execution"}) for candidate in response.candidates
        ],
        "columns": execution.get("columns", []), "rows": execution.get("rows", [])[:100],
        "row_count": min(execution.get("row_count", 0), 100),
        "truncated": execution.get("truncated", False) or execution.get("row_count", 0) > 100,
        "errors": errors, "warnings": response.warnings,
        **({"plan_output": response.plan_output, "validation": response.validation,
            "executed": response.execution is not None} if include_plan_output else {}),
        **({"error": "; ".join(errors), "error_code": "WORKFLOW_FAILED"} if errors else {}),
    }


def query_error(exc: Exception) -> dict[str, Any]:
    failure = classify_query_failure(exc)
    if isinstance(exc, ValidationError):
        failure = QueryFailure("INVALID_REQUEST", "Invalid query request", 422)
    elif isinstance(exc, HTTPException):
        detail: dict[str, Any] = exc.detail if isinstance(exc.detail, dict) else {}
        if detail.get("code") == "QUERY_TIMEOUT":
            failure = classify_query_failure(TimeoutError())
        elif detail.get("code") == "QUERY_CAPACITY":
            failure = classify_query_failure(QueryCapacityError())
        elif exc.status_code in {401, 403}:
            failure = QueryFailure(
                "INVALID_REQUEST", "Operator authentication required", exc.status_code
            )
        elif 400 <= exc.status_code < 500:
            failure = QueryFailure("INVALID_REQUEST", "Invalid query request", exc.status_code)
    return {
        "status": "failed", "error": failure.message, "error_code": failure.code,
        "http_status": failure.http_status, "error_type": type(exc).__name__,
        "errors": [failure.message], "warnings": [], "sql": "", "columns": [], "rows": [],
        "row_count": 0, "truncated": False,
        **({"retry_after_seconds": failure.retry_after_seconds}
           if failure.retry_after_seconds is not None else {}),
    }
