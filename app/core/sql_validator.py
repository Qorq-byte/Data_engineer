"""SQL Validator — multi-stage SQL validation pipeline.

See SPEC §4.5.1 and implementation-plan §4.7.4 for the full specification.

All five steps are implemented:
  1. Syntax parsing (sqlglot dialect-aware)                    — blocking
  2. Schema reference validation (table/column existence)      — blocking
  3. Type compatibility check (comparison operands)            — warnings only
  4. Performance analysis (static EXPLAIN-style heuristics)    — warnings only
  5. Business rule validation (BusinessRule pattern/enforce)   — warnings only

Per the validation state machine, only Steps 1–2 affect ``passed``;
Steps 3–5 contribute warnings that reduce the score without blocking.
"""

from __future__ import annotations

import re
from typing import Any

import sqlglot
from sqlglot import exp
from sqlglot.errors import ErrorLevel, ParseError

from app.knowledge.rule_engine import _match_pattern
from app.models.query import PlanAnalysis, ValidationReport
from app.models.schema import ColumnSchema, SchemaSnapshot

# sqlglot-recognised dialect names (lowercase keys)
_SQLGLOT_DIALECTS: set[str] = {
    d.name.lower() for d in sqlglot.Dialects if d.name != "DIALECT"
}


def _read_dialect(dialect: str) -> str | None:
    """Return a sqlglot-compatible dialect string, or ``None`` for default."""
    d = dialect.lower()
    if d in ("ansi", "standard", "sql", "generic", ""):
        return None
    if d in _SQLGLOT_DIALECTS:
        return d
    return None


def _read_kwargs(dialect: str = "") -> dict:
    """Get ``read`` kwarg dict for sqlglot, or empty dict."""
    d = _read_dialect(dialect)
    return {"read": d} if d else {}


class SQLValidator:
    """Multi-stage SQL validation — each step is independent and skippable.

    Usage::

        validator = SQLValidator()
        report = await validator.validate(sql, schema)
        if not report.passed:
            print(report.syntax_errors)
    """

    def __init__(self, dialect: str = "") -> None:
        self.dialect = dialect if dialect not in ("ansi",) else ""

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def validate(
        self,
        sql: str,
        schema: SchemaSnapshot | None = None,
        business_rules: list[Any] | None = None,
        *,
        rules: list | None = None,
        skip_syntax: bool = False,
        skip_schema: bool = False,
        skip_types: bool = False,
        skip_performance: bool = False,
        skip_rules: bool = False,
    ) -> ValidationReport:
        """Run the 5-stage validation pipeline.

        Args:
            sql: The SQL string to validate.
            schema: Optional database schema for reference checks.
            business_rules: Business rules for Step 5 (legacy positional alias).
            rules: Business rules for Step 5; takes precedence over
                ``business_rules`` when both are given.
            skip_syntax: Skip syntax parsing (Step 1).
            skip_schema: Skip schema reference check (Step 2).
            skip_types: Skip type compatibility check (Step 3).
            skip_performance: Skip performance analysis (Step 4).
            skip_rules: Skip business rule validation (Step 5).

        Returns:
            A ``ValidationReport`` with per-stage results and overall score.
        """
        syntax_ok = True
        syntax_errors: list[str] = []
        schema_valid = True
        schema_errors: list[str] = []
        type_valid = True
        type_errors: list[str] = []
        warnings: list[str] = []

        # Step 1 — Syntax
        if not skip_syntax:
            syntax_ok, syntax_errors = self._check_syntax(sql)
            if syntax_errors:
                warnings.extend(syntax_errors)

        # Step 2 — Schema references
        if not skip_schema and schema is not None:
            schema_valid, schema_errors = self._check_schema_refs(sql, schema)
            if schema_errors:
                warnings.extend(schema_errors)

        # Step 3 — Type compatibility
        if not skip_types and schema is not None:
            type_valid, type_errors = self._check_types(sql, schema)
            if type_errors:
                warnings.extend(type_errors)

        # Steps 1–3 warnings carry the standard score penalty
        hard_warning_count = len(warnings)

        # Step 4 — Performance analysis (static heuristics, non-blocking)
        plan_analysis: PlanAnalysis | None = None
        if not skip_performance:
            perf_warnings = self._analyze_performance(sql)
            plan_analysis = PlanAnalysis(
                has_full_scan=any("full table scan" in w for w in perf_warnings),
                warnings=perf_warnings,
            )
            warnings.extend(perf_warnings)

        # Step 5 — Business rules (non-blocking)
        business_valid = True
        business_warnings: list[str] = []
        effective_rules = rules if rules is not None else business_rules
        if not skip_rules and effective_rules:
            business_warnings = self._check_rules(sql, effective_rules)
            business_valid = len(business_warnings) == 0
            warnings.extend(business_warnings)

        # Steps 4–5 warnings are advisory → lighter score penalty
        soft_warning_count = len(warnings) - hard_warning_count

        passed = len(syntax_errors) == 0 and len(schema_errors) == 0
        score = self._calculate_score(passed, hard_warning_count, soft_warning_count)

        return ValidationReport(
            passed=passed,
            syntax_ok=syntax_ok,
            syntax_errors=syntax_errors,
            schema_valid=schema_valid,
            schema_errors=schema_errors,
            type_valid=type_valid,
            type_errors=type_errors,
            plan_analysis=plan_analysis,
            business_valid=business_valid,
            business_warnings=business_warnings,
            warnings=warnings,
            score=score,
        )

    # ------------------------------------------------------------------
    # Step 1 — Syntax parsing
    # ------------------------------------------------------------------

    def _check_syntax(self, sql: str) -> tuple[bool, list[str]]:
        """Parse SQL with sqlglot and report syntax errors.

        Uses the configured dialect for accurate parsing.
        """
        if not sql or not sql.strip():
            return False, ["SYNTAX_ERROR: Empty SQL statement"]

        try:
            # Try parsing — sqlglot is lenient by default; raise on errors
            parsed = sqlglot.parse(
                sql, error_level=ErrorLevel.RAISE, **_read_kwargs(self.dialect)
            )
            if not parsed or not parsed[0]:
                return False, ["SYNTAX_ERROR: Unable to parse SQL — empty result"]
        except ParseError as e:
            return False, [f"SYNTAX_ERROR: {e}"]
        except Exception as e:
            return False, [f"SYNTAX_ERROR: {e}"]

        # Additional sanity checks
        errors: list[str] = []
        for statement in parsed:
            if statement is None:
                errors.append("SYNTAX_ERROR: Null statement in parse result")
                continue
            # Check for balanced structure — sqlglot usually handles this
        return len(errors) == 0, errors

    # ------------------------------------------------------------------
    # Step 2 — Schema reference validation
    # ------------------------------------------------------------------

    def _check_schema_refs(
        self, sql: str, schema: SchemaSnapshot
    ) -> tuple[bool, list[str]]:
        """Extract table and column references, validate against schema."""
        errors: list[str] = []
        aliases = self._extract_table_aliases(sql)

        # Extract table names from SQL AST
        tables = self._extract_table_names(sql)
        for table in tables:
            if table not in schema.tables and table not in aliases:
                errors.append(
                    f"SCHEMA_ERROR: Table '{table}' not found in schema"
                )

        # Extract column references
        columns = self._extract_column_refs(sql)

        # For single-table queries, restrict column search to the FROM table
        from_tables = [
            t for t in tables if t in schema.tables
        ]
        single_table_mode = len(from_tables) == 1

        for col_ref in columns:
            if "." in col_ref:
                parts = col_ref.split(".", 1)
                prefix, col_name = parts[0], parts[1]
                # Resolve alias to actual table name
                actual_table = aliases.get(prefix, prefix)
                tbl = schema.get_table(actual_table)
                if tbl is None:
                    errors.append(
                        f"SCHEMA_ERROR: Table '{actual_table}' not found "
                        f"(referenced by column '{col_ref}')"
                    )
                elif col_name != "*" and tbl.get_column(col_name) is None:
                    errors.append(
                        f"SCHEMA_ERROR: Column '{col_name}' not found in "
                        f"table '{actual_table}'"
                    )
            else:
                # Unqualified column — search across tables
                if single_table_mode:
                    # Single-table query: column MUST exist in the FROM table
                    tbl = schema.get_table(from_tables[0])
                    if tbl is not None and col_ref != "*" and tbl.get_column(col_ref) is None:
                        errors.append(
                            f"SCHEMA_ERROR: Column '{col_ref}' not found in "
                            f"table '{from_tables[0]}'"
                        )
                else:
                    # Multi-table or no-table query: search across all tables
                    found = False
                    for t in schema.tables.values():
                        if t.get_column(col_ref) is not None:
                            found = True
                            break
                    if not found and col_ref != "*":
                        errors.append(
                            f"SCHEMA_ERROR: Column '{col_ref}' not found in any table"
                        )

        return len(errors) == 0, errors

    def _extract_table_aliases(self, sql: str) -> dict[str, str]:
        """Build a mapping of alias → real table name from the AST.

        For ``FROM users u JOIN orders o``, returns ``{"u": "users", "o": "orders"}``.
        """
        aliases: dict[str, str] = {}
        try:
            parsed = sqlglot.parse(sql, **_read_kwargs(self.dialect))
        except Exception:
            return aliases

        for statement in parsed:
            if statement is None:
                continue
            for node in statement.walk():
                if isinstance(node, exp.Table):
                    alias = node.alias
                    name = node.name
                    if alias and name:
                        aliases[alias] = name
        return aliases

    def _extract_table_names(self, sql: str) -> list[str]:
        """Extract table names from SQL using sqlglot AST traversal."""
        tables: list[str] = []
        try:
            parsed = sqlglot.parse(sql, **_read_kwargs(self.dialect))
        except Exception:
            return tables

        for statement in parsed:
            if statement is None:
                continue
            for node in statement.walk():
                if isinstance(node, exp.Table):
                    name = node.name
                    if name and name not in tables:
                        tables.append(name)
                # Table aliases (exp.Alias) are separate — not table refs
        return tables

    def _extract_column_refs(self, sql: str) -> list[str]:
        """Extract column references from SQL AST.

        Returns fully qualified refs (``table.column``) where available,
        or bare column names otherwise.
        """
        columns: list[str] = []
        try:
            parsed = sqlglot.parse(sql, **_read_kwargs(self.dialect))
        except Exception:
            return columns

        for statement in parsed:
            if statement is None:
                continue
            for node in statement.walk():
                if isinstance(node, exp.Column):
                    col_name = node.name
                    table_alias = node.table
                    ref = f"{table_alias}.{col_name}" if table_alias else col_name
                    if ref not in columns:
                        columns.append(ref)
        return columns

    # ------------------------------------------------------------------
    # Step 3 — Type compatibility check
    # ------------------------------------------------------------------

    def _check_types(
        self, sql: str, schema: SchemaSnapshot
    ) -> tuple[bool, list[str]]:
        """Check type compatibility in WHERE / JOIN ON comparisons.

        Extracts binary comparisons and verifies that the operand types
        are compatible (e.g., comparing INT to VARCHAR would warn).
        """
        warnings: list[str] = []
        aliases = self._extract_table_aliases(sql)

        comparisons = self._extract_comparisons(sql)
        all_columns: dict[str, ColumnSchema] = {}
        for t in schema.tables.values():
            for c in t.columns:
                all_columns[c.name] = c

        for left, op, right in comparisons:
            left_type = self._resolve_expr_type(left, all_columns, aliases)
            right_type = self._resolve_expr_type(right, all_columns, aliases)

            # Skip if either side couldn't be resolved
            if left_type is None or right_type is None:
                continue

            if not self._types_compatible(left_type, right_type):
                warnings.append(
                    f"TYPE_WARNING: Comparing {left_type} with {right_type} "
                    f"in '{left} {op} {right}'"
                )

        return True, warnings  # type warnings are non-blocking

    @staticmethod
    def _resolve_expr_type(
        ref: str,
        columns: dict[str, ColumnSchema],
        aliases: dict[str, str],
    ) -> str | None:
        """Resolve a column or literal to its SQL type string.

        Handles:
          - Qualified column refs (``u.name``) — resolves aliases.
          - Unqualified column refs (``id``) — looks up in all columns.
          - String literals (``'value'``) → ``VARCHAR``.
          - Numeric literals (``123``, ``1.5``) → ``INTEGER`` / ``DECIMAL``.
          - NULL → ``NULL``.
          - Boolean literals → ``BOOLEAN``.
        """
        clean = ref.strip()

        # String literal
        if (clean.startswith("'") and clean.endswith("'")) or clean.startswith(
            "'"
        ):
            return "VARCHAR"

        # Numeric literal
        try:
            float(clean)
            return "DECIMAL" if "." in clean else "INTEGER"
        except (ValueError, TypeError):
            pass

        # NULL
        if clean.upper() == "NULL":
            return "NULL"

        # Boolean literal
        if clean.upper() in ("TRUE", "FALSE"):
            return "BOOLEAN"

        # Column reference — strip quotes / backticks
        clean = clean.strip("`\"'[]")

        # Qualified column: alias.column → resolve alias
        if "." in clean:
            parts = clean.split(".", 1)
            prefix, col_name = parts[0], parts[1]
            # Resolve alias to actual table
            prefix = aliases.get(prefix, prefix)
            # Search for column in the specific table
            for col in columns.values():
                if col.name == col_name:
                    return col.type
            return None

        # Unqualified column
        col = columns.get(clean)
        return col.type if col else None

    def _extract_comparisons(
        self, sql: str
    ) -> list[tuple[str, str, str]]:
        """Extract binary comparisons from WHERE / JOIN ON clauses.

        Returns a list of ``(left_expression, operator, right_expression)``
        tuples using the string representation from the AST.
        """
        comparisons: list[tuple[str, str, str]] = []
        try:
            parsed = sqlglot.parse(sql, **_read_kwargs(self.dialect))
        except Exception:
            return comparisons

        for statement in parsed:
            if statement is None:
                continue
            for node in statement.walk():
                if isinstance(node, (exp.EQ, exp.NEQ, exp.GT, exp.GTE,
                                     exp.LT, exp.LTE, exp.Like)):
                    left = node.left.sql() if hasattr(node.left, "sql") else str(node.left)
                    right = node.right.sql() if hasattr(node.right, "sql") else str(node.right)
                    op_str = node.sql_name() if hasattr(node, "sql_name") else type(node).__name__
                    comparisons.append((left, op_str, right))
                elif isinstance(node, exp.Between):
                    left = node.this.sql() if hasattr(node.this, "sql") else str(node.this)
                    low = node.args.get("low")
                    low_str = low.sql() if low and hasattr(low, "sql") else str(low) if low else "?"
                    comparisons.append((left, "BETWEEN", low_str))
                elif isinstance(node, exp.In):
                    left = node.this.sql() if hasattr(node.this, "sql") else str(node.this)
                    comparisons.append((left, "IN", "..."))
        return comparisons

    @staticmethod
    def _types_compatible(type_a: str, type_b: str) -> bool:
        """Heuristic type compatibility check.

        Types are considered compatible if they share the same broad
        category: numeric, string, temporal, boolean, or binary.

        This is deliberately conservative — it raises warnings for
        cross-category comparisons (e.g., INT vs VARCHAR) but allows
        implicit conversions within a category.
        """
        a = type_a.upper().replace(" ", "")
        b = type_b.upper().replace(" ", "")

        # Strip type parameters (e.g. DECIMAL(10,2) → DECIMAL)
        a = re.sub(r"\(.*\)", "", a)
        b = re.sub(r"\(.*\)", "", b)

        # Normalize to base types
        a_base = _BASE_TYPE.get(a, a)
        b_base = _BASE_TYPE.get(b, b)

        # Same category → compatible
        return any(
            a_base in category and b_base in category
            for category in _TYPE_CATEGORIES
        )

    # ------------------------------------------------------------------
    # Step 4 — Performance analysis (static heuristics)
    # ------------------------------------------------------------------

    def _analyze_performance(self, sql: str) -> list[str]:
        """Static EXPLAIN-style performance heuristics — no DB connection.

        Detects common performance hazards:
          - Missing WHERE clause on a FROM query (full table scan risk)
          - ``SELECT *`` (no column pruning)
          - ``LIKE '%...'`` leading wildcard (index cannot be used)
          - Non-aggregate query without LIMIT (unbounded result set)
          - JOIN without ON/USING condition (cartesian product)

        Returns:
            List of ``PERF_WARNING: ...`` strings (never blocks validation).
        """
        warnings: list[str] = []
        try:
            parsed = sqlglot.parse(sql, **_read_kwargs(self.dialect))
        except Exception:
            return warnings  # unparseable SQL is Step 1's problem

        for statement in parsed:
            if statement is None:
                continue

            has_from = statement.find(exp.From) is not None

            # 1. No WHERE + has FROM → full table scan risk
            if has_from and statement.find(exp.Where) is None:
                warnings.append(
                    "PERF_WARNING: No WHERE clause, potential full table scan"
                )

            # 2. SELECT * → no column pruning
            if self._has_star_projection(statement):
                warnings.append(
                    "PERF_WARNING: SELECT * fetches all columns; "
                    "specify only the columns you need"
                )

            # 3. LIKE with leading wildcard → index cannot be used
            for like in statement.find_all(exp.Like, exp.ILike):
                pattern = like.expression
                if (
                    isinstance(pattern, exp.Literal)
                    and pattern.is_string
                    and str(pattern.this).startswith("%")
                ):
                    warnings.append(
                        f"PERF_WARNING: Leading wildcard LIKE "
                        f"'{pattern.this}' prevents index usage"
                    )

            # 4. Non-aggregate query without LIMIT → unbounded result set
            is_aggregate = (
                statement.find(exp.Group) is not None
                or statement.find(exp.AggFunc) is not None
            )
            if (
                has_from
                and not is_aggregate
                and statement.find(exp.Limit) is None
            ):
                warnings.append(
                    "PERF_WARNING: No LIMIT on non-aggregate query; "
                    "result set size is unbounded"
                )

            # 5. JOIN without ON/USING → cartesian product
            for join in statement.find_all(exp.Join):
                if (join.method or "").upper() == "NATURAL":
                    continue  # NATURAL JOIN carries an implicit condition
                if not join.args.get("on") and not join.args.get("using"):
                    warnings.append(
                        f"PERF_WARNING: JOIN on '{join.this.sql()}' has no "
                        f"ON/USING condition; potential cartesian product"
                    )

        return warnings

    @staticmethod
    def _has_star_projection(statement: exp.Expression) -> bool:
        """True if any SELECT in the statement projects ``*`` or ``t.*``."""
        for select in statement.find_all(exp.Select):
            for projection in select.expressions:
                if isinstance(projection, exp.Star):
                    return True
                if isinstance(projection, exp.Column) and isinstance(
                    projection.this, exp.Star
                ):
                    return True
        return False

    # ------------------------------------------------------------------
    # Step 5 — Business rule validation
    # ------------------------------------------------------------------

    def _check_rules(self, sql: str, rules: list[Any]) -> list[str]:
        """Validate SQL against business rules (non-blocking).

        Reuses the pattern matcher from :mod:`app.knowledge.rule_engine`.
        For each :class:`~app.models.domain.BusinessRule`:

        - A rule with a ``pattern`` applies when the pattern matches the SQL
          (case-insensitive regex). A rule without a pattern applies always.
        - If an applicable rule has ``enforce`` directives, each unmet
          directive yields a ``RULE_WARNING``.
        - If an applicable rule has *no* directives, the pattern match itself
          flags the rule (the pattern describes a discouraged construct).

        Returns:
            List of ``RULE_WARNING: ...`` strings (never blocks validation).
        """
        warnings: list[str] = []

        for rule in rules or []:
            pattern = getattr(rule, "pattern", "") or ""
            enforce = getattr(rule, "enforce", None) or []
            rule_id = getattr(rule, "id", "?")
            description = getattr(rule, "description", "")

            if pattern:
                score, _groups = _match_pattern(pattern, sql)
                if score <= 0.0:
                    continue  # rule does not apply to this SQL
                if not enforce:
                    warnings.append(
                        f"RULE_WARNING: [{rule_id}] Rule triggered: {description}"
                    )
                    continue

            for directive in enforce:
                unmet = self._check_enforcement(sql, directive)
                if unmet:
                    warnings.append(f"RULE_WARNING: [{rule_id}] {unmet}")

        return warnings

    @staticmethod
    def _check_enforcement(sql: str, directive: str) -> str | None:
        """Check a single ``key=value`` enforcement directive against SQL.

        Supported directives:
          - ``require=<token>``       — SQL must contain the token
          - ``forbid=<token>``        — SQL must not contain the token
          - ``require_where=true``    — SQL must have a WHERE clause
          - ``require_limit=true``    — SQL must have a LIMIT clause
          - ``require_time_range=true`` — SQL must filter on a time column
          - ``precision=<n>``         — SQL must use ROUND(...)

        Unknown directive keys are ignored (informational only, e.g.
        ``timezone=Asia/Shanghai`` guides generation, not validation).

        Returns:
            A violation message, or ``None`` if the directive is satisfied.
        """
        key, _, value = directive.partition("=")
        key = key.strip().lower()
        value = value.strip()
        upper_sql = sql.upper()

        match key:
            case "require":
                if value and value.upper() not in upper_sql:
                    return f"Required expression '{value}' missing from SQL"
            case "forbid":
                if value and value.upper() in upper_sql:
                    return f"Forbidden expression '{value}' present in SQL"
            case "require_where":
                if value.lower() == "true" and not re.search(
                    r"\bWHERE\b", sql, re.IGNORECASE
                ):
                    return "WHERE clause required but missing"
            case "require_limit":
                if value.lower() == "true" and not re.search(
                    r"\bLIMIT\b", sql, re.IGNORECASE
                ):
                    return "LIMIT clause required but missing"
            case "require_time_range":
                if value.lower() == "true" and not re.search(
                    r"(?:date|time|created|updated)\w*\s*(?:=|>|<|>=|<=|BETWEEN)",
                    sql,
                    re.IGNORECASE,
                ):
                    return "Time range filter required but missing"
            case "precision":
                if value and not re.search(r"\bROUND\s*\(", sql, re.IGNORECASE):
                    return f"Precision {value} required — use ROUND(...)"

        return None

    # ------------------------------------------------------------------
    # Scoring
    # ------------------------------------------------------------------

    @staticmethod
    def _calculate_score(
        passed: bool, warning_count: int, soft_warning_count: int = 0
    ) -> float:
        """Compute a validation score (0.0 – 1.0).

        - Passed, 0 warnings  → 1.0
        - Passed, N warnings  → 1.0 - 0.05 * N - 0.02 * soft (min 0.5)
        - Not passed          → 0.0

        Steps 1–3 warnings carry the standard 0.05 penalty; advisory
        Steps 4–5 warnings (``soft_warning_count``) deduct only 0.02 each.
        """
        if not passed:
            return 0.0
        return max(0.5, 1.0 - 0.05 * warning_count - 0.02 * soft_warning_count)


# ── Type category tables ───────────────────────────────────────────────

# Map common SQL type names to canonical base types
_BASE_TYPE: dict[str, str] = {
    # Integers
    "INT": "INTEGER",
    "INTEGER": "INTEGER",
    "BIGINT": "INTEGER",
    "SMALLINT": "INTEGER",
    "TINYINT": "INTEGER",
    "MEDIUMINT": "INTEGER",
    "SERIAL": "INTEGER",
    "BIGSERIAL": "INTEGER",
    "INT2": "INTEGER",
    "INT4": "INTEGER",
    "INT8": "INTEGER",
    # Floats
    "FLOAT": "FLOAT",
    "REAL": "FLOAT",
    "DOUBLE": "FLOAT",
    "DOUBLEPRECISION": "FLOAT",
    "FLOAT4": "FLOAT",
    "FLOAT8": "FLOAT",
    # Decimal
    "DECIMAL": "DECIMAL",
    "NUMERIC": "DECIMAL",
    "DEC": "DECIMAL",
    "NUMBER": "DECIMAL",
    "MONEY": "DECIMAL",
    # Strings
    "VARCHAR": "STRING",
    "CHAR": "STRING",
    "TEXT": "STRING",
    "NVARCHAR": "STRING",
    "NCHAR": "STRING",
    "CLOB": "STRING",
    "STRING": "STRING",
    "UUID": "STRING",
    "ENUM": "STRING",
    "NAME": "STRING",
    # Temporal
    "DATE": "TEMPORAL",
    "TIME": "TEMPORAL",
    "TIMESTAMP": "TEMPORAL",
    "TIMESTAMPTZ": "TEMPORAL",
    "TIMETZ": "TEMPORAL",
    "DATETIME": "TEMPORAL",
    "INTERVAL": "TEMPORAL",
    # Boolean
    "BOOL": "BOOLEAN",
    "BOOLEAN": "BOOLEAN",
    # Binary
    "BLOB": "BINARY",
    "BYTEA": "BINARY",
    "BINARY": "BINARY",
    "VARBINARY": "BINARY",
    # JSON
    "JSON": "JSON",
    "JSONB": "JSON",
}

_TYPE_CATEGORIES: list[set[str]] = [
    {"INTEGER", "FLOAT", "DECIMAL"},   # numeric
    {"STRING", "JSON"},                 # text-like
    {"TEMPORAL"},                       # time
    {"BOOLEAN"},                        # bool
    {"BINARY"},                         # binary
]
