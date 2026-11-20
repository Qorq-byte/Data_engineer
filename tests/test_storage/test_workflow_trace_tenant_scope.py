"""Process-local workflow traces must respect the request tenant."""

from __future__ import annotations

from app.api import workflows
from app.auth.tenant import reset_current_tenant_id, set_current_tenant_id


def test_recorded_workflow_trace_carries_tenant_id() -> None:
    original = list(workflows._TRACE_STORE)
    workflows._TRACE_STORE.clear()
    token = set_current_tenant_id(23)
    try:
        workflows.record_trace("request_query", "success", 5)
        assert workflows._TRACE_STORE[-1]["tenant_id"] == 23
    finally:
        reset_current_tenant_id(token)
        workflows._TRACE_STORE[:] = original
