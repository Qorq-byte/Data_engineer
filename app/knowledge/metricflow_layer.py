r"""MetricFlow Engine — lightweight metric semantic layer for NL→SQL generation.

See implementation-plan §4.8.6 and progress-log §3.13 for the full specification.

The metric flow engine provides a lightweight, declarative semantic layer for
business metrics (KPIs).  It maps metric names to SQL expressions and generates
complete, dialect-aware ``SELECT`` statements via :meth:`MetricFlowEngine.query_metrics`.

**Design philosophy** — this is NOT a wrapper around the heavy ``metricflow``
library from dbt Labs.  Instead it is a minimal, standalone implementation
focused on the MVP use case: resolving named metrics to SQL fragments,
assembling complete queries with dimensions / time-grain / filters, and
delegating dialect translation to :class:`~app.db.dialect_adapter.DialectAdapter`.

**Integration points**::

    MetricResolveNode (3.14)  ──►  MetricFlowEngine.query_metrics()
    DomainManager             ──►  MetricFlowEngine.load_from_domains()
    DialectAdapter            ──►  MetricFlowEngine._translate_dialect()

Usage::

    from app.knowledge.domain_manager import DomainManager
    from app.knowledge.metricflow_layer import MetricFlowEngine

    dm = DomainManager("app/config/domains")
    engine = MetricFlowEngine(domain_manager=dm)
    engine.load_from_domains()

    sql = engine.query_metrics(
        metrics=["revenue", "order_count"],
        dimensions=["region", "date"],
        time_grain="month",
        domain="ecommerce",
    )
    # → SELECT DATE_TRUNC('month', date) AS date_month, region,
    #     SUM(amount) AS revenue, COUNT(*) AS order_count
    #   FROM orders GROUP BY 1, 2 ORDER BY 1, 2
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# ── MetricDef ───────────────────────────────────────────────────────────


@dataclass
class MetricDef:
    """A declarative business metric (KPI) definition.

    Attributes:
        name: Unique metric identifier (e.g. ``"revenue"``, ``"aov"``).
        description: Human-readable description.
        formula: SQL expression (e.g. ``"SUM(amount)"``, ``"SUM(amount)/COUNT(*)"``).
        dimensions: List of allowed drill-down dimension column names.
        time_grain: Default time granularity — ``"hour"`` | ``"day"`` | ``"week"`` |
            ``"month"`` | ``"quarter"`` | ``"year"``.
        aggregation: Default aggregation function — ``"sum"`` | ``"count"`` |
            ``"avg"`` | ``"count_distinct"`` | ``"min"`` | ``"max"``.
        precision: Decimal places for numeric output.
        sql_template: Optional full-SQL template with ``{metric}``, ``{dimensions}``,
            ``{time_grain}``, ``{table}``, ``{filters}`` placeholders.
        domain: Owning domain identifier.
        table: Primary source table for the metric.
        time_column: Column used for time-grain date truncation.
        aliases: Alternative names for fuzzy matching.
        tags: Freeform tags.
    """

    name: str
    description: str = ""
    formula: str = ""
    dimensions: list[str] = field(default_factory=list)
    time_grain: str = "day"
    aggregation: str = "sum"
    precision: int = 2
    sql_template: str = ""
    domain: str = ""
    table: str = ""
    time_column: str = ""
    aliases: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)

    @property
    def has_formula(self) -> bool:
        """``True`` if this metric has a computable SQL formula."""
        return bool(self.formula)

    @property
    def has_template(self) -> bool:
        """``True`` if this metric uses a full SQL template."""
        return bool(self.sql_template)


# ── MetricSQL ───────────────────────────────────────────────────────────


@dataclass
class MetricSQL:
    """Resolved metric SQL with metadata.

    Attributes:
        metric_name: The metric name that produced this SQL.
        sql: The generated SQL string.
        dialect: Target database dialect (empty = ANSI/untranslated).
        table: Source table name.
        columns: Output column names in SELECT order.
        params: Query parameters for parameterised execution.
    """

    metric_name: str
    sql: str
    dialect: str = ""
    table: str = ""
    columns: list[str] = field(default_factory=list)
    params: dict[str, Any] = field(default_factory=dict)


# ── MetricFlowEngine ────────────────────────────────────────────────────


class MetricFlowEngine:
    """Lightweight metric semantic layer — resolve, query, and generate SQL.

    Manages :class:`MetricDef` objects (global + per-domain) and provides
    the core :meth:`query_metrics` entry-point for generating dialect-aware
    SQL from named metric references.

    Args:
        domain_manager: Optional ``DomainManager`` for loading metrics from
                        domain YAML ``metrics:`` blocks.
        dialect_adapter: Optional ``DialectAdapter`` for translating generated
                         SQL to a target database dialect.
    """

    def __init__(
        self,
        domain_manager: Any = None,
        dialect_adapter: Any = None,
    ) -> None:
        self._domain_manager = domain_manager
        self._dialect_adapter = dialect_adapter
        # Global metrics: name → MetricDef
        self._global: dict[str, MetricDef] = {}
        # Domain metrics: domain → (name → MetricDef)
        self._domain_metrics: dict[str, dict[str, MetricDef]] = {}

    # ── Properties ─────────────────────────────────────────────────────

    @property
    def metric_count(self) -> int:
        """Total number of registered metrics."""
        count = len(self._global)
        for metrics in self._domain_metrics.values():
            count += len(metrics)
        return count

    @property
    def domain_count(self) -> int:
        """Number of domains with registered metrics."""
        return len(self._domain_metrics)

    # ── Loading ────────────────────────────────────────────────────────

    def load_from_domains(self) -> int:
        """Load metric definitions from the attached ``DomainManager``.

        Reads metric definitions from domain YAML ``metrics:`` blocks
        (if the YAML schema supports them) plus auto-generates metrics
        from ``glossary:`` entries with ``type: derived_column`` mappings.

        Returns:
            Number of metrics loaded.
        """
        if self._domain_manager is None:
            return 0

        loaded = 0
        for name in self._domain_manager.list_all():
            domain = self._domain_manager.get(name)
            if domain is None:
                continue

            # 1. Explicit metrics block (future-proof — not yet in YAML schema)
            for metric_raw in getattr(domain, "metrics", []) or []:
                metric = MetricDef(
                    name=metric_raw.get("name", ""),
                    description=metric_raw.get("description", ""),
                    formula=metric_raw.get("formula", ""),
                    dimensions=metric_raw.get("dimensions", []),
                    time_grain=metric_raw.get("time_grain", "day"),
                    aggregation=metric_raw.get("aggregation", "sum"),
                    precision=metric_raw.get("precision", 2),
                    sql_template=metric_raw.get("sql_template", ""),
                    domain=name,
                    table=metric_raw.get("table", ""),
                    time_column=metric_raw.get("time_column", ""),
                    aliases=metric_raw.get("aliases", []),
                )
                if metric.name:
                    self.add(metric, domain=name)
                    loaded += 1

            # 2. Derive metrics from glossary terms with derived_column mappings
            for term in domain.glossary:
                if term.mapping and term.mapping.type == "derived_column":
                    metric = MetricDef(
                        name=term.term_en or term.term,
                        description=term.description,
                        formula=term.mapping.expression,
                        domain=name,
                        table=term.mapping.table or "",
                        time_grain="day",
                        aggregation="sum",
                        precision=term.mapping.precision or 2,
                        aliases=[term.term] if term.term_en else [],
                    )
                    self.add(metric, domain=name)
                    loaded += 1

        return loaded

    # ── CRUD ───────────────────────────────────────────────────────────

    def add(self, metric: MetricDef, domain: str | None = None) -> None:
        """Register a metric definition.

        Args:
            metric: The ``MetricDef`` to add.
            domain: Optional domain scope.  ``None`` = global.
        """
        if domain:
            if domain not in self._domain_metrics:
                self._domain_metrics[domain] = {}
            self._domain_metrics[domain][metric.name] = metric
        else:
            self._global[metric.name] = metric

    def get(self, name: str, domain: str | None = None) -> MetricDef | None:
        """Look up a metric by name.

        Tries exact name match first, then alias match, then case-insensitive.
        Domain scope is searched before global.
        """
        if domain and domain in self._domain_metrics:
            found = self._find_in_store(self._domain_metrics[domain], name)
            if found is not None:
                return found
        return self._find_in_store(self._global, name)

    def list_all(self, domain: str | None = None) -> list[MetricDef]:
        """List all metrics, optionally filtered by *domain*.

        Returns:
            List of ``MetricDef`` sorted by name.
        """
        if domain:
            metrics = self._domain_metrics.get(domain, {})
            return sorted(metrics.values(), key=lambda m: m.name)
        return sorted(self._global.values(), key=lambda m: m.name)

    def delete(self, name: str, domain: str | None = None) -> bool:
        """Delete a metric by name."""
        store = self._get_store(domain)
        if name in store:
            del store[name]
            return True
        if domain and name in self._global:
            del self._global[name]
            return True
        return False

    def clear(self, domain: str | None = None) -> int:
        """Clear all metrics, or only those in a specific *domain*.

        Returns:
            Number of metrics removed.
        """
        if domain:
            count = len(self._domain_metrics.get(domain, {}))
            self._domain_metrics.pop(domain, None)
            return count
        else:
            count = self.metric_count
            self._global.clear()
            self._domain_metrics.clear()
            return count

    # ── Core: Resolution ───────────────────────────────────────────────

    def resolve_metric(
        self,
        name: str,
        domain: str | None = None,
    ) -> MetricDef | None:
        """Resolve a metric name to its ``MetricDef`` (alias of :meth:`get`)."""
        return self.get(name, domain=domain)

    def query_metrics(
        self,
        metrics: list[str],
        *,
        dimensions: list[str] | None = None,
        time_grain: str = "",
        filters: dict[str, str] | None = None,
        domain: str | None = None,
        dialect: str = "",
        order_by: list[str] | None = None,
        limit: int = 0,
    ) -> str:
        """Generate a complete SQL ``SELECT`` query from metric definitions.

        This is the primary entry point for the metric semantic layer.

        Args:
            metrics: List of metric names to include as SELECT expressions.
            dimensions: Column names for ``GROUP BY`` (also added to SELECT).
            time_grain: Override the default time granularity.
            filters: ``{column: value}`` dict for ``WHERE`` clause equality.
            domain: Domain scope for metric lookup.
            dialect: Target SQL dialect for translation (empty = ANSI).
            order_by: Optional explicit ``ORDER BY`` columns.
            limit: Optional ``LIMIT`` clause.

        Returns:
            Complete SQL SELECT statement.

        Raises:
            KeyError: If any metric name cannot be resolved.
        """
        # 1. Resolve all metrics
        resolved: list[MetricDef] = []
        tables: set[str] = set()
        for m_name in metrics:
            m = self.get(m_name, domain=domain)
            if m is None:
                raise KeyError(
                    f"Metric '{m_name}' not found"
                    + (f" in domain '{domain}'" if domain else "")
                )
            resolved.append(m)
            if m.table:
                tables.add(m.table)

        if not tables:
            raise ValueError(
                "At least one metric must have a 'table' set to generate SQL"
            )

        # Use the first metric's table as the primary FROM source
        # (multi-table JOINs require sql_template overrides)
        primary_table = resolved[0].table

        # 2. Determine effective time_grain
        effective_grain = time_grain or resolved[0].time_grain

        # 3. Build SELECT expressions
        dims = dimensions or []
        select_parts: list[str] = []

        # Time-grain dimension column (if a time_column is set on any metric)
        time_col = ""
        for m in resolved:
            if m.time_column:
                time_col = m.time_column
                break
        if time_col and effective_grain:
            date_expr = _date_trunc_expr(time_col, effective_grain)
            select_parts.append(f"{date_expr} AS {time_col}_{effective_grain}")
            if time_col not in dims:
                dims = [time_col] + dims

        # Dimension columns
        for dim in dims:
            select_parts.append(_quote_ident(dim))

        # Metric expressions
        for m in resolved:
            alias = _sanitise_alias(m.name)
            if m.has_template:
                # Use SQL template with placeholders
                template_sql = m.sql_template.format(
                    metric=m.formula,
                    table=m.table,
                    dimensions=", ".join(dims),
                    time_grain=effective_grain,
                    filters=_build_filter_clause(filters or {}),
                )
                select_parts.append(f"{template_sql} AS {alias}")
            elif m.has_formula:
                select_parts.append(f"{m.formula} AS {alias}")
            else:
                select_parts.append(f"COUNT(*) AS {alias}")

        # 4. Assemble query
        sql = "SELECT\n  " + ",\n  ".join(select_parts)
        sql += f"\nFROM {_quote_ident(primary_table)}"

        # WHERE clause
        if filters:
            where_parts = []
            for col, val in filters.items():
                where_parts.append(f"{_quote_ident(col)} = {_quote_literal(val)}")
            sql += "\nWHERE " + " AND ".join(where_parts)

        # GROUP BY (all dimension positions)
        if dims:
            group_positions = list(range(1, len(dims) + 1))
            # If time_col was prepended, adjust
            if time_col and time_col in dims:
                group_positions = list(range(1, len(select_parts) - len(resolved) + 1))
            sql += "\nGROUP BY " + ", ".join(str(p) for p in group_positions)

        # ORDER BY
        order_cols = order_by or dims
        if order_cols:
            order_parts = [_quote_ident(c) for c in order_cols]
            sql += "\nORDER BY " + ", ".join(order_parts)

        # LIMIT
        if limit > 0:
            sql += f"\nLIMIT {limit}"

        # 5. Dialect translation
        if dialect:
            sql = self._translate_dialect(sql, dialect)

        return sql

    # ── Phase 5.8: Deep integration ─────────────────────────────────

    def auto_generate_metrics_from_glossary(self, domain: str = "") -> int:
        """Create MetricDefs from glossary terms that have expressions.

        For each glossary term in the active domain with ``type: derived_column``
        or an explicit SQL expression, create a corresponding :class:`MetricDef`.

        Args:
            domain: Domain identifier. Empty = all domains.

        Returns:
            Number of new metrics created.
        """
        try:
            from app.knowledge.domain_manager import domain_manager
        except ImportError:
            return 0

        domains = [domain] if domain else list(domain_manager._domains.keys())
        count = 0

        for d in domains:
            terms = domain_manager.get_glossary(d)
            for term in terms:
                if term.get("type") == "derived_column" or term.get("expression"):
                    name = term.get("name", "")
                    if not name or self.get(name, domain=d):
                        continue  # already exists
                    metric = MetricDef(
                        name=name,
                        description=term.get("description", ""),
                        formula=term.get("expression", ""),
                        domain=d,
                    )
                    self.add(metric, domain=d)
                    count += 1
        return count

    def validate_metric_sql(self, sql: str, target_dialect: str = "") -> dict[str, Any]:
        """Validate generated metric SQL against dialect-specific syntax.

        Args:
            sql: The generated SQL to validate.
            target_dialect: Target dialect for validation.

        Returns:
            Dict with ``valid`` (bool), ``errors`` (list of str), ``dialect``.
        """
        errors: list[str] = []
        try:
            import sqlglot

            if target_dialect:
                parsed = sqlglot.parse(sql, read=target_dialect)
            else:
                parsed = sqlglot.parse(sql)
            if not parsed:
                errors.append("SQL could not be parsed by sqlglot")
        except ImportError:
            pass  # sqlglot not available, skip validation
        except Exception as e:
            errors.append(f"Parse error: {e}")

        return {
            "valid": len(errors) == 0,
            "errors": errors,
            "dialect": target_dialect or "ansi",
        }

    def get_metric_lineage(self, metric_name: str) -> dict[str, Any]:
        """Trace which glossary terms and tables feed a metric.

        Args:
            metric_name: Name of the metric to trace.

        Returns:
            Dict with ``metric``, ``tables``, ``glossary_terms``, ``formula``.
        """
        metric = self.get(metric_name)
        if metric is None:
            return {"metric": metric_name, "error": "not found"}

        # Extract table references from formula
        tables: list[str] = []
        import re

        if metric.formula:
            tables = list(set(re.findall(r"FROM\s+(\w+)", metric.formula, re.IGNORECASE)))
        if metric.table:
            tables.append(metric.table)

        return {
            "metric": metric_name,
            "description": metric.description,
            "formula": metric.formula,
            "tables": sorted(set(tables)),
            "dimensions": metric.dimensions,
            "time_grain": metric.time_grain,
            "domain": metric.domain,
        }

    def suggest_dimensions(self, metric_name: str) -> list[str]:
        """Suggest dimensions for a metric using RAG discovery.

        Enhanced from the existing stub to use SchemaMetadataRAG for
        dimension discovery when available.

        Args:
            metric_name: Name of the metric.

        Returns:
            List of suggested dimension column names.
        """
        metric = self.get(metric_name)
        if metric is None:
            return []
        if metric.dimensions:
            return list(metric.dimensions)  # already defined

        # Try RAG-based discovery
        try:
            from app.knowledge.retrieval.lancedb_store import LanceDBStore
            from app.knowledge.retrieval.schema_rag import SchemaMetadataRAG

            store = LanceDBStore()
            rag = SchemaMetadataRAG(store)
            # Search for tables related to the metric
            tables = rag.find_relevant_tables(
                f"{metric.description} {metric.formula}",
                top_k=3,
            )
            suggestions: list[str] = []
            for t in tables:
                if hasattr(t, "columns"):
                    suggestions.extend(
                        c.name for c in t.columns[:3] if hasattr(c, "name")
                    )
            return suggestions[:10]
        except Exception:
            return []

    def cross_dialect_validate(
        self, metric_name: str, dialects: list[str] | None = None
    ) -> dict[str, Any]:
        """Generate SQL for multiple dialects and validate all.

        Args:
            metric_name: Name of the metric.
            dialects: List of dialects to validate. Default: common set.

        Returns:
            Dict mapping ``dialect → validation_result`` plus ``all_valid`` boolean.
        """
        if dialects is None:
            dialects = ["postgres", "mysql", "sqlite", "duckdb", "snowflake"]

        results: dict[str, Any] = {"all_valid": True, "results": {}}
        for d in dialects:
            sql = self.query_metrics([metric_name], dialect=d)
            validation = self.validate_metric_sql(sql, target_dialect=d)
            results["results"][d] = {
                "sql": sql,
                "valid": validation["valid"],
                "errors": validation["errors"],
            }
            if not validation["valid"]:
                results["all_valid"] = False
        return results

    # ── Internal ───────────────────────────────────────────────────────

    def _get_store(self, domain: str | None) -> dict[str, MetricDef]:
        if domain:
            if domain not in self._domain_metrics:
                self._domain_metrics[domain] = {}
            return self._domain_metrics[domain]
        return self._global

    @staticmethod
    def _find_in_store(
        store: dict[str, MetricDef], name: str
    ) -> MetricDef | None:
        """Find a metric by name, aliases, or case-insensitive match."""
        # Exact name match
        if name in store:
            return store[name]

        name_lower = name.lower()
        for m in store.values():
            # Alias match
            if name_lower in (a.lower() for a in m.aliases):
                return m
            # Case-insensitive name match
            if m.name.lower() == name_lower:
                return m

        return None

    def _translate_dialect(self, sql: str, dialect: str) -> str:
        """Translate *sql* to the target *dialect* if a ``DialectAdapter`` is set."""
        if self._dialect_adapter is not None:
            try:
                return self._dialect_adapter.translate(sql, dialect)
            except Exception:
                pass
        # Fallback: use sqlglot directly
        try:
            import sqlglot
            result = sqlglot.transpile(sql, read="ansi", write=dialect, pretty=True)
            if result:
                return result[0]
        except Exception:
            pass
        return sql


# ── SQL helpers ─────────────────────────────────────────────────────────


def _date_trunc_expr(column: str, grain: str) -> str:
    """Build a cross-dialect ``DATE_TRUNC`` expression.

    Uses the style ``DATE_TRUNC('grain', column)`` which sqlglot can
    translate to dialect-specific forms.
    """
    valid_grains = {"hour", "day", "week", "month", "quarter", "year"}
    g = grain.lower()
    if g not in valid_grains:
        g = "day"
    return f"DATE_TRUNC('{g}', {_quote_ident(column)})"


def _quote_ident(name: str) -> str:
    """Double-quote an identifier if it contains special characters."""
    if not name:
        return name
    # Already quoted
    if name.startswith('"') and name.endswith('"'):
        return name
    # Only quote if needed (spaces, reserved words, special chars)
    if any(c in name for c in ' -/()[]{}.,;:\'"'):
        return f'"{name}"'
    return name


def _quote_literal(value: str) -> str:
    """Single-quote a string literal, escaping embedded quotes."""
    escaped = value.replace("'", "''")
    return f"'{escaped}'"


def _sanitise_alias(name: str) -> str:
    """Convert a metric name to a valid SQL alias."""
    return name.lower().replace(" ", "_").replace("-", "_")


def _build_filter_clause(filters: dict[str, str]) -> str:
    """Build a ``WHERE``-style filter string from key-value pairs."""
    if not filters:
        return "1=1"
    parts = []
    for col, val in filters.items():
        parts.append(f"{_quote_ident(col)} = {_quote_literal(val)}")
    return " AND ".join(parts)
