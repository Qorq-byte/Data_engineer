"""Fail-closed, single-statement SQL policy shared by every execution entry point."""

import re

import sqlglot
from sqlglot import exp

_FORBIDDEN = {
    "PropertyEQ",
    "Into",
    "Lock",
    "Command",
    "Copy",
    "Pragma",
    "Attach",
    "Detach",
    "Insert",
    "Update",
    "Delete",
    "Create",
    "Drop",
    "Alter",
    "Merge",
    "Transaction",
    "Commit",
    "Rollback",
    "Set",
    "Use",
    "Grant",
    "Revoke",
}
_UNSAFE_FUNCTIONS = {
    "load_extension",
    "readfile",
    "writefile",
    "pg_sleep",
    "pg_terminate_backend",
    "pg_cancel_backend",
    "set_config",
    "nextval",
    "setval",
    "dblink",
    "sleep",
    "http_get",
    "http_post",
    "read_csv",
    "read_parquet",
    "read_json",
    "glob",
}
_SAFE_ANONYMOUS = {"strftime", "julianday", "unixepoch", "printf", "typeof"}


def require_read_query(sql: str, dialect: str = "") -> None:
    """Allow only a single relational query without writes or external table functions.

    Unknown user-defined functions require an explicit future policy; they are
    never trusted merely because the outer statement starts with SELECT.
    """
    if re.search(r"/\*(?:!|M!)", sql, re.I):
        raise ValueError("Executable database comments are forbidden")
    # Reject lexical constructs whose meaning varies across database SQL modes.
    if "\\" in sql or "\x00" in sql or re.search(r"--(?=[^\x00-\x20])", sql):
        raise ValueError("Ambiguous SQL quoting or comment syntax is forbidden")
    if re.search(r"/\*(?:(?!\*/).)*?/\*", sql, re.S):
        raise ValueError("Nested SQL comments are forbidden")
    # EXPLAIN without ANALYZE only describes the separately validated query.
    explain = re.match(r"^\s*EXPLAIN\s+(?:QUERY\s+PLAN\s+)?(?=SELECT\b|WITH\b)", sql, re.I)
    parsed_sql = sql[explain.end() :] if explain else sql
    try:
        # sqlglot represents a comment after the final semicolon as a standalone
        # Semicolon expression. It contains no executable statement.
        statements = [s for s in sqlglot.parse(parsed_sql, read=dialect or None)
                      if s is not None and not isinstance(s, exp.Semicolon)]
    except (sqlglot.errors.SqlglotError, ValueError) as exc:
        raise ValueError("SQL could not be parsed safely") from exc
    if len(statements) != 1 or not isinstance(statements[0], exp.Query):
        raise ValueError("Only one read-only SELECT query is allowed")
    for item in statements[0].walk():
        if isinstance(item, exp.Select) and not item.expressions:
            raise ValueError("SELECT must have a projection")
        if type(item).__name__ in _FORBIDDEN or isinstance(item, exp.DDL | exp.DML):
            raise ValueError("Write operations, locks and side effects are forbidden")
        if isinstance(item, exp.Table) and not isinstance(item.this, exp.Identifier):
            raise ValueError("External and table-valued functions are forbidden")
        if isinstance(item, exp.Table) and re.search(
            r"[/\\:]|\.(csv|parquet|json|txt)(\.|$)", item.name, re.I
        ):
            raise ValueError("External file table references are forbidden")
        if isinstance(item, exp.Func):
            name = item.name.lower() if isinstance(item, exp.Anonymous) else item.sql_name().lower()
            if name in _UNSAFE_FUNCTIONS:
                raise ValueError("Side-effect and external functions are forbidden")
            if isinstance(item, exp.Anonymous) and name not in _SAFE_ANONYMOUS:
                raise ValueError(f"Unapproved user-defined function: {name}")
