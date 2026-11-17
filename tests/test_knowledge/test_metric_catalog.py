"""Catalog limits and deterministic aggregate semantics, using real SQLite."""

import hashlib
from copy import deepcopy
from pathlib import Path

import pytest
import sqlglot
import yaml

from app.knowledge.metric_catalog import CatalogError, load_catalog

FIXTURE = Path("tests/fixtures/metric_catalog.yml")


def selection(catalog=None, **overrides):
    params = {
        "version": "synthetic-ecommerce-v1", "domain": "ecommerce", "database": "metric-db",
        "metrics": ["revenue"], "dimensions": [], "filters": {},
    }
    params.update(overrides)
    return (catalog or load_catalog(str(FIXTURE))).select(**params)


def test_aggregate_results_and_fingerprint(sqlite_connection):
    selected = selection(
        metrics=["revenue", "order_count", "avg_amount", "min_amount", "max_amount"]
    )
    sql = selected.sql("sqlite")
    actual = tuple(sqlite_connection.execute(sql).fetchone())
    expected = tuple(sqlite_connection.execute(
        "SELECT SUM(amount), COUNT(*), AVG(amount), MIN(amount), MAX(amount) FROM orders"
    ).fetchone())
    assert actual == expected
    assert selected.catalog_sha256 == hashlib.sha256(FIXTURE.read_bytes()).hexdigest()


def test_grouped_filter_results(sqlite_connection):
    sql = selection(dimensions=["region"], filters={"status": "completed"}).sql("sqlite")
    actual = [tuple(row) for row in sqlite_connection.execute(sql)]
    expected = [tuple(row) for row in sqlite_connection.execute(
        "SELECT region, SUM(amount) FROM orders WHERE status='completed' "
        "GROUP BY region ORDER BY region"
    )]
    assert actual == expected


def test_filter_value_cannot_become_sql(sqlite_connection):
    sql = selection(filters={"region": "East' OR 1=1; DELETE FROM orders;"}).sql("sqlite")
    assert len(sqlglot.parse(sql, read="sqlite")) == 1
    assert sqlite_connection.execute(sql).fetchone()[0] is None
    assert sqlite_connection.execute("SELECT COUNT(*) FROM orders").fetchone()[0] == 5


@pytest.mark.parametrize("overrides", [
    {"version": ""}, {"version": "old"}, {"domain": "finance"}, {"database": "other"},
    {"metrics": []}, {"metrics": ["missing"]}, {"metrics": ["revenue", "REVENUE"]},
    {"metrics": ["revenue", "product_count"]}, {"dimensions": ["amount"]},
    {"dimensions": ["region", "region"]}, {"filters": {"amount": "10"}},
    {"filters": {"region": 1}}, {"filters": {"region": "\x00"}},
    {"filters": {"region": "a" * 1025}}, {"metrics": ["revenue; DROP TABLE orders"]},
    {"dimensions": ["region; DROP TABLE orders"]},
])
def test_selection_rejects_unsupported_requests(overrides):
    with pytest.raises(CatalogError):
        selection(**overrides)


@pytest.mark.parametrize("raw", [
    "", "version: one\nversion: two\nbindings: []", "version: &v one\nbindings: [*v]",
    "version: v1\nbindings: []", "version: v1\nbindings: []\nunknown: true", "[[[[",
])
def test_invalid_yaml_and_contract_rejected(tmp_path, raw):
    path = tmp_path / "catalog.yml"
    path.write_text(raw, encoding="utf-8")
    with pytest.raises(CatalogError):
        load_catalog(str(path))


@pytest.mark.parametrize("change", [
    {"aggregation": "delete"}, {"formula": "SUM(amount)"}, {"table": "other.orders"},
    {"column": "amount); DELETE FROM orders"}, {"column": "*", "aggregation": "sum"},
    {"dimensions": ["region", "REGION"]},
])
def test_unsafe_definitions_rejected(tmp_path, change):
    raw = yaml.safe_load(FIXTURE.read_text(encoding="utf-8"))
    raw["bindings"][0]["metrics"][0].update(change)
    path = tmp_path / "catalog.yml"
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    with pytest.raises(CatalogError):
        load_catalog(str(path))


def test_missing_oversized_duplicate_binding_and_duplicate_metric(tmp_path):
    with pytest.raises(CatalogError):
        load_catalog("")
    path = tmp_path / "catalog.yml"
    with pytest.raises(CatalogError):
        load_catalog(str(path))
    path.write_bytes(b" " * 1_048_577)
    with pytest.raises(CatalogError):
        load_catalog(str(path))
    for field in ("bindings", "metrics"):
        raw = yaml.safe_load(FIXTURE.read_text(encoding="utf-8"))
        rows = raw["bindings"] if field == "bindings" else raw["bindings"][0]["metrics"]
        # Dump without shared object references, so this verifies duplicates rather than aliases.
        rows.append(deepcopy(rows[0]))
        path.write_text(yaml.safe_dump(raw), encoding="utf-8")
        with pytest.raises(CatalogError):
            load_catalog(str(path))


@pytest.mark.parametrize("dialect", ["sqlite", "postgres", "mysql", "duckdb", "snowflake"])
def test_rendered_dialects_remain_single_read_queries(dialect):
    from app.harness.runner import WorkflowRunner
    from app.nodes.base import NodeOutput
    from app.nodes.execute_sql import ExecuteSQLNode

    sql = selection(dimensions=["region"], filters={"status": "completed"}).sql(dialect)
    assert len(sqlglot.parse(sql, read=dialect)) == 1
    output = NodeOutput(context={"sql": sql}, metadata={"dialect": dialect})
    assert WorkflowRunner._check_syntax_valid(output).passed is True
    assert ExecuteSQLNode._is_write_statement(sql, dialect) is False
