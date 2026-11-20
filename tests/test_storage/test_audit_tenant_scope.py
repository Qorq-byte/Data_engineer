"""Tenant path isolation for local usage audits."""

from __future__ import annotations

from pathlib import Path

from app.audit.store import UsageAuditStore
from app.auth.tenant import reset_current_tenant_id, set_current_tenant_id
from app.workflow.query_audit import QueryAuditState, finish_audit


async def test_audit_store_isolates_writes_reads_and_retention(tmp_path: Path) -> None:
    base = tmp_path / "usage.db"
    query_id = "q_000000000001"
    first = set_current_tenant_id(1)
    try:
        default_store = UsageAuditStore(str(base))
        assert default_store.path == base
        assert await finish_audit(
            QueryAuditState(query_id=query_id, store=default_store, outcome="completed"), "same",
        ) == "persisted"
    finally:
        reset_current_tenant_id(first)

    second = set_current_tenant_id(9)
    try:
        tenant_store = UsageAuditStore(str(base), max_records=1)
        assert tenant_store.path == tmp_path / "usage.tenant-9.db"
        assert tenant_store.read(query_id) is None
        assert await finish_audit(
            QueryAuditState(query_id=query_id, store=tenant_store, outcome="error"), "same",
        ) == "persisted"
        assert tenant_store.read(query_id)["outcome"] == "error"
        assert await finish_audit(QueryAuditState(store=tenant_store), "next") == "persisted"
        assert tenant_store.read(query_id) is None
    finally:
        reset_current_tenant_id(second)

    assert UsageAuditStore(str(base)).read(query_id)["outcome"] == "completed"
