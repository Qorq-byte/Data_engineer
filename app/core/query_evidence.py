"""Reproducible SQL checks and source disclosures, without probability scores."""

from typing import Any

import sqlglot
from sqlglot import exp
from sqlglot.optimizer.scope import traverse_scope

from app.core.sql_validator import _read_dialect
from app.models.query import SQLCandidate
from app.models.schema import ColumnSchema, ForeignKey, SchemaSnapshot, TableSchema


def supplied_schema(ddl: str, dialect: str) -> SchemaSnapshot:
    """Parse declarations only. User DDL is never executed."""
    snapshot = SchemaSnapshot(database_type=dialect, database_name="user_supplied")
    statements = sqlglot.parse(ddl, read=_read_dialect(dialect))
    for statement in statements:
        if not isinstance(statement, exp.Create) or not isinstance(statement.this, exp.Schema):
            raise ValueError("请提供完整的 CREATE TABLE 声明")
        declaration = statement.this
        if not isinstance(declaration.this, exp.Table):
            raise ValueError("仅支持表结构声明")
        table = TableSchema(
            name=declaration.this.name, schema_name=declaration.this.db or None,
            catalog_name=declaration.this.catalog or None,
        )
        for column in declaration.find_all(exp.ColumnDef):
            table.columns.append(ColumnSchema(
                name=column.name,
                type=column.args["kind"].sql(dialect=_read_dialect(dialect)),
            ))
        for fk in declaration.find_all(exp.ForeignKey):
            reference = fk.find(exp.Reference)
            if reference is not None and isinstance(reference.this, exp.Schema):
                target = reference.this
                for local, remote in zip(fk.expressions, target.expressions, strict=True):
                    table.foreign_keys.append(ForeignKey(
                        name="", column=local.name, ref_table=target.this.name,
                        ref_column=remote.name,
                    ))
        for column in declaration.find_all(exp.ColumnDef):
            for reference in column.find_all(exp.Reference):
                target = reference.this
                if isinstance(target, exp.Schema) and len(target.expressions) == 1:
                    table.foreign_keys.append(ForeignKey(
                        name="", column=column.name, ref_table=target.this.name,
                        ref_column=target.expressions[0].name,
                    ))
        if not table.columns or table.name in snapshot.tables:
            raise ValueError("表必须具有字段且表名不可重复")
        for declared_key in table.foreign_keys:
            for schema_column in table.columns:
                if schema_column.name == declared_key.column:
                    schema_column.is_foreign_key = True
                    schema_column.references = (declared_key.ref_table, declared_key.ref_column)
        snapshot.tables[table.name] = table
    if not snapshot.tables:
        raise ValueError("未找到 CREATE TABLE 声明")
    return snapshot


def candidate_evidence(
    candidate: SQLCandidate, schema: SchemaSnapshot | None, source: str,
    rules: list[Any], *, notes: str = "", ambiguities: list[str] | None = None,
    execution_error: str = "",
) -> dict[str, Any]:
    """Describe only performed checks. FK coverage is conservative for complex joins."""
    report = candidate.validation or {}
    checks: list[dict[str, str]] = []

    def check(name: str, status: str, detail: str) -> None:
        checks.append({"name": name, "status": status, "detail": detail})

    def result(key: str, performed: bool) -> str:
        if not performed:
            return "unverified"
        return "passed" if report.get(key) else "failed"

    check("方言与只读语法", result("syntax_ok", bool(report)),
          candidate.dialect + " · " + "; ".join(report.get("syntax_errors", [])))
    check("表与字段", result("schema_valid", schema is not None and bool(report)),
          source + " · " + "; ".join(report.get("schema_errors", [])))
    check("字段类型启发式检查", result("type_valid", schema is not None and bool(report)),
          "静态兼容性检查，未覆盖全部表达式类型 · " + "; ".join(report.get("type_errors", [])))
    supported_directives = {
        "require", "forbid", "require_where", "require_limit", "require_time_range", "precision",
    }
    rules_covered = bool(rules) and all(
        (rule.pattern or rule.enforce) and all(
            directive.partition("=")[0].strip().lower() in supported_directives
            for directive in rule.enforce
        ) for rule in rules
    )
    check("业务规则",
          "failed" if report and not report.get("business_valid", True) else
          result("business_valid", rules_covered and bool(report)),
          f"配置规则 {len(rules)} 项；静态规则检查 · "
          + "; ".join(report.get("business_warnings", []))
          + ("；含未覆盖规则，仍需业务确认" if rules and not rules_covered else ""))
    execution = candidate.execution
    execution_status = "unverified"
    if execution_error:
        execution_status = "failed"
    elif execution:
        execution_status = "failed" if execution.get("error") else "passed"
    check("数据库实际执行", execution_status,
          execution_error or ("实际驱动执行结果" if execution else "未获得数据库执行结果"))
    assumptions: list[str] = []
    clarifications = list(ambiguities or [])
    for line in candidate.sql_text.splitlines():
        if line.strip().startswith("-- ASSUMPTION:"):
            assumptions.append(line.split(":", 1)[1].strip())
        if line.strip().startswith("-- CLARIFICATION:"):
            clarifications.append(line.split(":", 1)[1].strip())
    relations: list[str] = []
    verified = True
    try:
        tree = sqlglot.parse_one(
            candidate.sql_text, read=_read_dialect(candidate.dialect),
        )
        for scope in traverse_scope(tree):
            physical = {alias: table for alias, table in scope.sources.items()
                        if isinstance(table, exp.Table)}
            if schema is None:
                assumptions.extend("假设表 " + table.sql() for table in physical.values())
                assumptions.extend(
                    "假设字段 " + column.sql() for column in scope.columns
                    if column.table in physical or (not column.table and physical)
                )
            for join in scope.expression.args.get("joins") or []:
                on = join.args.get("on")
                relation = join.sql()
                supported = False
                # Only a direct column equality can prove a declared FK relationship.
                if isinstance(on, exp.EQ) and isinstance(on.left, exp.Column) and isinstance(
                    on.right, exp.Column
                ) and schema is not None:
                    for left, right in ((on.left, on.right), (on.right, on.left)):
                        left_table = physical.get(left.table)
                        right_table = physical.get(right.table)
                        table = schema.tables.get(left_table.name) if left_table else None
                        if table and right_table:
                            supported = any(
                                fk.column == left.name and fk.ref_table == right_table.name
                                and fk.ref_column == right.name for fk in table.foreign_keys
                            )
                        if supported:
                            break
                verified = verified and supported
                relations.append(("外键支持 · " if supported else "待确认关联 · ") + relation)
                if not supported:
                    assumptions.append("关联依据待确认 · " + relation)
    except (sqlglot.errors.SqlglotError, ValueError):
        verified = False
    check("关联关系", "passed" if relations and verified else "unverified",
          "\n".join(relations) or "无关联关系需要验证")
    check("业务语义与关键歧义", "unverified",
          "；".join(clarifications) or "尚未以人工答案验证业务口径")
    if schema is None and notes:
        assumptions.insert(0, "依据用户文字说明生成；表字段尚未与结构化 Schema 比对")
    return {
        "source": source, "schema_time": schema.created_at.isoformat() if schema else None,
        "dialect": candidate.dialect, "checks": checks,
        "assumptions": list(dict.fromkeys(assumptions)),
        "clarifications": list(dict.fromkeys(clarifications)),
        "score_policy": "逐项证据展示，不提供 SQL 正确概率",
    }
