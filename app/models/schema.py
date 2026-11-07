"""Database schema models — ColumnSchema, TableSchema, SchemaSnapshot, etc.

See SPEC §4.3.2 for the full specification.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


def _clone_leaf(value: Any) -> Any:
    """Copy a leaf value without paying deepcopy's per-object overhead.

    Scalars are immutable and shared; anything else (dicts, lists, custom
    objects) still goes through ``copy.deepcopy`` so the clone cannot alias them.
    """
    if isinstance(value, str | int | float | bool | bytes | type(None)):
        return value
    return copy.deepcopy(value)


@dataclass
class ColumnSchema:
    """A single column in a database table."""

    name: str
    type: str
    nullable: bool = True
    is_primary_key: bool = False
    is_foreign_key: bool = False
    references: tuple[str, str] | None = None  # (table, column)
    default: Any = None
    comment: str | None = None
    enum_values: list[str] | None = None
    sample_values: list[Any] = field(default_factory=list)
    # PII detection (SPEC §9.2): flagged columns can be masked or excluded
    # before sending schema to external LLMs.
    is_pii: bool = False
    pii_type: str = ""  # "phone" | "email" | "id_card" | "name" | "address" | "other"

    def clone(self) -> ColumnSchema:
        """Copy this column and its mutable leaf values (see SchemaSnapshot.clone)."""
        return ColumnSchema(
            name=self.name,
            type=self.type,
            nullable=self.nullable,
            is_primary_key=self.is_primary_key,
            is_foreign_key=self.is_foreign_key,
            references=self.references,
            default=_clone_leaf(self.default),
            comment=self.comment,
            enum_values=None if self.enum_values is None else list(self.enum_values),
            sample_values=[_clone_leaf(value) for value in self.sample_values],
            is_pii=self.is_pii,
            pii_type=self.pii_type,
        )


@dataclass
class IndexInfo:
    """Index metadata for a table."""

    name: str
    columns: list[str]
    is_unique: bool = False
    method: str = "btree"

    def clone(self) -> IndexInfo:
        return IndexInfo(
            name=self.name,
            columns=list(self.columns),
            is_unique=self.is_unique,
            method=self.method,
        )


@dataclass
class ForeignKey:
    """Foreign key constraint metadata."""

    name: str
    column: str
    ref_table: str
    ref_column: str

    def clone(self) -> ForeignKey:
        return ForeignKey(
            name=self.name,
            column=self.column,
            ref_table=self.ref_table,
            ref_column=self.ref_column,
        )


@dataclass
class TableSchema:
    """Complete schema for a single database table."""

    name: str
    comment: str | None = None
    columns: list[ColumnSchema] = field(default_factory=list)
    row_count_estimate: int = 0
    indexes: list[IndexInfo] = field(default_factory=list)
    foreign_keys: list[ForeignKey] = field(default_factory=list)
    # Optional provenance, populated only for the namespace actually introspected.
    schema_name: str | None = None
    catalog_name: str | None = None

    @property
    def column_names(self) -> list[str]:
        return [c.name for c in self.columns]

    @property
    def primary_keys(self) -> list[str]:
        return [c.name for c in self.columns if c.is_primary_key]

    @property
    def nullable_columns(self) -> list[str]:
        return [c.name for c in self.columns if c.nullable]

    def get_column(self, name: str) -> ColumnSchema | None:
        for c in self.columns:
            if c.name == name:
                return c
        return None

    def clone(self) -> TableSchema:
        """Copy this table and everything mutable below it."""
        return TableSchema(
            name=self.name,
            comment=self.comment,
            columns=[column.clone() for column in self.columns],
            row_count_estimate=self.row_count_estimate,
            indexes=[index.clone() for index in self.indexes],
            foreign_keys=[key.clone() for key in self.foreign_keys],
            schema_name=self.schema_name,
            catalog_name=self.catalog_name,
        )


@dataclass(frozen=True)
class DuckDBSourceState:
    """Native namespace identity, not a table/column DDL or data version."""

    catalog_name: str
    catalog_oid: int
    schema_name: str
    schema_oid: int
    search_path: str
    temporary_objects: tuple[tuple[str, str], ...] = ()


@dataclass
class SchemaSnapshot:
    """A point-in-time snapshot of a database's full schema."""

    database_type: str
    database_name: str
    tables: dict[str, TableSchema] = field(default_factory=dict)
    created_at: datetime = field(default_factory=datetime.now)
    version: int = 1
    # SQLite main.schema_version at introspection; unknown for legacy/other sources.
    source_schema_version: int | None = None
    duckdb_source_state: DuckDBSourceState | None = None
    from_cache: bool = False

    def get_table(self, name: str) -> TableSchema | None:
        return self.tables.get(name)

    def clone(self) -> SchemaSnapshot:
        """Return a deep copy without going through ``copy.deepcopy``.

        ``copy.deepcopy`` replays the pickle protocol for every dataclass
        instance. On a 10k-table snapshot that measured 1.3-2.7 s per call, and
        the extraction cache hands out one copy per request, which made it the
        largest single cost of a query at that size. Rebuilding the tree keeps
        the same guarantee — the copy shares no mutable state with the cached
        snapshot — at a fraction of the cost. Immutable fields (``created_at``,
        the frozen DuckDB source state) are shared on purpose.
        """
        return SchemaSnapshot(
            database_type=self.database_type,
            database_name=self.database_name,
            tables={name: table.clone() for name, table in self.tables.items()},
            created_at=self.created_at,
            version=self.version,
            source_schema_version=self.source_schema_version,
            duckdb_source_state=self.duckdb_source_state,
            from_cache=self.from_cache,
        )

    @property
    def table_names(self) -> list[str]:
        return sorted(self.tables.keys())

    def format_for_llm(
        self,
        table_limit: int = 8,
        table_order: list[str] | None = None,
        pii_mode: str = "mask",
    ) -> str:
        """Format schema as text suitable for injection into LLM prompts.

        When *table_order* is provided, tables are rendered in that order
        (useful for prioritizing relevant tables from schema linking).

        PII handling (SPEC §9.2):
            - ``pii_mode="mask"`` (default): replace PII column names with
              ``<pii:phone>``, ``<pii:email>`` etc. and keep the data type.
            - ``pii_mode="exclude"``: omit PII columns entirely.
            - ``pii_mode="off"``: include PII columns as-is (no masking).
        """
        lines = [f"Database: {self.database_name} ({self.database_type})"]

        # Determine which tables to render and in what order
        if table_order:
            ordered_names = [t for t in table_order if t in self.tables]
        else:
            ordered_names = sorted(self.tables.keys())
        tables = [self.tables[name] for name in ordered_names[:table_limit]]

        # Summary line — only list tables that are actually shown (respects table_limit)
        shown_names = [t.name for t in tables]
        if len(self.tables) > table_limit:
            lines.append(
                f"Tables ({len(self.tables)} total, showing {len(shown_names)}): "
                f"{', '.join(shown_names)}"
            )
        else:
            lines.append(f"Tables ({len(shown_names)}): {', '.join(shown_names)}")
        for table in tables:
            lines.append(f"\n-- Table: {table.name}")
            if table.schema_name:
                lines.append(f"   Schema: {table.schema_name}")
            if table.catalog_name:
                lines.append(f"   Catalog: {table.catalog_name}")
            if table.comment:
                lines.append(f"   Comment: {table.comment}")
            if table.row_count_estimate:
                lines.append(f"   Estimated rows: {table.row_count_estimate}")
            lines.append("   Columns:")
            for col in table.columns:
                # PII handling
                if col.is_pii:
                    if pii_mode == "exclude":
                        continue
                    if pii_mode == "mask":
                        masked_name = f"<pii:{col.pii_type or 'sensitive'}>"
                        lines.append(
                            f"     {masked_name}: {col.type}"
                            + ("" if col.nullable else " NOT NULL")
                            + " -- PII column (masked)"
                        )
                        continue
                    # pii_mode == "off": fall through and render normally
                flags = []
                if col.is_primary_key:
                    flags.append("PK")
                if col.is_foreign_key:
                    flags.append(f"FK→{col.references}")
                flag_str = f" [{', '.join(flags)}]" if flags else ""
                nullable = "" if col.nullable else " NOT NULL"
                comment = f" -- {col.comment}" if col.comment else ""
                lines.append(
                    f"     {col.name}: {col.type}{nullable}{flag_str}{comment}"
                )
        if len(self.tables) > table_limit:
            lines.append(
                f"\n... and {len(self.tables) - table_limit} more tables "
                f"(use describe_table to explore)"
            )
        return "\n".join(lines)
