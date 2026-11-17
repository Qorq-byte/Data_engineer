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

from app.security.sql_policy import require_read_query

_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,127}\Z")
_AGGREGATES = {"sum": exp.Sum, "count": exp.Count, "avg": exp.Avg, "min": exp.Min, "max": exp.Max}
_MAX_BYTES = 1_048_576


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
        raise CatalogError("Metric catalog fields do not match the v1 contract")
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


@dataclass(frozen=True)
class MetricSelection:
    version: str
    catalog_sha256: str
    domain: str
    database: str
    metrics: tuple[CatalogMetric, ...]
    dimensions: tuple[str, ...]
    filters: tuple[tuple[str, str], ...]

    def sql(self, dialect: str) -> str:
        """Only structured aggregates and bound-as-literals equality predicates."""
        projections: list[exp.Expr] = [exp.column(name, quoted=True) for name in self.dimensions]
        for metric in self.metrics:
            column = exp.Star() if metric.column == "*" else exp.column(metric.column, quoted=True)
            aggregate = _AGGREGATES[metric.aggregation](this=column)
            projections.append(exp.alias_(aggregate, metric.name, quoted=True))
        query = exp.select(*projections).from_(exp.to_table(self.metrics[0].table, quoted=True))
        if self.filters:
            query = query.where(*(
                exp.EQ(this=exp.column(key, quoted=True), expression=exp.Literal.string(value))
                for key, value in self.filters
            ))
        if self.dimensions:
            query = query.group_by(*(exp.column(name, quoted=True) for name in self.dimensions))
            query = query.order_by(*(exp.column(name, quoted=True) for name in self.dimensions))
        target = None if dialect in {"", "ansi"} else dialect
        sql = query.sql(dialect=target)
        require_read_query(sql, dialect=target or "")
        return sql


@dataclass(frozen=True)
class MetricBinding:
    domain: str
    database: str
    metrics: tuple[CatalogMetric, ...]


@dataclass(frozen=True)
class MetricCatalog:
    version: str
    sha256: str
    bindings: tuple[MetricBinding, ...]

    def select(
        self, *, version: str, domain: str, database: str, metrics: Any,
        dimensions: Any, filters: Any,
    ) -> MetricSelection:
        if version != self.version:
            raise CatalogError("Metric catalog version must match the active version")
        names = _names(metrics, maximum=8)
        dims = _names(dimensions)
        if not names:
            raise CatalogError("At least one explicit metric name is required")
        binding = next(
            (b for b in self.bindings if (b.domain, b.database) == (domain, database)), None
        )
        if binding is None:
            raise CatalogError("No metric binding for the requested domain and database")
        lookup = {metric.name: metric for metric in binding.metrics}
        if any(name not in lookup for name in names):
            raise CatalogError("Unknown metric name in the selected binding")
        selected = tuple(lookup[name] for name in names)
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
        return MetricSelection(
            self.version, self.sha256, domain, database, selected, dims,
            tuple(sorted(filters.items())),
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
    root = _object(document, {"version", "bindings"})
    version = _text(root["version"])
    rows = root["bindings"]
    if not isinstance(rows, list) or not 1 <= len(rows) <= 100:
        raise CatalogError("Metric catalog requires 1 to 100 bindings")
    bindings: list[MetricBinding] = []
    seen: set[tuple[str, str]] = set()
    for raw_binding in rows:
        row = _object(raw_binding, {"domain", "database", "metrics"})
        domain, database = _text(row["domain"]), _text(row["database"])
        if (domain, database) in seen:
            raise CatalogError("Duplicate metric domain/database binding")
        seen.add((domain, database))
        definitions = row["metrics"]
        if not isinstance(definitions, list) or not 1 <= len(definitions) <= 100:
            raise CatalogError("Metric bindings require 1 to 100 definitions")
        parsed: list[CatalogMetric] = []
        for definition in definitions:
            item = _object(definition, {"name", "table", "column", "aggregation", "dimensions"})
            name, table = _identifier(item["name"]), _identifier(item["table"])
            aggregation = item["aggregation"]
            if not isinstance(aggregation, str) or aggregation not in _AGGREGATES:
                raise CatalogError("Unsupported metric aggregation")
            column = "*" if item["column"] == "*" and aggregation == "count" else _identifier(
                item["column"]
            )
            parsed.append(CatalogMetric(
                name, table, column, aggregation, _names(item["dimensions"])
            ))
        if len({m.name.casefold() for m in parsed}) != len(parsed):
            raise CatalogError("Duplicate metric definitions")
        bindings.append(MetricBinding(domain, database, tuple(parsed)))
    return MetricCatalog(version, hashlib.sha256(raw).hexdigest(), tuple(bindings))
