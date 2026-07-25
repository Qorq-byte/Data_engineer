r"""MetricResolveNode — wrap MetricFlowEngine as a Harness Node.

See implementation-plan §4.8.6 and progress-log §3.14 for the full specification.

This node bridges the MetricFlow semantic layer into the WorkflowRunner
pipeline. It resolves named business metrics to complete SQL SELECT
statements and injects the generated SQL into the shared context for
downstream nodes (GenerateSQLNode, ExecuteSQLNode, etc.).

The node reads its parameters from ``input.config``:

=============  ============================================================
Key             Description
=============  ============================================================
``metrics``      List of metric names to resolve (required).
``dimensions``   Optional drill-down dimension column names.
``time_grain``   Time granularity override (``hour|day|week|month|quarter|year``).
``filters``      ``{column: value}`` dict for WHERE equality filters.
``domain``       Domain scope for metric lookup.
``dialect``      Target SQL dialect for translation.
``order_by``     Optional ORDER BY columns.
``limit``        Optional LIMIT value.
=============  ============================================================

Usage::

    from app.knowledge.metricflow_layer import MetricFlowEngine
    from app.nodes.metric_resolve import MetricResolveNode

    engine = MetricFlowEngine()
    engine.add(MetricDef(name="revenue", formula="SUM(amount)", table="orders"))

    node = MetricResolveNode(engine=engine)
    output = await node.execute(NodeInput(
        query_text="monthly revenue by region",
        config={
            "metrics": ["revenue"],
            "dimensions": ["region"],
            "time_grain": "month",
            "domain": "ecommerce",
        },
    ))
    # output.result.sql → SELECT DATE_TRUNC('month', ...) ... FROM orders ...
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.nodes.base import BaseNode, NodeInput, NodeOutput

# ── Result model ───────────────────────────────────────────────────────


@dataclass
class MetricResolveOutput:
    """Structured output from MetricResolveNode.

    Attributes:
        metrics: Resolved metric names (in order).
        sql: Generated SQL SELECT statement.
        dialect: Target dialect (empty = ANSI/untranslated).
        table: Primary source table.
        columns: Output column names in SELECT order.
        params: Query parameters for parameterised execution.
        errors: Non-fatal warnings or info messages.
    """

    metrics: list[str] = field(default_factory=list)
    sql: str = ""
    dialect: str = ""
    table: str = ""
    columns: list[str] = field(default_factory=list)
    params: dict[str, Any] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)

    @property
    def has_sql(self) -> bool:
        """``True`` if SQL was successfully generated."""
        return bool(self.sql)


# ── Node ───────────────────────────────────────────────────────────────


class MetricResolveNode(BaseNode):
    """Resolve named business metrics to SQL via MetricFlowEngine.

    Wraps :class:`~app.knowledge.metricflow_layer.MetricFlowEngine` so it
    fits the Harness lifecycle (setup_input → execute → update_context).

    This node is **not** an ``AgenticNode`` — metric resolution is purely
    deterministic (lookup + SQL assembly), with no LLM calls.

    Input config keys:
        ``metrics``: ``list[str]`` — metric names (required)
        ``dimensions``: ``list[str]`` — GROUP BY columns
        ``time_grain``: ``str`` — hour|day|week|month|quarter|year
        ``filters``: ``dict[str, str]`` — WHERE equality conditions
        ``domain``: ``str`` — domain scope for metric lookup
        ``dialect``: ``str`` — target SQL dialect
        ``order_by``: ``list[str]`` — ORDER BY columns
        ``limit``: ``int`` — LIMIT clause

    Output context keys:
        ``metric_sql`` — generated SQL string (for downstream nodes)
        ``metric_resolve_result`` — ``MetricResolveOutput``
    """

    name = "metric_resolve"
    description = (
        "Resolve named business metrics to dialect-aware SQL SELECT "
        "statements via the MetricFlow semantic layer"
    )

    def __init__(self, engine: Any = None) -> None:
        """Create a MetricResolveNode with an optional pre-configured engine.

        Args:
            engine: ``MetricFlowEngine`` instance (or ``None`` to require
                    injection via ``input.context["metric_engine"]``).
        """
        super().__init__()
        self._engine = engine

    # ── Properties ─────────────────────────────────────────────────────

    @property
    def has_engine(self) -> bool:
        """``True`` if a MetricFlowEngine is configured."""
        return self._engine is not None

    # ── Lifecycle ──────────────────────────────────────────────────────

    async def execute(self, input: NodeInput) -> NodeOutput:
        """Resolve metrics and generate SQL.

        Args:
            input: ``NodeInput`` whose ``config`` carries metric names,
                   dimensions, filters, etc.

        Returns:
            ``NodeOutput`` with ``MetricResolveOutput`` as ``result`` and
            ``metric_sql`` + ``metric_resolve_result`` in ``context``.
        """
        config = input.config

        # Resolve engine: constructor arg takes priority, then context
        engine = self._engine
        if engine is None:
            engine = input.context.get("metric_engine")
        if engine is None:
            return NodeOutput(
                result=MetricResolveOutput(
                    errors=["No MetricFlowEngine configured"],
                ),
                errors=["No MetricFlowEngine configured"],
                metadata={"status": "no_engine"},
            )

        # Extract parameters
        metrics: list[str] = config.get("metrics", [])
        if not metrics:
            return NodeOutput(
                result=MetricResolveOutput(
                    errors=["At least one metric name is required"],
                ),
                errors=["At least one metric name is required"],
                metadata={"status": "no_metrics"},
            )

        dimensions: list[str] | None = config.get("dimensions")
        time_grain: str = config.get("time_grain", "")
        filters: dict[str, str] | None = config.get("filters")
        domain: str | None = config.get("domain")
        dialect: str = config.get("dialect", "")
        order_by: list[str] | None = config.get("order_by")
        limit: int = config.get("limit", 0)

        # Call the engine
        try:
            sql = engine.query_metrics(
                metrics,
                dimensions=dimensions,
                time_grain=time_grain,
                filters=filters,
                domain=domain,
                dialect=dialect,
                order_by=order_by,
                limit=limit,
            )
        except KeyError as exc:
            return NodeOutput(
                result=MetricResolveOutput(
                    metrics=metrics,
                    errors=[str(exc)],
                ),
                errors=[str(exc)],
                metadata={"status": "metric_not_found", "metric": str(exc)},
            )
        except ValueError as exc:
            return NodeOutput(
                result=MetricResolveOutput(
                    metrics=metrics,
                    errors=[str(exc)],
                ),
                errors=[str(exc)],
                metadata={"status": "invalid_config", "error": str(exc)},
            )
        except Exception as exc:
            return NodeOutput(
                result=MetricResolveOutput(
                    metrics=metrics,
                    errors=[f"Metric resolution failed: {exc}"],
                ),
                errors=[f"Metric resolution failed: {exc}"],
                metadata={"status": "resolution_error"},
            )

        # Build output
        output = MetricResolveOutput(
            metrics=metrics,
            sql=sql,
            dialect=dialect,
            table=self._extract_table(sql),
            columns=self._extract_columns(metrics, dimensions, time_grain),
            params=filters or {},
        )

        return NodeOutput(
            result=output,
            metadata={
                "status": "success",
                "metric_count": len(metrics),
                "dimension_count": len(dimensions or []),
                "has_time_grain": bool(time_grain),
                "dialect": dialect or "ansi",
            },
            context={
                "metric_sql": sql,
                "metric_resolve_result": output,
            },
        )

    async def update_context(
        self, output: NodeOutput, shared_context: dict[str, Any]
    ) -> dict[str, Any]:
        """Merge metric resolution results into the shared workflow context.

        In addition to ``metric_sql`` and ``metric_resolve_result``,
        convenience keys are set for the most common downstream consumers:
          - ``sql`` — the generated SQL (overwritable by later nodes if needed)
          - ``resolved_metrics`` — list of resolved metric names
        """
        merged = {**shared_context}
        if output.context:
            merged.update(output.context)

        result: MetricResolveOutput | None = output.result
        if result is None:
            return merged

        # Always expose the full result
        merged.setdefault("metric_resolve_result", result)

        # Convenience: SQL string for ExecuteSQLNode / GenerateSQLNode
        if result.sql:
            merged.setdefault("sql", result.sql)

        # Convenience: resolved metric names
        if result.metrics:
            merged.setdefault("resolved_metrics", list(result.metrics))

        return merged

    # ── Helpers ─────────────────────────────────────────────────────────

    @staticmethod
    def _extract_table(sql: str) -> str:
        """Extract the primary table name from a generated SQL string."""
        import re

        match = re.search(
            r'\bFROM\s+(?:"([^"]+)"|(\w+))', sql, re.IGNORECASE
        )
        if match:
            return match.group(1) or match.group(2)
        return ""

    @staticmethod
    def _extract_columns(
        metrics: list[str],
        dimensions: list[str] | None,
        time_grain: str,
    ) -> list[str]:
        """Build the expected output column list."""
        cols: list[str] = []
        if dimensions:
            cols.extend(dimensions)
        cols.extend(metrics)
        return cols
