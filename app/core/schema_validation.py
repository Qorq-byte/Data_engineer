"""Validate references with SQLGlot scopes, without rewriting executable SQL."""

import sqlglot
from sqlglot import exp
from sqlglot.errors import OptimizeError, SqlglotError
from sqlglot.optimizer.normalize_identifiers import normalize_identifiers
from sqlglot.optimizer.qualify import qualify
from sqlglot.schema import MappingSchema

from app.models.schema import SchemaSnapshot, TableSchema


def check_schema_references(
    sql: str, schema: SchemaSnapshot, dialect: str | None
) -> tuple[bool, list[str]]:
    # Quoted keys preserve source identifier case; type checks remain a separate stage.
    try:
        expression = sqlglot.parse_one(sql, read=dialect)
    except (SqlglotError, ValueError) as exc:
        return False, [f"SCHEMA_ERROR: {exc}"]

    # Resolve only the tables this statement names. Building a sqlglot mapping
    # for the whole snapshot cost ~0.5 s per request against 1k tables (one
    # rendered identifier per column plus a normalisation pass per table) while
    # the statement touched a single table. Keys are normalised exactly like the
    # old full-dictionary ones, because ``qualify`` normalises the AST before
    # calling back; the case-folded scan keeps SQLite/DuckDB behaviour, where
    # MAIN.USERS resolves to "Users".
    sources: dict[str, TableSchema] = {}
    resolved: list[tuple[str, TableSchema]] = []
    for reference in dict.fromkeys(t.name for t in expression.find_all(exp.Table) if t.name):
        key = normalize_identifiers(
            exp.to_identifier(reference, quoted=True), dialect=dialect
        ).name
        table = schema.tables.get(key)
        schema_name = key
        if table is None:
            folded = key.casefold()
            for candidate, candidate_table in schema.tables.items():
                if candidate.casefold() == folded:
                    table, schema_name = candidate_table, candidate
                    break
        if table is None:
            continue
        sources[key] = table
        resolved.append((schema_name, table))

    mapping = MappingSchema(dialect=dialect)
    for schema_name, table in resolved:
        mapping.add_table(
            exp.Table(this=exp.to_identifier(schema_name, quoted=True)),
            {
                exp.to_identifier(col.name, quoted=True).sql(dialect=dialect): "UNKNOWN"
                for col in table.columns
            },
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
