"""Validate references with SQLGlot scopes, without rewriting executable SQL."""

import sqlglot
from sqlglot import exp
from sqlglot.errors import OptimizeError, SqlglotError
from sqlglot.optimizer.qualify import qualify
from sqlglot.schema import MappingSchema

from app.models.schema import SchemaSnapshot


def check_schema_references(
    sql: str, schema: SchemaSnapshot, dialect: str | None
) -> tuple[bool, list[str]]:
    # Quoted keys preserve source identifier case; type checks remain a separate stage.
    mapping = MappingSchema(dialect=dialect)
    for name, table in schema.tables.items():
        mapping.add_table(
            exp.Table(this=exp.to_identifier(name, quoted=True)),
            {exp.to_identifier(col.name, quoted=True).sql(dialect=dialect): "UNKNOWN"
             for col in table.columns},
        )

    def check_source(table: exp.Table) -> None:
        # Current TableSchema has no schema/catalog identity. Do not silently
        # validate another namespace using a same-named local table.
        if table.db or table.catalog:
            represented = (
                (dialect == "sqlite" and table.db == "main")
                or (dialect == "mysql" and table.db == schema.database_name)
            )
            if represented and not table.catalog:
                table.set("db", None)  # this is a private validation AST only
            else:
                raise OptimizeError("Qualified table namespace is not represented in source schema")
        if mapping.find(table, raise_on_missing=False) is None:
            raise OptimizeError(f"Table '{table.name}' not found in schema")

    try:
        expression = sqlglot.parse_one(sql, read=dialect)
        qualify(
            expression, dialect=dialect, schema=mapping, infer_schema=False,
            on_qualify=check_source,
        )
    except (SqlglotError, ValueError) as exc:
        return False, [f"SCHEMA_ERROR: {exc}"]
    return True, []
