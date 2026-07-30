"""Tests for MetricResolveNode — MetricFlow engine wrapped as a Harness Node.

Covers result model, construction, registry integration, SQL generation,
error handling, update_context convenience keys, and edge cases.
"""

from __future__ import annotations

import pytest

from app.knowledge.metricflow_layer import MetricDef, MetricFlowEngine
from app.nodes.base import NodeInput, NodeOutput
from app.nodes.metric_resolve import MetricResolveNode, MetricResolveOutput

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


def _make_engine() -> MetricFlowEngine:
    """Create a pre-populated MetricFlowEngine with ecommerce metrics."""
    eng = MetricFlowEngine()
    eng.add(
        _make_metric(
            "revenue", formula="SUM(amount)", table="orders",
            dimensions=["region", "date"], time_column="created_at",
        ),
        domain="ecommerce",
    )
    eng.add(
        _make_metric(
            "order_count", formula="COUNT(*)", table="orders",
            dimensions=["region"],
        ),
        domain="ecommerce",
    )
    eng.add(
        _make_metric(
            "aov", formula="AVG(amount)", table="orders",
            dimensions=["date"], aggregation="avg",
        ),
        domain="ecommerce",
    )
    eng.add(
        _make_metric(
            "nav", formula="SUM(value)", table="assets",
            dimensions=["portfolio"], time_grain="month",
            time_column="as_of_date",
        ),
        domain="finance",
    )
    return eng


# ── MetricResolveOutput tests ──────────────────────────────────────────


class TestMetricResolveOutput:
    def test_defaults(self):
        out = MetricResolveOutput()
        assert out.metrics == []
        assert out.sql == ""
        assert out.dialect == ""
        assert out.table == ""
        assert out.columns == []
        assert out.params == {}
        assert out.errors == []
        assert out.has_sql is False

    def test_has_sql_true(self):
        out = MetricResolveOutput(sql="SELECT 1")
        assert out.has_sql is True

    def test_has_sql_false(self):
        out = MetricResolveOutput()
        assert out.has_sql is False
        out2 = MetricResolveOutput(sql="")
        assert out2.has_sql is False

    def test_full_construction(self):
        out = MetricResolveOutput(
            metrics=["revenue", "order_count"],
            sql=(
                "SELECT region, SUM(amount) AS revenue, COUNT(*) AS order_count "
                "FROM orders GROUP BY 1"
            ),
            dialect="postgres",
            table="orders",
            columns=["region", "revenue", "order_count"],
            params={"region": "CN"},
            errors=[],
        )
        assert len(out.metrics) == 2
        assert "SELECT" in out.sql
        assert out.dialect == "postgres"
        assert out.table == "orders"
        assert len(out.columns) == 3

    def test_errors_stored(self):
        out = MetricResolveOutput(errors=["Metric 'xyz' not found"])
        assert len(out.errors) == 1
        assert "xyz" in out.errors[0]


# ── Construction tests ─────────────────────────────────────────────────


class TestMetricResolveNodeConstruction:
    def test_default_construction(self):
        node = MetricResolveNode()
        assert node.name == "metric_resolve"
        assert node.has_engine is False

    def test_with_engine(self):
        engine = MetricFlowEngine()
        node = MetricResolveNode(engine=engine)
        assert node.has_engine is True

    def test_with_populated_engine(self):
        engine = _make_engine()
        node = MetricResolveNode(engine=engine)
        assert node.has_engine is True
        assert engine.metric_count == 4


# ── Registry tests ─────────────────────────────────────────────────────


class TestMetricResolveNodeRegistry:
    @pytest.fixture(autouse=True)
    def _clear_registry(self):
        from app.nodes.registry import NodeRegistry
        NodeRegistry._nodes.clear()
        yield
        NodeRegistry._nodes.clear()

    def test_can_register(self):
        from app.nodes.registry import NodeRegistry
        registry = NodeRegistry()
        node = MetricResolveNode()
        registry.register(node)
        assert "metric_resolve" in registry
        assert registry.get("metric_resolve") is node

    def test_duplicate_raises(self):
        from app.nodes.registry import NodeRegistry
        registry = NodeRegistry()
        registry.register(MetricResolveNode())
        with pytest.raises(ValueError, match="already registered"):
            registry.register(MetricResolveNode())


# ── Error handling tests ───────────────────────────────────────────────


class TestMetricResolveNodeErrors:
    @pytest.mark.asyncio
    async def test_no_engine_configured(self):
        """Node without engine should return clear error."""
        node = MetricResolveNode()
        output = await node.execute(NodeInput(
            query_text="revenue by region",
            config={"metrics": ["revenue"]},
        ))
        assert output.errors == ["No MetricFlowEngine configured"]
        assert output.metadata["status"] == "no_engine"
        result: MetricResolveOutput = output.result
        assert result.has_sql is False

    @pytest.mark.asyncio
    async def test_no_metrics_specified(self):
        """Empty metrics list should return error."""
        engine = _make_engine()
        node = MetricResolveNode(engine=engine)
        output = await node.execute(NodeInput(
            query_text="some query",
            config={},
        ))
        assert output.errors == ["At least one metric name is required"]
        assert output.metadata["status"] == "no_metrics"

    @pytest.mark.asyncio
    async def test_unknown_metric(self):
        """Unknown metric name should return error."""
        engine = _make_engine()
        node = MetricResolveNode(engine=engine)
        output = await node.execute(NodeInput(
            query_text="unknown metric query",
            config={"metrics": ["nonexistent_metric"], "domain": "ecommerce"},
        ))
        assert output.metadata["status"] == "metric_not_found"
        assert "nonexistent_metric" in output.errors[0]

    @pytest.mark.asyncio
    async def test_missing_table(self):
        """Metric without a table should raise ValueError."""
        engine = MetricFlowEngine()
        engine.add(_make_metric("no_table", table=""))
        node = MetricResolveNode(engine=engine)
        output = await node.execute(NodeInput(
            query_text="query",
            config={"metrics": ["no_table"]},
        ))
        assert output.metadata["status"] == "invalid_config"
        assert "table" in output.errors[0].lower()

    @pytest.mark.asyncio
    async def test_engine_from_context(self):
        """Engine can be injected via input.context."""
        engine = _make_engine()
        node = MetricResolveNode()  # no constructor engine
        output = await node.execute(NodeInput(
            query_text="revenue query",
            config={"metrics": ["revenue"], "domain": "ecommerce"},
            context={"metric_engine": engine},
        ))
        assert output.metadata["status"] == "success"
        result: MetricResolveOutput = output.result
        assert "SUM(amount)" in result.sql

    @pytest.mark.asyncio
    async def test_constructor_engine_takes_priority(self):
        """Constructor engine should take priority over context engine."""
        ctor_engine = _make_engine()
        ctx_engine = MetricFlowEngine()  # different engine
        ctx_engine.add(_make_metric("other", table="other_tbl"))
        node = MetricResolveNode(engine=ctor_engine)
        output = await node.execute(NodeInput(
            query_text="revenue query",
            config={"metrics": ["revenue"], "domain": "ecommerce"},
            context={"metric_engine": ctx_engine},
        ))
        assert output.metadata["status"] == "success"
        result: MetricResolveOutput = output.result
        assert "orders" in result.sql  # from ctor_engine


# ── Integration tests ──────────────────────────────────────────────────


class TestMetricResolveNode:
    @pytest.fixture
    def engine(self) -> MetricFlowEngine:
        return _make_engine()

    @pytest.fixture
    def node(self, engine: MetricFlowEngine) -> MetricResolveNode:
        return MetricResolveNode(engine=engine)

    # ── Basic SQL generation ─────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_single_metric_no_dimensions(self, node: MetricResolveNode):
        output = await node.execute(NodeInput(
            query_text="revenue",
            config={"metrics": ["revenue"], "domain": "ecommerce"},
        ))
        assert output.metadata["status"] == "success"
        result: MetricResolveOutput = output.result
        assert "SUM(amount)" in result.sql
        assert "FROM orders" in result.sql
        assert result.has_sql is True
        assert result.table == "orders"

    @pytest.mark.asyncio
    async def test_single_metric_with_dimensions(self, node: MetricResolveNode):
        output = await node.execute(NodeInput(
            query_text="revenue by region",
            config={
                "metrics": ["revenue"],
                "dimensions": ["region"],
                "domain": "ecommerce",
            },
        ))
        assert output.metadata["status"] == "success"
        result: MetricResolveOutput = output.result
        assert "SUM(amount)" in result.sql
        assert "region" in result.sql
        assert "GROUP BY" in result.sql
        assert output.metadata["dimension_count"] == 1

    @pytest.mark.asyncio
    async def test_multiple_metrics(self, node: MetricResolveNode):
        output = await node.execute(NodeInput(
            query_text="revenue and order count by region",
            config={
                "metrics": ["revenue", "order_count"],
                "dimensions": ["region"],
                "domain": "ecommerce",
            },
        ))
        assert output.metadata["status"] == "success"
        result: MetricResolveOutput = output.result
        assert "SUM(amount)" in result.sql
        assert "COUNT(*)" in result.sql
        assert len(result.metrics) == 2

    @pytest.mark.asyncio
    async def test_time_grain_override(self, node: MetricResolveNode):
        output = await node.execute(NodeInput(
            query_text="monthly revenue",
            config={
                "metrics": ["revenue"],
                "time_grain": "month",
                "domain": "ecommerce",
            },
        ))
        assert output.metadata["status"] == "success"
        result: MetricResolveOutput = output.result
        assert "DATE_TRUNC" in result.sql
        assert "month" in result.sql.lower()
        assert output.metadata["has_time_grain"] is True

    @pytest.mark.asyncio
    async def test_with_filters(self, node: MetricResolveNode):
        output = await node.execute(NodeInput(
            query_text="revenue in CN",
            config={
                "metrics": ["revenue"],
                "dimensions": ["region"],
                "filters": {"region": "CN"},
                "domain": "ecommerce",
            },
        ))
        assert output.metadata["status"] == "success"
        result: MetricResolveOutput = output.result
        assert "WHERE" in result.sql
        assert "CN" in result.sql
        assert result.params == {"region": "CN"}

    @pytest.mark.asyncio
    async def test_with_order_by(self, node: MetricResolveNode):
        output = await node.execute(NodeInput(
            query_text="revenue by region, ordered",
            config={
                "metrics": ["revenue"],
                "dimensions": ["region"],
                "order_by": ["revenue"],
                "domain": "ecommerce",
            },
        ))
        assert output.metadata["status"] == "success"
        result: MetricResolveOutput = output.result
        assert "ORDER BY" in result.sql

    @pytest.mark.asyncio
    async def test_with_limit(self, node: MetricResolveNode):
        output = await node.execute(NodeInput(
            query_text="top 10 revenue",
            config={
                "metrics": ["revenue"],
                "dimensions": ["region"],
                "limit": 10,
                "domain": "ecommerce",
            },
        ))
        assert output.metadata["status"] == "success"
        result: MetricResolveOutput = output.result
        assert "LIMIT 10" in result.sql

    @pytest.mark.asyncio
    async def test_cross_domain_metric(self, node: MetricResolveNode):
        """Finance domain metric should resolve independently."""
        output = await node.execute(NodeInput(
            query_text="nav by portfolio",
            config={
                "metrics": ["nav"],
                "dimensions": ["portfolio"],
                "domain": "finance",
            },
        ))
        assert output.metadata["status"] == "success"
        result: MetricResolveOutput = output.result
        assert "SUM(value)" in result.sql
        assert "FROM assets" in result.sql

    @pytest.mark.asyncio
    async def test_dialect_translation(self, node: MetricResolveNode):
        output = await node.execute(NodeInput(
            query_text="revenue for postgres",
            config={
                "metrics": ["revenue"],
                "dialect": "postgres",
                "domain": "ecommerce",
            },
        ))
        assert output.metadata["status"] == "success"
        result: MetricResolveOutput = output.result
        assert result.dialect == "postgres"
        assert "SELECT" in result.sql.upper()

    @pytest.mark.asyncio
    async def test_sql_template_metric(self, node: MetricResolveNode):
        """Metric with a custom SQL template should use it."""
        engine = MetricFlowEngine()
        engine.add(_make_metric(
            "custom_metric",
            formula="SUM(amount)",
            table="orders",
            sql_template="SELECT {metric} AS val, {dimensions} FROM {table} WHERE {filters}",
        ))
        node2 = MetricResolveNode(engine=engine)
        output = await node2.execute(NodeInput(
            query_text="custom query",
            config={
                "metrics": ["custom_metric"],
                "dimensions": ["region"],
                "filters": {"status": "active"},
            },
        ))
        assert output.metadata["status"] == "success"
        result: MetricResolveOutput = output.result
        assert "SUM(amount)" in result.sql
        assert "region" in result.sql
        assert "active" in result.sql

    @pytest.mark.asyncio
    async def test_all_clauses_present(self, node: MetricResolveNode):
        """Full query should have all SQL clauses."""
        output = await node.execute(NodeInput(
            query_text="full query",
            config={
                "metrics": ["revenue"],
                "dimensions": ["region"],
                "filters": {"region": "US"},
                "order_by": ["revenue"],
                "limit": 5,
                "domain": "ecommerce",
            },
        ))
        result: MetricResolveOutput = output.result
        assert result.sql.upper().startswith("SELECT")
        assert "FROM" in result.sql.upper()
        assert "WHERE" in result.sql.upper()
        assert "GROUP BY" in result.sql.upper()
        assert "ORDER BY" in result.sql.upper()
        assert "LIMIT" in result.sql.upper()

    # ── Metadata ─────────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_metadata_counts(self, node: MetricResolveNode):
        output = await node.execute(NodeInput(
            query_text="revenue and orders by region monthly",
            config={
                "metrics": ["revenue", "order_count"],
                "dimensions": ["region"],
                "time_grain": "month",
                "domain": "ecommerce",
            },
        ))
        assert output.metadata["metric_count"] == 2
        assert output.metadata["dimension_count"] == 1
        assert output.metadata["has_time_grain"] is True
        assert output.metadata["dialect"] == "ansi"

    @pytest.mark.asyncio
    async def test_metadata_dialect(self, node: MetricResolveNode):
        output = await node.execute(NodeInput(
            query_text="revenue",
            config={
                "metrics": ["revenue"],
                "dialect": "postgres",
                "domain": "ecommerce",
            },
        ))
        assert output.metadata["dialect"] == "postgres"

    # ── update_context ────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_update_context_sets_convenience_keys(self, node: MetricResolveNode):
        output = await node.execute(NodeInput(
            query_text="revenue by region",
            config={
                "metrics": ["revenue"],
                "dimensions": ["region"],
                "domain": "ecommerce",
            },
        ))
        ctx = await node.update_context(output, {})

        assert "metric_sql" in ctx
        assert "SELECT" in ctx["metric_sql"]
        assert "metric_resolve_result" in ctx
        assert "sql" in ctx
        assert ctx["sql"] == ctx["metric_sql"]
        assert "resolved_metrics" in ctx
        assert "revenue" in ctx["resolved_metrics"]

    @pytest.mark.asyncio
    async def test_update_context_preserves_existing_keys(self, node: MetricResolveNode):
        output = await node.execute(NodeInput(
            query_text="revenue",
            config={"metrics": ["revenue"], "domain": "ecommerce"},
        ))
        ctx = await node.update_context(output, {"existing": "keep", "sql": "original"})
        assert ctx["existing"] == "keep"
        # Existing 'sql' should NOT be overwritten (setdefault behavior)
        assert ctx["sql"] == "original"

    @pytest.mark.asyncio
    async def test_update_context_no_sql_on_error(self):
        engine = _make_engine()
        node = MetricResolveNode(engine=engine)
        output = await node.execute(NodeInput(
            query_text="bad query",
            config={"metrics": ["nonexistent"]},
        ))
        ctx = await node.update_context(output, {"existing": "keep"})
        assert "sql" not in ctx  # no SQL generated
        assert "metric_resolve_result" in ctx

    @pytest.mark.asyncio
    async def test_update_context_no_result(self, node: MetricResolveNode):
        """If output.result is None, update_context should not crash."""
        output = NodeOutput(result=None)
        ctx = await node.update_context(output, {"a": 1})
        assert ctx["a"] == 1

    # ── _extract_table ────────────────────────────────────────────────

    def test_extract_table_simple(self):
        assert MetricResolveNode._extract_table(
            "SELECT * FROM orders"
        ) == "orders"

    def test_extract_table_quoted(self):
        assert MetricResolveNode._extract_table(
            'SELECT * FROM "order items"'
        ) == "order items"

    def test_extract_table_no_match(self):
        assert MetricResolveNode._extract_table("SELECT 1") == ""

    def test_extract_table_case_insensitive(self):
        assert MetricResolveNode._extract_table(
            "SELECT * from orders WHERE id=1"
        ) == "orders"

    # ── _extract_columns ──────────────────────────────────────────────

    def test_extract_columns_basic(self):
        cols = MetricResolveNode._extract_columns(
            ["revenue"], ["region", "date"], "month",
        )
        assert cols == ["region", "date", "revenue"]

    def test_extract_columns_no_dimensions(self):
        cols = MetricResolveNode._extract_columns(
            ["revenue", "order_count"], None, "",
        )
        assert cols == ["revenue", "order_count"]

    def test_extract_columns_empty(self):
        cols = MetricResolveNode._extract_columns([], None, "")
        assert cols == []


# ── Edge cases ─────────────────────────────────────────────────────────


class TestMetricResolveNodeEdgeCases:
    @pytest.mark.asyncio
    async def test_empty_string_metric_name(self):
        """Empty string in metrics list should be treated as unknown."""
        engine = _make_engine()
        node = MetricResolveNode(engine=engine)
        output = await node.execute(NodeInput(
            query_text="query",
            config={"metrics": [""]},
        ))
        assert output.metadata["status"] == "metric_not_found"

    @pytest.mark.asyncio
    async def test_no_dimensions_no_filters(self, node: MetricResolveNode | None = None):
        """Minimal config — just metric names."""
        if node is None:
            engine = _make_engine()
            node = MetricResolveNode(engine=engine)
        output = await node.execute(NodeInput(
            query_text="revenue",
            config={"metrics": ["revenue"], "domain": "ecommerce"},
        ))
        assert output.metadata["status"] == "success"
        result: MetricResolveOutput = output.result
        assert "SELECT" in result.sql
        # No GROUP BY when no dimensions and no time_column needed
        assert "FROM orders" in result.sql

    @pytest.mark.asyncio
    async def test_alias_resolution(self):
        """Metrics should be resolvable by alias."""
        engine = MetricFlowEngine()
        engine.add(_make_metric(
            "revenue", formula="SUM(amount)", table="orders",
            aliases=["rev", "sales"],
        ))
        node = MetricResolveNode(engine=engine)
        output = await node.execute(NodeInput(
            query_text="rev query",
            config={"metrics": ["rev"]},
        ))
        assert output.metadata["status"] == "success"
        result: MetricResolveOutput = output.result
        assert "SUM(amount)" in result.sql

    @pytest.mark.asyncio
    async def test_case_insensitive_metric_name(self):
        """Metric names should be case-insensitive."""
        engine = MetricFlowEngine()
        engine.add(_make_metric("Revenue", formula="SUM(amount)", table="orders"))
        node = MetricResolveNode(engine=engine)
        output = await node.execute(NodeInput(
            query_text="REVENUE query",
            config={"metrics": ["revenue"]},
        ))
        assert output.metadata["status"] == "success"
        result: MetricResolveOutput = output.result
        assert "SUM(amount)" in result.sql

    @pytest.mark.asyncio
    async def test_multiple_filters(self):
        engine = _make_engine()
        node = MetricResolveNode(engine=engine)
        output = await node.execute(NodeInput(
            query_text="paid orders in CN",
            config={
                "metrics": ["revenue"],
                "filters": {"status": "paid", "region": "CN"},
                "domain": "ecommerce",
            },
        ))
        assert output.metadata["status"] == "success"
        result: MetricResolveOutput = output.result
        assert result.sql.count("AND") >= 1

    @pytest.mark.asyncio
    async def test_zero_limit_means_no_limit(self):
        engine = _make_engine()
        node = MetricResolveNode(engine=engine)
        output = await node.execute(NodeInput(
            query_text="revenue",
            config={
                "metrics": ["revenue"],
                "limit": 0,
                "domain": "ecommerce",
            },
        ))
        result: MetricResolveOutput = output.result
        assert "LIMIT" not in result.sql

    @pytest.mark.asyncio
    async def test_long_query_text(self):
        """Node should work with any query_text (it's not used for resolution)."""
        engine = _make_engine()
        node = MetricResolveNode(engine=engine)
        output = await node.execute(NodeInput(
            query_text="a" * 1000,
            config={"metrics": ["revenue"], "domain": "ecommerce"},
        ))
        assert output.metadata["status"] == "success"
