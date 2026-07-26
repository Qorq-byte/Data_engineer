"""SQL utility API endpoints — explain, translate, format, validate."""

from __future__ import annotations

import re

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

router = APIRouter()

# Dialect name normalization: map common names to sqlglot-compatible names
_DIALECT_NORMALIZE: dict[str, str] = {
    "postgresql": "postgres",
    "postgres": "postgres",
    "mysql": "mysql",
    "sqlite": "sqlite",
    "duckdb": "duckdb",
    "clickhouse": "clickhouse",
    "snowflake": "snowflake",
    "bigquery": "bigquery",
    "starrocks": "starrocks",
    "redshift": "redshift",
    "databricks": "databricks",
    "trino": "trino",
    "ansi": "",
}


def _normalize_dialect(dialect: str) -> str:
    """Normalize a dialect name to sqlglot-compatible format."""
    if not dialect:
        return ""
    return _DIALECT_NORMALIZE.get(dialect.lower(), dialect)


class SQLRequest(BaseModel):
    sql: str = Field(..., min_length=1)
    dialect: str = Field(default="ansi")
    target_dialect: str = Field(default="")


@router.post("/sql/explain")
async def explain_sql(body: SQLRequest) -> dict:
    """Analyse SQL structure and produce a human-readable execution plan."""
    try:
        import sqlglot
        from sqlglot import exp

        dialect = _normalize_dialect(body.dialect) if body.dialect else None
        parsed = sqlglot.parse(body.sql, dialect=dialect)
        if not parsed:
            raise HTTPException(status_code=400, detail="Unable to parse SQL")
        tree = parsed[0]
        tables: list[str] = []
        joins: list[dict] = []
        where_conditions: list[str] = []
        order_by_columns: list[str] = []
        has_aggregation = False
        has_subquery = False
        has_cte = False
        for node in tree.walk():
            if isinstance(node, exp.Table):
                tables.append(node.name)
            elif isinstance(node, exp.Join):
                table_name = (
                    node.this.name
                    if isinstance(node.this, exp.Table)
                    else str(node.this)
                )
                joins.append({"kind": node.kind or "JOIN", "table": table_name})
            elif isinstance(node, exp.Where):
                where_conditions.append(str(node.this)[:120])
            elif isinstance(node, exp.Order):
                for col in node.expressions:
                    order_by_columns.append(str(col)[:60])
            elif isinstance(node, (exp.Avg, exp.Sum, exp.Count, exp.Max, exp.Min)):
                has_aggregation = True
            elif isinstance(node, exp.Subquery):
                has_subquery = True
            elif isinstance(node, exp.CTE):
                has_cte = True
        warnings: list[str] = []
        if not where_conditions and tables:
            warnings.append("未检测到 WHERE 子句，可能存在全表扫描风险")
        plan_lines: list[str] = []
        if tables:
            plan_lines.append(f"扫描表: {', '.join(tables)}")
        if joins:
            for j in joins:
                plan_lines.append(f"  {j['kind']} {j['table']}")
        if where_conditions:
            plan_lines.append(f"过滤条件: {'; '.join(where_conditions[:3])}")
        if has_aggregation:
            plan_lines.append("聚合操作: 是")
        return {
            "status": "analyzed",
            "dialect": body.dialect or "ansi",
            "tables": tables,
            "join_count": len(joins),
            "joins": joins,
            "where_conditions": where_conditions,
            "order_by": order_by_columns,
            "has_aggregation": has_aggregation,
            "has_subquery": has_subquery,
            "has_cte": has_cte,
            "statement_count": len(parsed),
            "warnings": warnings,
            "plan_text": "\n".join(plan_lines) if plan_lines else "简单查询",
        }
    except ImportError:
        return {"status": "unavailable", "message": "sqlglot 未安装"}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"SQL 分析失败: {e}") from e


@router.post("/sql/translate")
async def translate_sql(body: SQLRequest) -> dict:
    """Translate SQL from one dialect to another using sqlglot."""
    if not body.target_dialect:
        raise HTTPException(status_code=400, detail="target_dialect is required")
    try:
        import sqlglot

        read_dialect = _normalize_dialect(body.dialect) if body.dialect else None
        write_dialect = _normalize_dialect(body.target_dialect)
        translated = sqlglot.transpile(body.sql, read=read_dialect, write=write_dialect)
        if not translated:
            raise HTTPException(status_code=400, detail="Translation produced no output")
        return {
            "status": "translated",
            "source_dialect": body.dialect or "ansi",
            "target_dialect": body.target_dialect,
            "source_sql": body.sql,
            "translated_sql": translated[0],
        }
    except ImportError:
        return {"status": "unavailable", "message": "sqlglot 未安装"}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"SQL 翻译失败: {e}") from e


@router.post("/sql/format")
async def format_sql(body: SQLRequest) -> dict:
    """Format SQL with proper indentation and keyword casing."""
    try:
        import sqlparse

        formatted = sqlparse.format(
            body.sql, reindent=True, keyword_case="upper", indent_width=2
        )
        return {
            "status": "formatted",
            "dialect": body.dialect or "ansi",
            "source_sql": body.sql,
            "formatted_sql": formatted,
        }
    except ImportError:
        keywords = [
            "SELECT", "FROM", "WHERE", "AND", "OR", "ORDER BY", "GROUP BY",
            "HAVING", "LIMIT", "JOIN", "LEFT JOIN", "RIGHT JOIN",
            "INNER JOIN", "ON", "AS", "INSERT", "UPDATE", "DELETE",
            "CREATE", "ALTER", "DROP", "SET", "INTO", "VALUES",
            "UNION", "ALL", "DISTINCT", "CASE", "WHEN", "THEN", "ELSE",
            "END", "COUNT", "SUM", "AVG", "MAX", "MIN", "COALESCE",
            "NULL", "NOT", "IN", "LIKE", "BETWEEN", "IS", "WITH",
            "RECURSIVE", "OVER", "PARTITION BY", "ROW_NUMBER", "RANK",
            "DENSE_RANK", "LAG", "LEAD",
        ]
        formatted = body.sql
        for kw in sorted(keywords, key=len, reverse=True):
            pattern = re.compile(r"\b" + re.escape(kw) + r"\b", re.IGNORECASE)
            formatted = pattern.sub(
                lambda m: "\n" + m.group(0).upper(), formatted
            )
        formatted = re.sub(r"\n\s*\n", "\n", formatted).strip()
        return {
            "status": "formatted",
            "dialect": body.dialect or "ansi",
            "source_sql": body.sql,
            "formatted_sql": formatted,
            "note": "sqlparse 未安装，使用基础格式化",
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"SQL 格式化失败: {e}") from e


@router.post("/sql/validate")
async def validate_sql(body: SQLRequest) -> dict:
    """Quick syntax validation and basic lint checks for a SQL statement."""
    errors: list[str] = []
    warnings: list[str] = []
    try:
        import sqlglot

        dialect = _normalize_dialect(body.dialect) if body.dialect else None
        parsed = sqlglot.parse(body.sql, dialect=dialect)
        if not parsed:
            errors.append("无法解析 SQL 语句")
        else:
            sql_upper = body.sql.upper()
            if "SELECT" in sql_upper and "FROM" not in sql_upper:
                warnings.append("SELECT 语句缺少 FROM 子句")
            if sql_upper.count("(") != sql_upper.count(")"):
                warnings.append("括号不匹配")
    except ImportError:
        warnings.append("sqlglot 未安装，跳过语法验证")
    except Exception as e:
        errors.append(f"语法解析错误: {e}")
    return {
        "status": "valid" if not errors else "invalid",
        "syntax_ok": len(errors) == 0,
        "errors": errors,
        "warnings": warnings,
        "dialect": body.dialect or "ansi",
    }
