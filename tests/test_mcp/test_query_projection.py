"""MCP retains canonical shared-state failures across the HTTP service boundary."""

import pytest
from fastapi import HTTPException

from app.harness.mysql_coordination import CoordinationUnavailableError
from app.mcp.query_projection import query_error
from app.storage.shared_sessions import SessionStoreUnavailableError


@pytest.mark.parametrize("cause", [CoordinationUnavailableError, SessionStoreUnavailableError])
def test_wrapped_shared_state_failure_retains_code_and_safe_retry(cause):
    direct = query_error(cause("private-driver-and-path"))
    wrapped = query_error(HTTPException(503, detail={
        "code": "SHARED_STATE_UNAVAILABLE", "message": "private-driver-and-path",
        "retry_after_seconds": 99999,
    }))
    for field in ("error_code", "http_status", "error", "retry_after_seconds", "rows"):
        assert direct[field] == wrapped[field]
    assert wrapped["http_status"] == 503 and wrapped["retry_after_seconds"] == 1
    assert wrapped["error_code"] == "SHARED_STATE_UNAVAILABLE"
    assert "private-driver-and-path" not in str(wrapped)


@pytest.mark.parametrize("status,code,expected", [
    (503, "unknown-private-code", "INTERNAL_ERROR"),
    (400, "SHARED_STATE_UNAVAILABLE", "INVALID_REQUEST"),
])
def test_unknown_or_wrong_status_http_error_cannot_select_shared_failure(status, code, expected):
    result = query_error(HTTPException(status, detail={"code": code, "message": "private-detail"}))
    assert result["error_code"] == expected and "private-detail" not in str(result)
