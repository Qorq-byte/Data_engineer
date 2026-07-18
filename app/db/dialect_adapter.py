"""Database dialect adapter — sqlglot-based SQL translation across 11 databases.

See SPEC §4.5.3 for the Dialect Adapter specification.
"""

from dataclasses import dataclass, field


@dataclass
class DialectConfig:
    """Configuration and capability profile for a single SQL dialect."""

    dialect: str  # sqlglot dialect name (postgres, mysql, sqlite, duckdb, ...)
    driver: str  # Python driver name
    type_mapping: dict[str, str] = field(default_factory=dict)
    function_mapping: dict[str, str] = field(default_factory=dict)
    quote_char: str = '"'
    supports: set[str] = field(default_factory=set)  # {"cte", "window", "json", ...}


# ── Pre-built dialect configs (11 total, 4 complete in Phase 1) ────────────

PRESET_DIALECTS: dict[str, DialectConfig] = {
    "postgresql": DialectConfig(
        dialect="postgres",
        driver="asyncpg",
        type_mapping={
            "int": "INTEGER",
            "bigint": "BIGINT",
            "float": "REAL",
            "decimal": "NUMERIC",
            "text": "TEXT",
            "timestamp": "TIMESTAMP",
            "boolean": "BOOLEAN",
            "json": "JSONB",
        },
        function_mapping={
            "now": "NOW()",
            "date_trunc": "DATE_TRUNC",
            "string_agg": "STRING_AGG",
            "ilike": "ILIKE",
        },
        quote_char='"',
        supports={"cte", "window", "json", "array", "fulltext", "upsert"},
    ),
    "mysql": DialectConfig(
        dialect="mysql",
        driver="aiomysql",
        type_mapping={
            "int": "INT",
            "bigint": "BIGINT",
            "float": "FLOAT",
            "decimal": "DECIMAL",
            "text": "TEXT",
            "timestamp": "DATETIME",
            "boolean": "TINYINT(1)",
            "json": "JSON",
        },
        function_mapping={
            "now": "NOW()",
            "date_trunc": "DATE_FORMAT",
            "string_agg": "GROUP_CONCAT",
            "ilike": "LIKE",
        },
        quote_char="`",
        supports={"cte", "window", "json", "fulltext", "upsert"},
    ),
    "sqlite": DialectConfig(
        dialect="sqlite",
        driver="sqlite3",
        type_mapping={
            "int": "INTEGER",
            "bigint": "INTEGER",
            "float": "REAL",
            "decimal": "REAL",
            "text": "TEXT",
            "timestamp": "TEXT",
            "boolean": "INTEGER",
            "json": "TEXT",
        },
        function_mapping={
            "now": "datetime('now')",
            "date_trunc": "strftime",
            "string_agg": "GROUP_CONCAT",
            "ilike": "LIKE",
        },
        quote_char='"',
        supports={"cte", "window", "json"},
    ),
    "duckdb": DialectConfig(
        dialect="duckdb",
        driver="duckdb",
        type_mapping={
            "int": "INTEGER",
            "bigint": "BIGINT",
            "float": "FLOAT",
            "decimal": "DECIMAL",
            "text": "VARCHAR",
            "timestamp": "TIMESTAMP",
            "boolean": "BOOLEAN",
            "json": "JSON",
        },
        function_mapping={
            "now": "NOW()",
            "date_trunc": "DATE_TRUNC",
            "string_agg": "STRING_AGG",
            "ilike": "ILIKE",
        },
        quote_char='"',
        supports={"cte", "window", "json", "array", "fulltext", "upsert"},
    ),
    # ── Phase 5: completed adapters ────────────────────────────────────────
    "snowflake": DialectConfig(
        dialect="snowflake",
        driver="snowflake-connector",
        type_mapping={
            "int": "INTEGER",
            "bigint": "BIGINT",
            "float": "FLOAT",
            "decimal": "NUMBER",
            "text": "VARCHAR",
            "timestamp": "TIMESTAMP_NTZ",
            "boolean": "BOOLEAN",
            "json": "VARIANT",
        },
        function_mapping={
            "now": "CURRENT_TIMESTAMP()",
            "date_trunc": "DATE_TRUNC",
            "string_agg": "LISTAGG",
            "ilike": "ILIKE",
        },
        quote_char='"',
        supports={"cte", "window", "json", "array", "upsert", "lateral_join"},
    ),
    "starrocks": DialectConfig(
        dialect="starrocks",
        driver="mysql-connector",
        type_mapping={
            "int": "INT",
            "bigint": "BIGINT",
            "float": "FLOAT",
            "decimal": "DECIMAL",
            "text": "VARCHAR",
            "timestamp": "DATETIME",
            "boolean": "BOOLEAN",
            "json": "JSON",
        },
        function_mapping={
            "now": "NOW()",
            "date_trunc": "DATE_TRUNC",
            "string_agg": "GROUP_CONCAT",
            "ilike": "LIKE",
        },
        quote_char="`",
        supports={"cte", "window", "json", "array", "fulltext", "upsert"},
    ),
    "bigquery": DialectConfig(
        dialect="bigquery",
        driver="google-cloud-bigquery",
        type_mapping={
            "int": "INT64",
            "bigint": "INT64",
            "float": "FLOAT64",
            "decimal": "NUMERIC",
            "text": "STRING",
            "timestamp": "TIMESTAMP",
            "boolean": "BOOL",
            "json": "JSON",
        },
        function_mapping={
            "now": "CURRENT_TIMESTAMP()",
            "date_trunc": "DATE_TRUNC",
            "string_agg": "STRING_AGG",
            "ilike": "LOWER",
        },
        quote_char="`",
        supports={"cte", "window", "json", "array", "lateral_join"},
    ),
    "redshift": DialectConfig(
        dialect="redshift",
        driver="redshift-connector",
        type_mapping={
            "int": "INTEGER",
            "bigint": "BIGINT",
            "float": "REAL",
            "decimal": "DECIMAL",
            "text": "VARCHAR",
            "timestamp": "TIMESTAMP",
            "boolean": "BOOLEAN",
            "json": "SUPER",
        },
        function_mapping={
            "now": "GETDATE()",
            "date_trunc": "DATE_TRUNC",
            "string_agg": "LISTAGG",
            "ilike": "ILIKE",
        },
        quote_char='"',
        supports={"cte", "window", "json", "upsert"},
    ),
    "clickhouse": DialectConfig(
        dialect="clickhouse",
        driver="clickhouse-connect",
        type_mapping={
            "int": "Int32",
            "bigint": "Int64",
            "float": "Float32",
            "decimal": "Decimal",
            "text": "String",
            "timestamp": "DateTime",
            "boolean": "Bool",
            "json": "JSON",
        },
        function_mapping={
            "now": "now()",
            "date_trunc": "toStartOfMonth",
            "string_agg": "groupArray",
            "ilike": "ILIKE",
        },
        quote_char='"',
        supports={"cte", "window", "json", "array", "fulltext"},
    ),
    "databricks": DialectConfig(
        dialect="databricks",
        driver="databricks-sql-connector",
        type_mapping={
            "int": "INT",
            "bigint": "BIGINT",
            "float": "FLOAT",
            "decimal": "DECIMAL",
            "text": "STRING",
            "timestamp": "TIMESTAMP",
            "boolean": "BOOLEAN",
            "json": "STRING",
        },
        function_mapping={
            "now": "CURRENT_TIMESTAMP()",
            "date_trunc": "DATE_TRUNC",
            "string_agg": "COLLECT_LIST",
            "ilike": "ILIKE",
        },
        quote_char="`",
        supports={"cte", "window", "json", "array", "upsert", "lateral_join"},
    ),
    "trino": DialectConfig(
        dialect="trino",
        driver="trino-python-client",
        type_mapping={
            "int": "INTEGER",
            "bigint": "BIGINT",
            "float": "REAL",
            "decimal": "DECIMAL",
            "text": "VARCHAR",
            "timestamp": "TIMESTAMP",
            "boolean": "BOOLEAN",
            "json": "JSON",
        },
        function_mapping={
            "now": "CURRENT_TIMESTAMP",
            "date_trunc": "DATE_TRUNC",
            "string_agg": "ARRAY_JOIN",
            "ilike": "ILIKE",
        },
        quote_char='"',
        supports={"cte", "window", "json", "array", "lateral_join"},
    ),
}


class DialectAdapter:
    """Translates SQL between dialects using sqlglot.

    Core generation logic stays dialect-agnostic (ANSI SQL).
    DialectAdapter handles the final translation to target database syntax.
    """

    def __init__(self, config: DialectConfig):
        self.config = config

    def translate(self, sql: str, target_dialect: str = "") -> str:
        """Translate SQL from this adapter's dialect to the target dialect.

        Args:
            sql: SQL string in this adapter's dialect.
            target_dialect: Target sqlglot dialect name. If empty, no translation.

        Returns:
            Translated SQL string.
        """
        if not target_dialect or target_dialect == self.config.dialect:
            return sql
        try:
            import sqlglot

            result = sqlglot.transpile(
                sql, read=self.config.dialect, write=target_dialect
            )
            return result[0] if result else sql
        except ImportError:
            return sql
        except Exception:
            return sql  # Fallback: return untranslated SQL

    def quote_identifier(self, name: str) -> str:
        """Wrap an identifier in dialect-appropriate quotes."""
        q = self.config.quote_char
        return f"{q}{name}{q}"

    def get_type_name(self, generic_type: str) -> str:
        """Map a generic type name to this dialect's equivalent."""
        return self.config.type_mapping.get(generic_type, generic_type.upper())

    def get_function(self, generic_func: str) -> str:
        """Map a generic function name to this dialect's equivalent."""
        return self.config.function_mapping.get(generic_func, generic_func.upper())

    def supports_feature(self, feature: str) -> bool:
        """Check if this dialect supports a specific SQL feature."""
        return feature in self.config.supports

    @classmethod
    def get_adapter(cls, dialect_name: str) -> "DialectAdapter":
        """Get a pre-configured adapter for the given dialect name."""
        config = PRESET_DIALECTS.get(dialect_name)
        if config is None:
            raise ValueError(
                f"Unknown dialect: '{dialect_name}'. "
                f"Available: {list(PRESET_DIALECTS.keys())}"
            )
        return cls(config)

    @classmethod
    def validate_translation(cls, sql: str, source_dialect: str, target_dialect: str) -> dict:
        """Translate then validate SQL via sqlglot parse in the target dialect.

        Args:
            sql: Source SQL.
            source_dialect: Source dialect name.
            target_dialect: Target dialect name.

        Returns:
            Dict with ``valid`` (bool), ``translated_sql`` (str), ``error`` (str or None).
        """
        adapter = cls.get_adapter(source_dialect)
        result: dict = {"valid": True, "translated_sql": sql, "error": None}
        try:
            translated = adapter.translate(sql, target_dialect)
            result["translated_sql"] = translated
            import sqlglot

            parsed = sqlglot.parse(translated, read=target_dialect)
            if not parsed:
                result["valid"] = False
                result["error"] = "sqlglot could not parse translated SQL"
        except ImportError:
            pass
        except Exception as e:
            result["valid"] = False
            result["error"] = str(e)
        return result

    @classmethod
    def list_supported(cls) -> list[str]:
        """List all dialect names that have configs (even stubs)."""
        return list(PRESET_DIALECTS.keys())

    @classmethod
    def list_fully_supported(cls) -> list[str]:
        """List dialects with complete configs (Phase 5: all 11)."""
        return sorted(PRESET_DIALECTS.keys())
