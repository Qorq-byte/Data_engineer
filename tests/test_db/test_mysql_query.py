"""SQL boundary regressions: preserve source semantics and refuse unsafe fallback."""

import pytest

from app.db.mysql_query import bounded_mysql_query
from app.security.sql_policy import require_read_query


@pytest.mark.parametrize("sql", [
    "SELECT id FROM orders LIMIT ?", "SELECT id FROM orders LIMIT @count",
    "SELECT id FROM orders LIMIT 1+2", "SELECT id FROM orders LIMIT -1",
    "SELECT 1; SELECT 2", "SELECT 1; /* tail */ DELETE FROM orders",
    "SELECT 1 /*! UNION SELECT 2 */", "SELECT id INTO OUTFILE 'out' FROM orders",
    "SELECT id FROM orders FOR UPDATE", "EXPLAIN SELECT id FROM orders",
])
def test_mysql_cap_never_falls_back_to_unbounded_or_unsafe_sql(sql):
    with pytest.raises(ValueError):
        bounded_mysql_query(sql, 3)


@pytest.mark.parametrize("sql", [
    "SELECT id FROM orders; -- tail", "SELECT id FROM orders; /* tail */",
    "SELECT id FROM orders /* before */; /* after */",
])
def test_policy_accepts_comment_only_semicolon_but_not_second_statement(sql):
    require_read_query(sql)
    require_read_query(sql, dialect="mysql")
    with pytest.raises(ValueError):
        require_read_query(sql + "\nDELETE FROM orders")


def test_cap_only_edits_limit_literal_keeps_projection_comments_and_offset():
    sql = "SELECT id+1, 'LIMIT 999' FROM orders LIMIT /* count */ 999 OFFSET 2; -- tail"
    assert bounded_mysql_query(sql, 3) == sql.replace("*/ 999", "*/ 3")


def test_invalid_cap_fails_before_execution():
    with pytest.raises(ValueError):
        bounded_mysql_query("SELECT 1", 0)
