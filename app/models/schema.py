"""Database schema models — ColumnSchema, TableSchema, SchemaSnapshot, etc.

See SPEC §4.3.2 for the full specification.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


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


@dataclass
class IndexInfo:
    """Index metadata for a table."""

    name: str
    columns: list[str]
    is_unique: bool = False
    method: str = "btree"


@dataclass
class ForeignKey:
    """Foreign key constraint metadata."""

    name: str
    column: str
    ref_table: str
    ref_column: str


@dataclass
class TableSchema:
    """Complete schema for a single database table."""

    name: str
    comment: str | None = None
    columns: list[ColumnSchema] = field(default_factory=list)
    row_count_estimate: int = 0
    indexes: list[IndexInfo] = field(default_factory=list)
    foreign_keys: list[ForeignKey] = field(default_factory=list)

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


@dataclass
class SchemaSnapshot:
    """A point-in-time snapshot of a database's full schema."""

    database_type: str
    database_name: str
    tables: dict[str, TableSchema] = field(default_factory=dict)
    created_at: datetime = field(default_factory=datetime.now)
    version: int = 1

    def get_table(self, name: str) -> TableSchema | None:
        return self.tables.get(name)

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
