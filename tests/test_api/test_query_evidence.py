"""Check generated SQL comments without permitting extra statements."""

import pytest

from app.core.sql_validator import SQLValidator


@pytest.mark.parametrize("sql, passed", [
    ("SELECT 1;\n-- ASSUMPTION: example input", True),
    ("SELECT 1; SELECT 2;\n-- ASSUMPTION: example input", False),
])
async def test_generated_comments_preserve_single_statement_policy(sql, passed):
    report = await SQLValidator("sqlite").validate(sql)
    assert report.passed is passed
