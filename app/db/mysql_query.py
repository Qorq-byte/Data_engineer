"""Cap MySQL result rows without rewriting projections or implicit column labels."""

import re

import sqlglot
from sqlglot import TokenType, exp

from app.security.sql_policy import require_read_query


def bounded_mysql_query(sql: str, row_limit: int) -> str:
    """Apply a server result cap to one validated SELECT, preserving smaller LIMITs.

    Source edits preserve MySQL's expression-derived column names and comments.
    Only literal integer LIMIT counts are supported; parameters/expressions fail
    closed, as this execution path does not accept query parameters. This bounds
    result rows, not row bytes or the server's scan/sort/intermediate work.
    """
    if row_limit < 1:
        raise ValueError("MySQL result row limit must be positive")
    require_read_query(sql, dialect="mysql")
    statements = [s for s in sqlglot.parse(sql, read="mysql")
                  if s is not None and not isinstance(s, exp.Semicolon)]
    statement = statements[0]
    if not isinstance(statement, exp.Query):
        raise ValueError("MySQL result cap requires a SELECT query")
    limit = statement.args.get("limit")
    if limit is not None:
        count = limit.expression
        if (not isinstance(limit, exp.Limit) or not isinstance(count, exp.Literal)
                or count.is_string or not re.fullmatch(r"[0-9]+", count.this)):
            raise ValueError("MySQL result cap requires a literal integer LIMIT")
        start, end = count.meta.get("start"), count.meta.get("end")
        if (not isinstance(start, int) or not isinstance(end, int)
                or sql[start:end + 1] != count.this):
            raise ValueError("Unable to safely locate MySQL LIMIT")
        if int(count.this) <= row_limit:
            return sql
        bounded = sql[:start] + str(row_limit) + sql[end + 1:]
    else:
        tokens = sqlglot.Dialect.get_or_raise("mysql").tokenize(sql)
        last = next(t for t in reversed(tokens) if t.token_type != TokenType.SEMICOLON)
        end = last.end + 1
        bounded = sql[:end] + f" LIMIT {row_limit}" + sql[end:]
    require_read_query(bounded, dialect="mysql")
    return bounded
