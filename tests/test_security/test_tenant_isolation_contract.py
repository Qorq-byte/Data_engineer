"""Cross-surface tenant isolation contract checks."""

from __future__ import annotations

from pathlib import Path

from app.audit.store import UsageAuditStore
from app.auth.tenant import reset_current_tenant_id, set_current_tenant_id
from app.harness.session import AdvancedSQLiteSession
from app.knowledge.metric_catalog import load_catalog
from app.workflow.history_store import PlanHistoryStore


def test_runtime_surfaces_do_not_cross_tenant(tmp_path: Path) -> None:
    sessions = AdvancedSQLiteSession(str(tmp_path / "sessions.db"), node_name="chat")
    history = PlanHistoryStore(tmp_path / "plans.db")
    catalog_path = tmp_path / "metrics.yml"
    catalog_path.write_text(
        """version: v1
bindings:
  - tenant_id: 1
    domain: sales
    database: db
    metrics:
      - name: revenue
        table: orders
        column: amount
        aggregation: sum
        dimensions: [region]
  - tenant_id: 2
    domain: sales
    database: db
    metrics:
      - name: revenue
        table: invoices
        column: total
        aggregation: sum
        dimensions: [region]
""",
        encoding="utf-8",
    )
    catalog = load_catalog(str(catalog_path))
    audit_path = tmp_path / "usage.db"

    first = set_current_tenant_id(1)
    try:
        sessions.save_turn("same", {"tenant": 1})
        history.record("same query", "request_query")
        assert UsageAuditStore(str(audit_path)).path == audit_path
        assert catalog.select(
            version="v1", domain="sales", database="db", metrics=["revenue"],
            dimensions=[], filters={}
        ).metrics[0].table == "orders"
    finally:
        reset_current_tenant_id(first)

    second = set_current_tenant_id(2)
    try:
        assert sessions.get_turns("same") == []
        assert history.find_similar("same query") == []
        assert UsageAuditStore(str(audit_path)).path != audit_path
        assert catalog.select(
            version="v1", domain="sales", database="db", metrics=["revenue"],
            dimensions=[], filters={}
        ).metrics[0].table == "invoices"
    finally:
        reset_current_tenant_id(second)
        sessions.close()
        history.close()
