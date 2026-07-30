"""Tests for MetricFlowEngine — metric CRUD, resolution, SQL generation.

Covers metric definition loading, CRUD, resolve_metric, query_metrics,
cross-dialect translation, time-grain handling, and edge cases.
"""

from __future__ import annotations

import pytest

from app.knowledge.metricflow_layer import (
    MetricDef,
    MetricFlowEngine,
    MetricSQL,
    _date_trunc_expr,
    _quote_ident,
    _quote_literal,
    _sanitise_alias,
)

# ── Helpers ────────────────────────────────────────────────────────────


def _make_metric(
    name: str = "revenue",
    formula: str = "SUM(amount)",
    table: str = "orders",
    dimensions: list[str] | None = None,
    time_grain: str = "day",
    time_column: str = "",
    aggregation: str = "sum",
    precision: int = 2,
    domain: str = "",
    sql_template: str = "",
    aliases: list[str] | None = None,
    description: str = "",
) -> MetricDef:
    return MetricDef(
        name=name,
        formula=formula,
        table=table,
        dimensions=dimensions or [],
        time_grain=time_grain,
        time_column=time_column,
        aggregation=aggregation,
        precision=precision,
        domain=domain,
        sql_template=sql_template,
        aliases=aliases or [],
        description=description,
    )


# ── MetricDef tests ────────────────────────────────────────────────────


class TestMetricDef:
    def test_has_formula_true(self):
        m = _make_metric(formula="SUM(amount)")
        assert m.has_formula is True

    def test_has_formula_false(self):
        m = _make_metric(formula="")
        assert m.has_formula is False

    def test_has_template_true(self):
        m = _make_metric(sql_template="SELECT {metric} FROM {table}")
        assert m.has_template is True

    def test_has_template_false(self):
        m = _make_metric()
        assert m.has_template is False


# ── SQL helper tests ───────────────────────────────────────────────────


class TestSQLHelpers:
    def test_date_trunc_day(self):
        expr = _date_trunc_expr("created_at", "day")
        assert "DATE_TRUNC" in expr
        assert "day" in expr
        assert "created_at" in expr

    def test_date_trunc_invalid_grain(self):
        expr = _date_trunc_expr("created_at", "invalid_grain")
        assert "day" in expr  # falls back to day

    def test_quote_ident_simple(self):
        assert _quote_ident("orders") == "orders"

    def test_quote_ident_special_chars(self):
        assert _quote_ident("order items") == '"order items"'

    def test_quote_ident_already_quoted(self):
        assert _quote_ident('"already"') == '"already"'

    def test_quote_literal(self):
        assert _quote_literal("hello") == "'hello'"

    def test_quote_literal_escapes(self):
        assert _quote_literal("it's") == "'it''s'"

    def test_sanitise_alias(self):
        assert _sanitise_alias("Average Order Value") == "average_order_value"


# ── CRUD tests ─────────────────────────────────────────────────────────


class TestMetricFlowEngineCRUD:
    @pytest.fixture
    def engine(self) -> MetricFlowEngine:
        return MetricFlowEngine()

    def test_add_global(self, engine: MetricFlowEngine):
        m = _make_metric("revenue")
        engine.add(m)
        assert engine.metric_count == 1
        assert engine.get("revenue") is m

    def test_add_domain_scoped(self, engine: MetricFlowEngine):
        m = _make_metric("revenue", domain="ecommerce")
        engine.add(m, domain="ecommerce")
        assert engine.metric_count == 1
        assert engine.domain_count == 1
        assert engine.get("revenue", domain="ecommerce") is m

    def test_add_duplicate_overwrites(self, engine: MetricFlowEngine):
        m1 = _make_metric("revenue", formula="SUM(x)")
        m2 = _make_metric("revenue", formula="SUM(y)")
        engine.add(m1)
        engine.add(m2)
        assert engine.get("revenue").formula == "SUM(y)"

    def test_get_domain_takes_priority(self, engine: MetricFlowEngine):
        global_m = _make_metric("revenue", formula="global")
        domain_m = _make_metric("revenue", formula="domain")
        engine.add(global_m)
        engine.add(domain_m, domain="ecommerce")
        assert engine.get("revenue", domain="ecommerce").formula == "domain"

    def test_get_falls_back_to_global(self, engine: MetricFlowEngine):
        m = _make_metric("revenue")
        engine.add(m)
        assert engine.get("revenue", domain="ecommerce") is m

    def test_get_missing(self, engine: MetricFlowEngine):
        assert engine.get("nonexistent") is None

    def test_get_by_alias(self, engine: MetricFlowEngine):
        m = _make_metric("revenue", aliases=["rev", "sales"])
        engine.add(m)
        assert engine.get("rev") is m
        assert engine.get("sales") is m

    def test_get_case_insensitive(self, engine: MetricFlowEngine):
        m = _make_metric("Revenue")
        engine.add(m)
        assert engine.get("revenue") is m
        assert engine.get("REVENUE") is m

    def test_list_all_global(self, engine: MetricFlowEngine):
        engine.add(_make_metric("b"))
        engine.add(_make_metric("a"))
        names = [m.name for m in engine.list_all()]
        assert names == ["a", "b"]

    def test_list_all_domain(self, engine: MetricFlowEngine):
        engine.add(_make_metric("a"), domain="ecommerce")
        engine.add(_make_metric("b"), domain="ecommerce")
        assert len(engine.list_all(domain="ecommerce")) == 2

    def test_delete_existing(self, engine: MetricFlowEngine):
        engine.add(_make_metric("r"))
        assert engine.delete("r") is True
        assert engine.get("r") is None

    def test_delete_nonexistent(self, engine: MetricFlowEngine):
        assert engine.delete("nope") is False

    def test_clear_global(self, engine: MetricFlowEngine):
        engine.add(_make_metric("a"))
        engine.add(_make_metric("b"), domain="ecommerce")
        assert engine.clear() == 2
        assert engine.metric_count == 0

    def test_clear_domain(self, engine: MetricFlowEngine):
        engine.add(_make_metric("a"), domain="ecommerce")
        engine.add(_make_metric("b"))  # global
        assert engine.clear(domain="ecommerce") == 1
        assert engine.metric_count == 1


# ── Query metrics tests ────────────────────────────────────────────────


class TestQueryMetrics:
    @pytest.fixture
    def engine(self) -> MetricFlowEngine:
        eng = MetricFlowEngine()
        eng.add(_make_metric("revenue", formula="SUM(amount)",
                             table="orders", dimensions=["region", "date"],
                             time_grain="day", time_column="created_at"),
                domain="ecommerce")
        eng.add(_make_metric("order_count", formula="COUNT(*)",
                             table="orders", dimensions=["region"],
                             time_grain="day"),
                domain="ecommerce")
        eng.add(_make_metric("aov", formula="AVG(amount)",
                             table="orders", dimensions=["date"],
                             aggregation="avg"),
                domain="ecommerce")
        eng.add(_make_metric("nav", formula="SUM(value)",
                             table="assets", dimensions=["portfolio"],
                             time_grain="month", time_column="as_of_date"),
                domain="finance")
        return eng

    def test_single_metric_no_dimensions(self, engine: MetricFlowEngine):
        sql = engine.query_metrics(["revenue"], domain="ecommerce")
        assert "SUM(amount)" in sql
        assert "revenue" in sql.lower()
        assert "FROM orders" in sql

    def test_single_metric_with_dimensions(self, engine: MetricFlowEngine):
        sql = engine.query_metrics(
            ["revenue"], dimensions=["region"], domain="ecommerce",
        )
        assert "SUM(amount)" in sql
        assert "region" in sql
        assert "GROUP BY" in sql
        assert "FROM orders" in sql

    def test_multiple_metrics(self, engine: MetricFlowEngine):
        sql = engine.query_metrics(
            ["revenue", "order_count"],
            dimensions=["region"],
            domain="ecommerce",
        )
        assert "SUM(amount)" in sql
        assert "revenue" in sql.lower()
        assert "COUNT(*)" in sql
        assert "order_count" in sql.lower()
        assert "region" in sql

    def test_time_grain_override(self, engine: MetricFlowEngine):
        sql = engine.query_metrics(
            ["revenue"],
            dimensions=["date"],
            time_grain="month",
            domain="ecommerce",
        )
        assert "month" in sql.lower()
        assert "DATE_TRUNC" in sql

    def test_with_filters(self, engine: MetricFlowEngine):
        sql = engine.query_metrics(
            ["revenue"],
            dimensions=["region"],
            filters={"region": "CN"},
            domain="ecommerce",
        )
        assert "WHERE" in sql
        assert "region" in sql
        assert "CN" in sql

    def test_with_order_by(self, engine: MetricFlowEngine):
        sql = engine.query_metrics(
            ["revenue"],
            dimensions=["region"],
            order_by=["revenue"],
            domain="ecommerce",
        )
        assert "ORDER BY" in sql
        assert "revenue" in sql.lower()

    def test_with_limit(self, engine: MetricFlowEngine):
        sql = engine.query_metrics(
            ["revenue"],
            dimensions=["region"],
            limit=10,
            domain="ecommerce",
        )
        assert "LIMIT 10" in sql

    def test_finance_domain_metric(self, engine: MetricFlowEngine):
        sql = engine.query_metrics(
            ["nav"],
            dimensions=["portfolio"],
            domain="finance",
        )
        assert "SUM(value)" in sql
        assert "FROM assets" in sql
        assert "portfolio" in sql

    def test_unknown_metric_raises(self, engine: MetricFlowEngine):
        with pytest.raises(KeyError, match="unknown_metric"):
            engine.query_metrics(["unknown_metric"], domain="ecommerce")

    def test_missing_table_raises(self, engine: MetricFlowEngine):
        eng = MetricFlowEngine()
        eng.add(_make_metric("no_table", table=""))
        with pytest.raises(ValueError, match="table"):
            eng.query_metrics(["no_table"])

    def test_with_sql_template(self, engine: MetricFlowEngine):
        eng = MetricFlowEngine()
        eng.add(_make_metric(
            "custom_metric",
            formula="SUM(amount)",
            table="orders",
            sql_template="SELECT {metric} AS val, {dimensions} FROM {table} WHERE {filters}",
        ))
        sql = eng.query_metrics(
            ["custom_metric"],
            dimensions=["region"],
            filters={"status": "active"},
        )
        assert "SUM(amount)" in sql
        assert "region" in sql
        assert "active" in sql

    def test_sql_structure_complete(self, engine: MetricFlowEngine):
        """Verify the generated SQL has all expected clauses."""
        sql = engine.query_metrics(
            ["revenue"],
            dimensions=["region"],
            filters={"region": "US"},
            order_by=["revenue"],
            limit=5,
            domain="ecommerce",
        )
        assert sql.upper().startswith("SELECT")
        assert "FROM" in sql.upper()
        assert "WHERE" in sql.upper()
        assert "GROUP BY" in sql.upper()
        assert "ORDER BY" in sql.upper()
        assert "LIMIT" in sql.upper()

    def test_time_column_generates_date_trunc(self, engine: MetricFlowEngine):
        """When time_column is set and time_grain specified, DATE_TRUNC appears."""
        sql = engine.query_metrics(
            ["revenue"],
            time_grain="week",
            domain="ecommerce",
        )
        assert "DATE_TRUNC" in sql
        assert "week" in sql
        assert "created_at" in sql


# ── Resolve metric tests ───────────────────────────────────────────────


class TestResolveMetric:
    @pytest.fixture
    def engine(self) -> MetricFlowEngine:
        eng = MetricFlowEngine()
        eng.add(_make_metric("revenue", aliases=["rev"]), domain="ecommerce")
        return eng

    def test_resolve_exact(self, engine: MetricFlowEngine):
        m = engine.resolve_metric("revenue", domain="ecommerce")
        assert m is not None
        assert m.name == "revenue"

    def test_resolve_by_alias(self, engine: MetricFlowEngine):
        m = engine.resolve_metric("rev", domain="ecommerce")
        assert m is not None
        assert m.name == "revenue"

    def test_resolve_not_found(self, engine: MetricFlowEngine):
        assert engine.resolve_metric("nonexistent") is None


# ── Load from domains tests ─────────────────────────────────────────────


class TestLoadFromDomains:
    def test_load_glossary_derived_metrics(self, tmp_path):
        """Glossary terms with derived_column mappings become metrics."""
        from app.knowledge.domain_manager import DomainManager

        d = tmp_path / "domains"
        d.mkdir()
        (d / "ecommerce.yml").write_text("""
name: ecommerce
label:
  zh: "电商"
description:
  zh: "电商领域"
databases: []
timezone: "Asia/Shanghai"
currency: "CNY"
keywords: []
glossary:
  - term: "订单金额"
    term_en: "order_amount"
    description: "订单金额"
    mapping:
      expression: "SUM(orders.amount)"
      type: derived_column
      table: orders
  - term: "客单价"
    term_en: "aov"
    description: "客单价"
    mapping:
      expression: "AVG(orders.amount)"
      type: derived_column
      table: orders
  - term: "活跃用户"
    term_en: "active_user"
    description: "活跃用户"
    mapping:
      expression: "COUNT(DISTINCT user_id)"
      type: filter_condition  # not derived_column → skipped
rules: []
""", encoding="utf-8")

        dm = DomainManager(str(d))
        engine = MetricFlowEngine(domain_manager=dm)
        loaded = engine.load_from_domains()
        assert loaded == 2  # only derived_column ones

        # Check the derived metrics
        oa = engine.get("order_amount", domain="ecommerce")
        assert oa is not None
        assert oa.formula == "SUM(orders.amount)"
        assert oa.table == "orders"
        # Check alias → term name
        assert "订单金额" in oa.aliases

        aov = engine.get("aov", domain="ecommerce")
        assert aov is not None
        assert aov.formula == "AVG(orders.amount)"

    def test_load_no_domain_manager(self):
        engine = MetricFlowEngine()
        assert engine.load_from_domains() == 0


# ── MetricSQL tests ────────────────────────────────────────────────────


class TestMetricSQL:
    def test_construction_defaults(self):
        ms = MetricSQL(metric_name="revenue", sql="SELECT 1")
        assert ms.metric_name == "revenue"
        assert ms.sql == "SELECT 1"
        assert ms.dialect == ""
        assert ms.table == ""
        assert ms.columns == []
        assert ms.params == {}

    def test_construction_full(self):
        ms = MetricSQL(
            metric_name="rev",
            sql="SELECT SUM(amount) FROM orders",
            dialect="postgres",
            table="orders",
            columns=["amount"],
            params={"limit": 10},
        )
        assert ms.dialect == "postgres"
        assert ms.table == "orders"
        assert ms.columns == ["amount"]
        assert ms.params == {"limit": 10}


# ── Edge cases ─────────────────────────────────────────────────────────


class TestMetricFlowEngineEdgeCases:
    def test_empty_engine(self):
        engine = MetricFlowEngine()
        assert engine.metric_count == 0
        assert engine.domain_count == 0
        assert engine.get("any") is None
        assert engine.list_all() == []

    def test_same_name_different_domains(self):
        engine = MetricFlowEngine()
        ecom = _make_metric("revenue", table="orders", domain="ecommerce")
        fin = _make_metric("revenue", table="transactions", domain="finance")
        engine.add(ecom, domain="ecommerce")
        engine.add(fin, domain="finance")
        assert engine.metric_count == 2
        assert engine.get("revenue", domain="ecommerce").table == "orders"
        assert engine.get("revenue", domain="finance").table == "transactions"

    def test_query_metrics_dialect_translation(self):
        """When dialect is set, SQL should pass through _translate_dialect."""
        engine = MetricFlowEngine()
        engine.add(_make_metric("revenue", table="orders"))
        # Without a DialectAdapter, passing a dialect simply calls sqlglot
        sql = engine.query_metrics(["revenue"], dialect="postgres")
        assert "SELECT" in sql.upper()
        assert "FROM" in sql.upper()

    def test_query_metrics_no_dimensions_no_timecol(self):
        """Without dimensions or time_column, only metric expressions in SELECT."""
        engine = MetricFlowEngine()
        engine.add(_make_metric("cnt", formula="COUNT(*)", table="users"))
        sql = engine.query_metrics(["cnt"])
        assert "COUNT(*)" in sql
        assert "FROM users" in sql
        # No GROUP BY since there are no dimensions
        assert "GROUP BY" not in sql

    def test_query_metrics_multiple_filters(self):
        engine = MetricFlowEngine()
        engine.add(_make_metric("revenue", table="orders"))
        sql = engine.query_metrics(
            ["revenue"],
            filters={"status": "paid", "region": "CN"},
        )
        assert "status" in sql
        assert "paid" in sql
        assert "region" in sql
        assert "CN" in sql
        # Two filter conditions joined by AND
        assert sql.count("AND") >= 1

    def test_load_from_domains_handles_empty_glossary(self, tmp_path):
        from app.knowledge.domain_manager import DomainManager

        d = tmp_path / "domains"
        d.mkdir()
        (d / "empty.yml").write_text("""
name: empty_domain
label:
  zh: "空"
databases: []
keywords: []
glossary: []
rules: []
""", encoding="utf-8")

        dm = DomainManager(str(d))
        engine = MetricFlowEngine(domain_manager=dm)
        loaded = engine.load_from_domains()
        assert loaded == 0
