"""Tenant-aware metric catalog selection tests."""

from __future__ import annotations

from pathlib import Path

from app.auth.tenant import reset_current_tenant_id, set_current_tenant_id
from app.knowledge.metric_catalog import load_catalog


def test_metric_binding_selection_is_tenant_scoped(tmp_path: Path) -> None:
    path = tmp_path / "metrics.yml"
    path.write_text(
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
    catalog = load_catalog(str(path))

    first = set_current_tenant_id(1)
    try:
        assert catalog.select(
            version="v1", domain="sales", database="db", metrics=["revenue"],
            dimensions=[], filters={}
        ).metrics[0].table == "orders"
    finally:
        reset_current_tenant_id(first)

    second = set_current_tenant_id(2)
    try:
        assert catalog.select(
            version="v1", domain="sales", database="db", metrics=["revenue"],
            dimensions=[], filters={}, tenant_id=2
        ).metrics[0].table == "invoices"
    finally:
        reset_current_tenant_id(second)
