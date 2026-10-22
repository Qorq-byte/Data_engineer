"""Validate references with SQLGlot scopes, without rewriting executable SQL."""

import sqlglot
from sqlglot import exp
from sqlglot.errors import OptimizeError, SqlglotError
from sqlglot.optimizer.normalize_identifiers import normalize_identifiers
from sqlglot.optimizer.qualify import qualify
from sqlglot.schema import MappingSchema

from app.models.schema import SchemaSnapshot


def check_schema_references(
    sql: str, schema: SchemaSnapshot, dialect: str | None
) -> tuple[bool, list[str]]:
    # Quoted keys preserve source identifier case; type checks remain a separate stage.
    mapping = MappingSchema(dialect=dialect)
    sources = {
        normalize_identifiers(exp.to_identifier(name, quoted=True), dialect=dialect).name: table
        for name, table in schema.tables.items()
    }
    for name, table in schema.tables.items():
        mapping.add_table(
            exp.Table(this=exp.to_identifier(name, quoted=True)),
            {exp.to_identifier(col.name, quoted=True).sql(dialect=dialect): "UNKNOWN"
             for col in table.columns},
        )

    def check_source(table: exp.Table) -> None:
        source = sources.get(table.name)
        # Match using the same dialect normalization as the private query AST.
        def matches(actual: str, expected: str | None) -> bool:
            return expected is not None and actual == normalize_identifiers(
                exp.to_identifier(expected, quoted=True), dialect=dialect,
            ).name

        if table.db or table.catalog:
            source_schema = source.schema_name if source else None
            # Compatibility for snapshots produced before namespace metadata existed.
            if source_schema is None:
                if dialect == "sqlite":
                    source_schema = "main"
                elif dialect == "mysql":
                    source_schema = schema.database_name
            if not matches(table.db, source_schema) or (
                table.catalog
                and not matches(table.catalog, source.catalog_name if source else None)
            ):
                raise OptimizeError("Qualified table namespace is not represented in source schema")
            table.set("db", None)  # private AST; never rewrite SQL sent to the driver
            table.set("catalog", None)
        if mapping.find(table, raise_on_missing=False) is None:
            raise OptimizeError(f"Table '{table.name}' not found in schema")

    try:
        expression = sqlglot.parse_one(sql, read=dialect)
        qualify(
            expression, dialect=dialect, schema=mapping, infer_schema=False,
            on_qualify=check_source,
        )
        # qualify_tables resolves a column's full prefix only if it matches a
        # source in that scope. A residual prefix must not be silently ignored.
        if any(column.db or column.catalog for column in expression.find_all(exp.Column)):
            raise OptimizeError("Qualified column namespace is not represented in query sources")
    except (SqlglotError, ValueError) as exc:
        return False, [f"SCHEMA_ERROR: {exc}"]
    return True, []
