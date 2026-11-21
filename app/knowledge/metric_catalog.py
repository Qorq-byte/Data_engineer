"""Bounded, versioned, server-owned metric definitions and deterministic SQL."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Hashable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from sqlglot import exp

from app.auth.tenant import DEFAULT_TENANT_ID, get_current_tenant_id, normalize_tenant_id
from app.models.schema import SchemaSnapshot
from app.security.sql_policy import require_read_query

_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,127}\Z")
_AGGREGATES = {"sum": exp.Sum, "count": exp.Count, "avg": exp.Avg, "min": exp.Min, "max": exp.Max}
_MAX_BYTES = 1_048_576
_CALENDAR_GRAINS = {"day", "week", "month"}
_PERIOD_COLUMN = "period_start"
_DIALECTS = {"sqlite", "postgres", "mysql"}
_INTEGER_COLUMN = re.compile(r"(?:tiny|small|medium|big)?int(?:eger|[248])?\b", re.I)


class CatalogError(ValueError):
    """Invalid server catalog or unsupported metric request (messages contain no secrets)."""


class _UniqueLoader(yaml.SafeLoader):
    """Reject ambiguous duplicate mappings and YAML aliases."""

    def compose_node(self, parent: Any, index: Any) -> Any:
        if self.check_event(yaml.AliasEvent):
            raise CatalogError("Metric catalog aliases are unsupported")
        return super().compose_node(parent, index)

    def construct_mapping(self, node: Any, deep: bool = False) -> dict[Hashable, Any]:
        mapping: dict[Hashable, Any] = {}
        for key_node, value_node in node.value:
            key = self.construct_object(key_node, deep=deep)
            if not isinstance(key, str) or key in mapping:
                raise CatalogError("Metric catalog contains invalid or duplicate keys")
            mapping[key] = self.construct_object(value_node, deep=deep)
        return mapping


def _object(value: Any, keys: set[str]) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != keys:
        raise CatalogError("Metric catalog fields do not match the catalog format")
    return value


def _text(value: Any) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 128:
        raise CatalogError("Metric catalog requires nonempty short strings")
    return value


def _identifier(value: Any) -> str:
    if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
        raise CatalogError("Metric identifiers must be simple SQL names")
    return value


def _names(value: Any, *, maximum: int = 32) -> tuple[str, ...]:
    if not isinstance(value, list) or len(value) > maximum:
        raise CatalogError("Metric name lists exceed the v1 limits")
    names = tuple(_identifier(item) for item in value)
    if len({name.casefold() for name in names}) != len(names):
        raise CatalogError("Duplicate metric or dimension names are unsupported")
    return names


@dataclass(frozen=True)
class CatalogMetric:
    name: str
    table: str
    column: str
    aggregation: str
    dimensions: tuple[str, ...]
    date_column: str = ""


@dataclass(frozen=True)
class MetricLookup:
    fact_table: str
    fact_key: str
    table: str
    key: str
    dimensions: tuple[tuple[str, str], ...]

    def check_schema(self, schema: SchemaSnapshot, dialect: str) -> None:
        fact = schema.get_table(self.fact_table)
        lookup = schema.get_table(self.table)
        left = fact.get_column(self.fact_key) if fact is not None else None
        right = lookup.get_column(self.key) if lookup is not None else None
        if (
            dialect not in _DIALECTS or lookup is None or lookup.primary_keys != [self.key]
            or left is None or right is None or not _INTEGER_COLUMN.match(left.type)
            or not _INTEGER_COLUMN.match(right.type)
        ):
            raise CatalogError("Metric lookup requires integer keys and a single primary key")


def _period(column: exp.Column, grain: str, dialect: str) -> exp.Expr:
    if dialect == "sqlite":
        pattern = "%Y-%m-01" if grain == "month" else "%Y-%m-%d"
        offsets = [exp.Literal.string("weekday 0"), exp.Literal.string("-6 days")]
        arguments = [exp.Literal.string(pattern), column]
        if grain == "week":
            arguments.extend(offsets)
        return exp.Anonymous(this="STRFTIME", expressions=arguments)
    if dialect == "postgres":
        timestamp = exp.cast(column, "TIMESTAMP")
        truncated = exp.Anonymous(
            this="DATE_TRUNC", expressions=[exp.Literal.string(grain), timestamp],
        )
        return exp.cast(truncated, "DATE")
    if grain == "day":
        return column
    if grain == "week":
        day = exp.Anonymous(this="DAYOFWEEK", expressions=[column.copy()])
        shift: exp.Expr = exp.Mod(
            this=exp.Paren(this=exp.Add(this=day, expression=exp.Literal.number(5))),
            expression=exp.Literal.number(7),
        )
    else:
        day = exp.Anonymous(this="DAYOFMONTH", expressions=[column.copy()])
        shift = exp.Sub(this=day, expression=exp.Literal.number(1))
    return exp.DateSub(this=column, expression=shift, unit=exp.Var(this="DAY"))


@dataclass(frozen=True)
class MetricSelection:
    version: str
    catalog_sha256: str
    domain: str
    database: str
    metrics: tuple[CatalogMetric, ...]
    dimensions: tuple[str, ...]
    filters: tuple[tuple[str, str], ...]
    grain: str = ""
    lookup: MetricLookup | None = None

    @property
    def output_columns(self) -> tuple[str, ...]:
        time_columns = (_PERIOD_COLUMN,) if self.grain else ()
        return (*time_columns, *self.dimensions, *(metric.name for metric in self.metrics))

    def sql(self, dialect: str, *, schema: SchemaSnapshot | None = None) -> str:
        """Only structured aggregates and bound-as-literals equality predicates."""
        fact = self.metrics[0].table
        lookup = self.lookup
        if lookup is not None:
            if schema is None:
                raise CatalogError("Metric lookup requires a captured schema")
            lookup.check_schema(schema, dialect)
        lookup_columns = dict(lookup.dimensions) if lookup is not None else {}

        def dimension(name: str) -> exp.Column:
            if lookup is not None and name in lookup_columns:
                return exp.column(lookup_columns[name], table=lookup.table, quoted=True)
            return exp.column(name, table=fact if lookup is not None else None, quoted=True)

        projections: list[exp.Expr] = []
        groups: list[exp.Expr] = []
        if self.grain:
            table = schema.get_table(fact) if schema is not None else None
            date_column = self.metrics[0].date_column
            column = table.get_column(date_column) if table is not None else None
            if dialect not in _DIALECTS or column is None or column.type.upper() != "DATE":
                raise CatalogError("Metric grain requires a DATE column on a supported dialect")
            period = _period(exp.column(date_column, table=fact, quoted=True), self.grain, dialect)
            groups.append(period)
            projections.append(exp.alias_(period.copy(), _PERIOD_COLUMN, quoted=True))
        for name in self.dimensions:
            expr = dimension(name)
            groups.append(expr)
            projections.append(exp.alias_(expr.copy(), name, quoted=True)
                               if name in lookup_columns else expr.copy())
        for metric in self.metrics:
            measure = exp.Star() if metric.column == "*" else exp.column(
                metric.column, table=fact if lookup is not None else None, quoted=True,
            )
            aggregate = _AGGREGATES[metric.aggregation](this=measure)
            projections.append(exp.alias_(aggregate, metric.name, quoted=True))
        source = (exp.Table(this=exp.to_identifier(fact, quoted=True)) if lookup or self.grain
                  else exp.to_table(fact, quoted=True))
        query = exp.select(*projections).from_(source)
        if lookup is not None:
            dimension_table = exp.Table(this=exp.to_identifier(lookup.table, quoted=True))
            query = query.join(dimension_table, join_type="LEFT", on=exp.EQ(
                this=exp.column(lookup.key, table=lookup.table, quoted=True),
                expression=exp.column(lookup.fact_key, table=fact, quoted=True),
            ))
        if self.filters:
            query = query.where(*(
                exp.EQ(this=dimension(key), expression=exp.Literal.string(value))
                for key, value in self.filters
            ))
        if groups:
            query = query.group_by(*(expr.copy() for expr in groups))
            query = query.order_by(*(expr.copy() for expr in groups))
        target = None if dialect in {"", "ansi"} else dialect
        sql = query.sql(dialect=target)
        require_read_query(sql, dialect=target or "")
        return sql


@dataclass(frozen=True)
class MetricBinding:
    domain: str
    database: str
    metrics: tuple[CatalogMetric, ...]
    tenant_id: int = DEFAULT_TENANT_ID
    lookup: MetricLookup | None = None


@dataclass(frozen=True)
class MetricCatalog:
    version: str
    sha256: str
    bindings: tuple[MetricBinding, ...]

    def select(
        self, *, version: str, domain: str, database: str, metrics: Any,
        dimensions: Any, filters: Any, tenant_id: int | None = None, grain: str = "",
    ) -> MetricSelection:
        tenant_id = get_current_tenant_id() if tenant_id is None else normalize_tenant_id(tenant_id)
        if version != self.version:
            raise CatalogError("Metric catalog version must match the active version")
        names = _names(metrics, maximum=8)
        dims = _names(dimensions)
        if not names:
            raise CatalogError("At least one explicit metric name is required")
        binding = next(
            (
                b for b in self.bindings
                if (b.domain, b.database, b.tenant_id) == (domain, database, tenant_id)
            ), None
        )
        if binding is None:
            raise CatalogError("No metric binding for the requested domain and database")
        definitions = {metric.name: metric for metric in binding.metrics}
        if any(name not in definitions for name in names):
            raise CatalogError("Unknown metric name in the selected binding")
        selected = tuple(definitions[name] for name in names)
        if len({metric.table for metric in selected}) != 1:
            raise CatalogError("Metric v1 does not support cross-table combinations")
        allowed = set.intersection(*(set(metric.dimensions) for metric in selected))
        if not set(dims) <= allowed or {n.casefold() for n in dims} & {n.casefold() for n in names}:
            raise CatalogError("Dimensions are not allowed or conflict with metric aliases")
        if not isinstance(filters, dict) or len(filters) > 32 or not set(filters) <= allowed:
            raise CatalogError("Filter columns are outside the selected metrics")
        if any(not isinstance(value, str) or len(value) > 1024 or "\x00" in value
               for value in filters.values()):
            raise CatalogError("Metric filters require bounded string equality values")
        if grain and (
            grain not in _CALENDAR_GRAINS or len({metric.date_column for metric in selected}) != 1
            or not selected[0].date_column
            or _PERIOD_COLUMN in {n.casefold() for n in (*dims, *names)}
        ):
            raise CatalogError("Requested metric grain requires one configured date column")
        lookup = binding.lookup
        if lookup is not None and (
            lookup.fact_table != selected[0].table
            or not (set(dims) | set(filters)) & dict(lookup.dimensions).keys()
        ):
            lookup = None
        return MetricSelection(
            self.version, self.sha256, domain, database, selected, dims,
            tuple(sorted(filters.items())), grain, lookup,
        )


def _lookup(value: Any) -> MetricLookup:
    item = _object(value, {"fact_table", "fact_key", "table", "key", "dimensions"})
    fact, table = _identifier(item["fact_table"]), _identifier(item["table"])
    dims = item["dimensions"]
    if (
        fact.casefold() == table.casefold()
        or not isinstance(dims, dict) or not 1 <= len(dims) <= 32
    ):
        raise CatalogError("Metric lookup requires a separate table and dimension aliases")
    _names(list(dims))
    columns = tuple((name, _identifier(column)) for name, column in sorted(dims.items()))
    return MetricLookup(
        fact, _identifier(item["fact_key"]), table, _identifier(item["key"]), columns,
    )


def load_catalog(path: str) -> MetricCatalog:
    """Load one snapshot; configuration comes from the server, never a request path."""
    if not path.strip():
        raise CatalogError("metric_query requires a configured METRIC_CATALOG_PATH")
    try:
        with Path(path).open("rb") as stream:
            raw = stream.read(_MAX_BYTES + 1)
        if len(raw) > _MAX_BYTES:
            raise CatalogError("Metric catalog exceeds the file size limit")
        document = yaml.load(raw.decode("utf-8-sig"), Loader=_UniqueLoader)
    except CatalogError:
        raise
    except (OSError, UnicodeError, yaml.YAMLError, RecursionError):
        raise CatalogError("Metric catalog is unreadable or malformed") from None
    root_keys = {"version", "bindings"}
    v2 = isinstance(document, dict) and document.get("format") == "metric-catalog-v2"
    root = _object(document, root_keys | {"format"} if v2 else root_keys)
    version = _text(root["version"])
    rows = root["bindings"]
    if not isinstance(rows, list) or not 1 <= len(rows) <= 100:
        raise CatalogError("Metric catalog requires 1 to 100 bindings")
    bindings: list[MetricBinding] = []
    seen: set[tuple[str, str, int]] = set()
    for raw_binding in rows:
        binding_keys = {"domain", "database", "metrics"}
        if isinstance(raw_binding, dict):
            binding_keys |= {"tenant_id"} & raw_binding.keys()
            if v2:
                binding_keys |= {"lookup"} & raw_binding.keys()
        row = _object(raw_binding, binding_keys)
        domain, database = _text(row["domain"]), _text(row["database"])
        try:
            tenant_id = normalize_tenant_id(row.get("tenant_id"))
        except ValueError:
            raise CatalogError("Metric binding tenant_id must be a positive integer") from None
        if (domain, database, tenant_id) in seen:
            raise CatalogError("Duplicate metric domain/database binding")
        seen.add((domain, database, tenant_id))
        definitions = row["metrics"]
        if not isinstance(definitions, list) or not 1 <= len(definitions) <= 100:
            raise CatalogError("Metric bindings require 1 to 100 definitions")
        parsed: list[CatalogMetric] = []
        for definition in definitions:
            keys = {"name", "table", "column", "aggregation", "dimensions"}
            if v2 and isinstance(definition, dict) and "date_column" in definition:
                keys.add("date_column")
            item = _object(definition, keys)
            name, table = _identifier(item["name"]), _identifier(item["table"])
            aggregation = item["aggregation"]
            if not isinstance(aggregation, str) or aggregation not in _AGGREGATES:
                raise CatalogError("Unsupported metric aggregation")
            column = "*" if item["column"] == "*" and aggregation == "count" else _identifier(
                item["column"]
            )
            parsed.append(CatalogMetric(
                name, table, column, aggregation, _names(item["dimensions"]),
                _identifier(item["date_column"]) if "date_column" in item else "",
            ))
        if len({m.name.casefold() for m in parsed}) != len(parsed):
            raise CatalogError("Duplicate metric definitions")
        lookup = _lookup(row["lookup"]) if "lookup" in row else None
        if lookup is not None and not any(m.table == lookup.fact_table for m in parsed):
            raise CatalogError("Metric lookup fact table must own a metric in its binding")
        bindings.append(MetricBinding(domain, database, tuple(parsed), tenant_id, lookup))
    return MetricCatalog(version, hashlib.sha256(raw).hexdigest(), tuple(bindings))
