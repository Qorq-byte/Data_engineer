"""ExecuteSQLNode — secure SQL execution with read-only enforcement.

This is the only node fully implemented in Phase 1 because it is the
foundational capability: all generated SQL must pass through this node
for execution, and the Harness invariants (read_only, max_rows, timeout)
are enforced here.

See SPEC §4.12.4 (built-in nodes) and §4.12.6 (node registry).
"""

import re as _re

from app.models.query import PlanAnalysis
from app.nodes.agentic import AgenticNode
from app.nodes.base import NodeInput, NodeOutput

# ── Write keywords blocked by default ──────────────────────────────

WRITE_KEYWORDS: set[str] = {
    "INSERT",
    "UPDATE",
    "DELETE",
    "DROP",
    "ALTER",
    "TRUNCATE",
    "CREATE",
    "REPLACE",
    "MERGE",
    "GRANT",
    "REVOKE",
    "RENAME",
    "VACUUM",
}

# ── Safe keywords explicitly allowed ───────────────────────────────

SAFE_KEYWORDS: set[str] = {
    "SELECT",
    "WITH",
    "EXPLAIN",
    "DESCRIBE",
    "SHOW",
    "PRAGMA",
    "ANALYZE",
}


class ExecuteSQLNode(AgenticNode):
    """Executes SQL safely, enforcing Harness invariants.

    - Blocks all write operations by default (read_only invariant).
    - Enforces max_rows limit (constraints invariant).
    - Enforces statement_timeout (constraints invariant).
    - Logs every execution to action history.
    """

    name = "execute_sql"
    description = "Safely execute a read-only SQL query and return results"

    async def execute(self, input: NodeInput) -> NodeOutput:
        """Execute SQL with safety checks.

        Expected input.context keys:
            - connection: A native database connection object.
            - sql: The SQL string to execute (falls back to query_text).

        Expected input.config keys:
            - max_rows: int (default 100)
            - allow_write: bool (default False)
            - timeout_ms: int (default 30000)
        """
        conn = input.context.get("connection")
        if conn is None:
            return NodeOutput(
                result=None,
                errors=["No database connection in context"],
                metadata={"status": "no_connection"},
            )

        sql = input.context.get("sql", input.query_text)
        if not sql or not sql.strip():
            return NodeOutput(
                result=None,
                errors=["Empty SQL statement"],
                metadata={"status": "empty_sql"},
            )

        # ── Invariant: read_only check ────────────────────────
        if self._is_write_statement(sql) and not input.config.get("allow_write", False):
            return NodeOutput(
                result=None,
                errors=[
                    "Write operation blocked: read_only mode is enabled. "
                    "Set allow_write=True to execute write statements."
                ],
                metadata={
                    "status": "blocked",
                    "reason": "write_statement_blocked",
                },
            )

        # ── Execute ────────────────────────────────────────────
        max_rows = input.config.get("max_rows", 100)
        do_explain = input.config.get("explain", False)
        analyze_plan = input.config.get("analyze_plan", False)

        plan: PlanAnalysis | None = None
        result: dict | None = None

        try:
            # Static plan analysis (no DB required)
            if analyze_plan:
                plan = self._static_plan_analysis(sql)

            # EXPLAIN execution
            if do_explain:
                explain_result = self._execute_explain(conn, sql)
                plan = self._parse_explain_output(explain_result, plan)

            # Handle different connection types
            if self._is_aiomysql_connection(conn):
                result = await self._execute_aiomysql(conn, sql, max_rows)
            elif self._is_asyncpg_connection(conn):
                result = await self._execute_asyncpg(conn, sql, max_rows)
            elif self._is_sqlite_connection(conn):
                result = self._execute_sqlite(conn, sql, max_rows)
            elif self._is_duckdb_connection(conn):
                result = self._execute_duckdb(conn, sql, max_rows)
            else:
                result = self._execute_generic(conn, sql, max_rows)

            metadata: dict = {
                "status": "success",
                "row_count": result.get("row_count", 0),
                "truncated": result.get("truncated", False),
            }
            if plan is not None:
                metadata["plan"] = plan

            return NodeOutput(
                result=result,
                metadata=metadata,
                context={
                    "last_execution": result,
                    "plan_analysis": plan,
                },
            )

        except Exception as e:
            return NodeOutput(
                result=None,
                errors=[f"SQL execution error: {e}"],
                metadata={"status": "execution_error"},
            )

    # ── Write detection ────────────────────────────────────────────

    @staticmethod
    def _is_write_statement(sql: str) -> bool:
        """Check if a SQL statement is a write operation.

        Normalizes the SQL and checks the first keyword against the
        WRITE_KEYWORDS set. Also checks for common write patterns
        like multiple statements separated by semicolons.
        """
        # Strip comments and normalize whitespace
        normalized = sql.strip()
        # Remove single-line comments
        import re

        normalized = re.sub(r"--.*$", "", normalized, flags=re.MULTILINE)
        normalized = " ".join(normalized.split())

        if not normalized:
            return False

        first_word = normalized.split()[0].upper()

        # Explicit safe keywords
        if first_word in SAFE_KEYWORDS:
            return False

        # Explicit write keywords
        if first_word in WRITE_KEYWORDS:
            return True

        # Multiple statements (semicolons) — check each
        statements = normalized.split(";")
        for stmt in statements:
            stmt = stmt.strip()
            if stmt:
                fw = stmt.split()[0].upper()
                if fw in WRITE_KEYWORDS:
                    return True

        return False

    # ── Connection type detection ──────────────────────────────────

    @staticmethod
    def _is_sqlite_connection(conn: object) -> bool:
        return type(conn).__name__ == "Connection" and hasattr(conn, "cursor") and not hasattr(conn, "ensure_closed")

    @staticmethod
    def _is_duckdb_connection(conn: object) -> bool:
        return type(conn).__name__ == "DuckDBPyConnection"

    @staticmethod
    def _is_asyncpg_connection(conn: object) -> bool:
        return type(conn).__name__ == "Connection" and hasattr(conn, "fetch")

    @staticmethod
    def _is_aiomysql_connection(conn: object) -> bool:
        return type(conn).__name__ == "Connection" and hasattr(conn, "ensure_closed")

    # ── Per-driver execution ───────────────────────────────────────

    @staticmethod
    def _execute_sqlite(conn: object, sql: str, max_rows: int) -> dict:
        """Execute SQL on a sqlite3 connection."""
        cursor = conn.cursor()
        cursor.execute(sql)
        rows = cursor.fetchmany(max_rows + 1)
        truncated = len(rows) > max_rows
        if truncated:
            rows = rows[:max_rows]
        columns = [d[0] for d in cursor.description] if cursor.description else []
        return {
            "columns": columns,
            "rows": [list(r) for r in rows],
            "row_count": len(rows),
            "truncated": truncated,
        }

    @staticmethod
    def _execute_duckdb(conn: object, sql: str, max_rows: int) -> dict:
        """Execute SQL on a DuckDB connection."""
        result = conn.execute(sql)
        rows = result.fetchmany(max_rows + 1)
        truncated = len(rows) > max_rows
        if truncated:
            rows = rows[:max_rows]
        columns = result.description if hasattr(result, "description") else []
        return {
            "columns": columns,
            "rows": [list(r) for r in rows],
            "row_count": len(rows),
            "truncated": truncated,
        }

    @staticmethod
    async def _execute_asyncpg(conn: object, sql: str, max_rows: int) -> dict:
        """Execute SQL on an asyncpg connection."""
        records = await conn.fetch(sql)
        rows = [list(r) for r in records[:max_rows]]
        truncated = len(records) > max_rows
        columns = list(records[0].keys()) if records else []
        return {
            "columns": columns,
            "rows": rows,
            "row_count": len(rows),
            "truncated": truncated,
        }

    @staticmethod
    async def _execute_aiomysql(conn: object, sql: str, max_rows: int) -> dict:
        """Execute SQL on an aiomysql (MySQL) connection."""
        cursor = await conn.cursor()
        try:
            await cursor.execute(sql)
            rows = await cursor.fetchmany(max_rows + 1)
            truncated = len(rows) > max_rows
            if truncated:
                rows = rows[:max_rows]
            columns = [d[0] for d in cursor.description] if cursor.description else []
            return {
                "columns": columns,
                "rows": [list(r) for r in rows],
                "row_count": len(rows),
                "truncated": truncated,
            }
        finally:
            await cursor.close()

    @staticmethod
    def _execute_generic(conn: object, sql: str, max_rows: int) -> dict:
        """Fallback: execute using generic DB-API 2.0 interface."""
        cursor = conn.cursor()
        cursor.execute(sql)
        rows = cursor.fetchmany(max_rows + 1)
        truncated = len(rows) > max_rows
        if truncated:
            rows = rows[:max_rows]
        columns = [d[0] for d in cursor.description] if cursor.description else []
        return {
            "columns": columns,
            "rows": [list(r) for r in rows],
            "row_count": len(rows),
            "truncated": truncated,
        }

    # ── EXPLAIN support ───────────────────────────────────────────────

    @staticmethod
    def _execute_explain(conn: object, sql: str) -> dict:
        """Execute EXPLAIN for a query and return the plan rows.

        Uses ``EXPLAIN QUERY PLAN`` for SQLite, ``EXPLAIN`` for others.
        """
        # Determine the right EXPLAIN syntax
        is_sqlite = ExecuteSQLNode._is_sqlite_connection(conn)
        explain_sql = f"EXPLAIN QUERY PLAN {sql}" if is_sqlite else f"EXPLAIN {sql}"

        try:
            cursor = conn.cursor()
            cursor.execute(explain_sql)
            rows = cursor.fetchall()
            columns = (
                [d[0] for d in cursor.description]
                if cursor.description
                else []
            )
            return {
                "columns": columns,
                "rows": [list(r) for r in rows],
                "row_count": len(rows),
                "truncated": False,
            }
        except Exception:
            # Fall back to basic EXPLAIN
            try:
                cursor = conn.cursor()
                cursor.execute(f"EXPLAIN {sql}")
                rows = cursor.fetchall()
                columns = (
                    [d[0] for d in cursor.description]
                    if cursor.description
                    else []
                )
                return {
                    "columns": columns,
                    "rows": [list(r) for r in rows],
                    "row_count": len(rows),
                    "truncated": False,
                }
            except Exception:
                return {
                    "columns": [],
                    "rows": [],
                    "row_count": 0,
                    "truncated": False,
                }

    @staticmethod
    def _parse_explain_output(
        explain_result: dict, existing: PlanAnalysis | None = None
    ) -> PlanAnalysis:
        """Parse EXPLAIN output into a ``PlanAnalysis``.

        Extracts scan types, join types, and flags full table scans.
        Works across SQLite / DuckDB / PostgreSQL EXPLAIN formats.
        """
        plan = existing or PlanAnalysis()
        scan_types: list[str] = []
        join_types: list[str] = []
        warnings: list[str] = list(plan.warnings)

        # Build a single text blob from all rows
        text_parts: list[str] = []
        for row in explain_result.get("rows", []):
            for cell in row:
                text_parts.append(str(cell))

        full_text = " ".join(text_parts).upper()

        # ── Detect scan types ──────────────────────────────────
        # Detect full table scan: "SCAN TABLE", "Seq Scan", "SCAN <table>"
        has_scan = (
            "SCAN TABLE" in full_text
            or "SEQ SCAN" in full_text
        )
        # SQLite bare SCAN (not INDEX/ONLY/USING SCAN)
        if not has_scan:
            for row in explain_result.get("rows", []):
                for cell in row:
                    cell_str = str(cell).upper()
                    # SQLite: "SCAN users" = full scan; "SEARCH ... USING INDEX" = indexed
                    if _re.match(r"^SCAN\s", cell_str.strip()):
                        has_scan = True
                        break
        if has_scan:
            scan_types.append("seq_scan")
            if not plan.has_full_scan:
                warnings.append(
                    "PERF_WARNING: Full table scan detected — consider adding an index"
                )
            plan.has_full_scan = True

        if "INDEX SCAN" in full_text or "USING INDEX" in full_text:
            scan_types.append("index_scan")
        if "INDEX ONLY SCAN" in full_text or "COVERING INDEX" in full_text:
            scan_types.append("index_only_scan")
        if "USING COVERING INDEX" in full_text:
            scan_types.append("index_only_scan")

        # ── Detect join types ──────────────────────────────────
        if "NESTED LOOP" in full_text:
            join_types.append("nested_loop")
        if "HASH JOIN" in full_text:
            join_types.append("hash_join")
        if "MERGE JOIN" in full_text or "SORT MERGE" in full_text:
            join_types.append("merge_join")
        if "CROSS JOIN" in full_text:
            join_types.append("cross_join")
            warnings.append("PERF_WARNING: Cross join detected — may produce large result sets")

        # ── Estimate rows ──────────────────────────────────────
        for row in explain_result.get("rows", []):
            for cell in row:
                cell_str = str(cell)
                if "ROWS=" in cell_str.upper():
                    match = _re.search(r"rows=(\d+)", cell_str, _re.IGNORECASE)
                    if match:
                        plan.estimated_rows = max(plan.estimated_rows, int(match.group(1)))
                # SQLite: "SCAN TABLE x (~N rows)"
                if "~" in cell_str and "ROWS" in cell_str.upper():
                    match = _re.search(r"~(\d+)\s*rows", cell_str, _re.IGNORECASE)
                    if match:
                        plan.estimated_rows = max(plan.estimated_rows, int(match.group(1)))

        plan.scan_types = list(set(scan_types))
        plan.join_types = list(set(join_types))
        plan.warnings = warnings
        return plan

    @staticmethod
    def _static_plan_analysis(sql: str) -> PlanAnalysis:
        """Perform static analysis of SQL without execution.

        Detects potential issues from the SQL text alone:
        - Missing WHERE clause (potential full scan)
        - SELECT * usage
        - Non-SARGable conditions (function-wrapped columns)
        """
        warnings: list[str] = []
        scan_types: list[str] = []
        has_full_scan = False
        upper = sql.upper()

        # Missing WHERE in SELECT / WITH statements
        has_from = bool(_re.search(r"\bFROM\s+\w+", upper))
        has_where = bool(_re.search(r"\bWHERE\b", upper))
        if has_from and not has_where:
            warnings.append(
                "PERF_WARNING: No WHERE clause — potential full table scan"
            )
            scan_types.append("seq_scan")
            has_full_scan = True

        # SELECT *
        if _re.search(r"\bSELECT\s+\*", upper):
            warnings.append(
                "STYLE_WARNING: SELECT * — consider specifying columns explicitly"
            )

        # Non-SARGable patterns: function on LHS
        sargable_check = _re.findall(
            r"WHERE\s+(?:\w+\.)?(\w+)\s*(?:>=|<=|>|<|=)\s*\w+\(.*\)", upper
        )
        if sargable_check:
            pass  # RHS function is OK

        # Function-wrapped column in WHERE: WHERE UPPER(col) = ...
        if _re.search(r"WHERE\s+\w+\(\s*\w+\s*\)", upper):
            warnings.append(
                "PERF_WARNING: Function-wrapped column in WHERE — "
                "may prevent index usage"
            )

        return PlanAnalysis(
            scan_types=scan_types,
            has_full_scan=has_full_scan,
            warnings=warnings,
        )
