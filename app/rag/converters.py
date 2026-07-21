"""RAG data converters — SchemaSnapshot / GlossaryTerm / BusinessRule → RAG documents.

Pure data transformation functions with no I/O or network dependencies.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from app.models.domain import BusinessRule, GlossaryTerm
from app.models.rag_metric import MetricDocument
from app.models.rag_schema import SchemaDocument
from app.models.schema import ColumnSchema, SchemaSnapshot, TableSchema


def snapshot_to_schema_docs(
    snapshot: SchemaSnapshot,
    db_id: str,
    schema_name: str = "public",
    language: str = "zh",
) -> list[SchemaDocument]:
    """Convert a SchemaSnapshot into a flat list of SchemaDocument for RAG indexing.

    Produces one table-level doc per table plus one column-level doc per column.
    The ``embedding_text`` field is a natural-language description that gets vectorized.

    Args:
        snapshot: Extracted schema snapshot from SchemaExtractor.
        db_id: Database identifier (e.g. ``"sqlite_test"``).
        schema_name: Schema/namespace name (default ``"public"``).
        language: Language hint for embedding text — ``"zh"`` or ``"en"``.

    Returns:
        Flat list of SchemaDocument, table-level docs first, then column-level docs.
    """
    docs: list[SchemaDocument] = []
    use_zh = language == "zh"

    for table_name, table in snapshot.tables.items():
        # ── Table-level document ──────────────────────────────────────
        table_doc = _build_table_doc(table, db_id, schema_name, table_name, use_zh)
        docs.append(table_doc)

        # ── Column-level documents ────────────────────────────────────
        for col in table.columns:
            col_doc = _build_column_doc(
                col, table_name, db_id, schema_name, table.row_count_estimate, use_zh
            )
            docs.append(col_doc)

    return docs


def _build_table_doc(
    table: TableSchema,
    db_id: str,
    schema_name: str,
    table_name: str,
    use_zh: bool,
) -> SchemaDocument:
    """Build a table-level SchemaDocument."""
    doc_id = f"{db_id}.{schema_name}.{table_name}"

    col_list = ", ".join(
        f"{c.name}({c.type})" for c in table.columns[:20]
    )
    if len(table.columns) > 20:
        col_list += f" …(+{len(table.columns) - 20})"

    pk_cols = [c.name for c in table.columns if c.is_primary_key]
    fk_cols = [
        f"{c.name}→{c.references[0]}.{c.references[1]}"
        for c in table.columns
        if c.is_foreign_key and c.references
    ]

    if use_zh:
        parts = [f"表 {table_name}"]
        if table.comment:
            parts.append(f"说明: {table.comment}")
        parts.append(f"包含 {len(table.columns)} 个列: {col_list}")
        if pk_cols:
            parts.append(f"主键: {', '.join(pk_cols)}")
        if fk_cols:
            parts.append(f"外键: {'; '.join(fk_cols)}")
        if table.row_count_estimate:
            parts.append(f"约 {table.row_count_estimate} 行")
    else:
        parts = [f"Table {table_name}"]
        if table.comment:
            parts.append(f"Comment: {table.comment}")
        parts.append(f"Contains {len(table.columns)} columns: {col_list}")
        if pk_cols:
            parts.append(f"Primary keys: {', '.join(pk_cols)}")
        if fk_cols:
            parts.append(f"Foreign keys: {'; '.join(fk_cols)}")
        if table.row_count_estimate:
            parts.append(f"~{table.row_count_estimate} rows")

    embedding_text = "。".join(parts) + "。" if use_zh else ". ".join(parts) + "."

    tags: list[str] = [table_name]
    if pk_cols:
        tags.append("primary_key")
    if fk_cols:
        tags.append("foreign_key")

    return SchemaDocument(
        doc_id=doc_id,
        db_id=db_id,
        schema_name=schema_name,
        table_name=table_name,
        column_name=None,
        data_type=None,
        comment=table.comment,
        table_row_count=table.row_count_estimate,
        embedding_text=embedding_text,
        tags=tags,
    )


def _build_column_doc(
    col: ColumnSchema,
    table_name: str,
    db_id: str,
    schema_name: str,
    table_row_count: int,
    use_zh: bool,
) -> SchemaDocument:
    """Build a column-level SchemaDocument."""
    doc_id = f"{db_id}.{schema_name}.{table_name}.{col.name}"

    # Build PK/FK badge
    badges: list[str] = []
    if col.is_primary_key:
        badges.append("主键" if use_zh else "primary key")
    if col.is_foreign_key and col.references:
        ref_str = f"{col.references[0]}.{col.references[1]}"
        badges.append(
            f"外键→{ref_str}" if use_zh else f"foreign key→{ref_str}"
        )

    nullable_str = "可空" if use_zh else "nullable"
    if not col.nullable:
        nullable_str = "非空" if use_zh else "NOT NULL"

    if use_zh:
        parts = [f"表 {table_name} 的列 {col.name} (类型: {col.type}, {nullable_str})"]
        if badges:
            parts.append(f"约束: {', '.join(badges)}")
        if col.comment:
            parts.append(f"注释: {col.comment}")
        if col.enum_values:
            vals = ", ".join(str(v) for v in col.enum_values[:8])
            parts.append(f"枚举值: {vals}")
    else:
        parts = [
            f"Column {col.name} (type: {col.type}, {nullable_str}) in table {table_name}"
        ]
        if badges:
            parts.append(f"Constraints: {', '.join(badges)}")
        if col.comment:
            parts.append(f"Comment: {col.comment}")
        if col.enum_values:
            vals = ", ".join(str(v) for v in col.enum_values[:8])
            parts.append(f"Enum values: {vals}")

    embedding_text = "。".join(parts) + "。" if use_zh else ". ".join(parts) + "."

    tags: list[str] = [table_name, col.name]
    if col.is_primary_key:
        tags.append("pk")
    if col.is_foreign_key:
        tags.append("fk")

    return SchemaDocument(
        doc_id=doc_id,
        db_id=db_id,
        schema_name=schema_name,
        table_name=table_name,
        column_name=col.name,
        data_type=col.type,
        comment=col.comment,
        is_primary_key=col.is_primary_key,
        is_foreign_key=col.is_foreign_key,
        fk_references=col.references,
        enum_values=col.enum_values,
        sample_values=list(col.sample_values) if col.sample_values else [],
        table_row_count=table_row_count,
        embedding_text=embedding_text,
        tags=tags,
    )


# ── Glossary → MetricDocument converters ──────────────────────────────────


def glossary_term_to_metric_doc(
    term: GlossaryTerm,
    domain: str,
    doc_id_prefix: str = "glossary",
) -> MetricDocument:
    """Convert a GlossaryTerm to a MetricDocument for MetricRAG indexing.

    Builds a rich ``embedding_text`` from term name, description, formula,
    and mapping information in natural language.

    Args:
        term: A glossary term from a domain config.
        domain: Domain identifier (e.g. ``"ecommerce"``).
        doc_id_prefix: Prefix for the ``doc_id`` (default ``"glossary"``).

    Returns:
        MetricDocument ready for ``MetricRAG.index_metric()``.
    """
    doc_id = f"{doc_id_prefix}.{domain}.{term.term}"

    parts: list[str] = [f"业务术语: {term.term}"]
    if term.term_en:
        parts.append(f"({term.term_en})")
    if term.description:
        parts.append(f"描述: {term.description}")

    formula = ""
    sql_template = ""
    if term.mapping:
        parts.append(f"SQL表达式: {term.mapping.expression}")
        formula = term.mapping.expression
        if term.mapping.table:
            parts.append(f"关联表: {term.mapping.table}")
        if term.mapping.condition:
            parts.append(f"过滤条件: {term.mapping.condition}")
        sql_template = term.mapping.expression

    aliases = [term.term]
    if term.term_en:
        aliases.append(term.term_en)
    if term.tags:
        aliases.extend(term.tags)

    embedding_text = "。".join(parts) + "。"

    return MetricDocument(
        doc_id=doc_id,
        domain=domain,
        name=term.term,
        name_zh=term.term,
        aliases=aliases,
        description=term.description,
        formula=formula,
        metric_type="derived",
        aggregation="sum",
        dimensions=[],
        time_grain="day",
        precision=term.mapping.precision if term.mapping and term.mapping.precision else 2,
        sql_template=sql_template,
        embedding_text=embedding_text,
    )


def business_rule_to_metric_doc(
    rule: BusinessRule,
    domain: str,
    doc_id_prefix: str = "rule",
) -> MetricDocument:
    """Convert a BusinessRule to a MetricDocument for MetricRAG indexing.

    Business rules with ``sql_template`` are indexed so the RAG system can
    retrieve enforcement directives when processing related NL queries.

    Args:
        rule: A business rule from a domain config.
        domain: Domain identifier.
        doc_id_prefix: Prefix for the ``doc_id`` (default ``"rule"``).

    Returns:
        MetricDocument ready for ``MetricRAG.index_metric()``.
    """
    doc_id = f"{doc_id_prefix}.{domain}.{rule.id}"

    parts: list[str] = [f"业务规则: {rule.description}"]
    if rule.pattern:
        parts.append(f"匹配模式: {rule.pattern}")
    if rule.sql_template:
        parts.append(f"SQL模板: {rule.sql_template}")
    if rule.enforce:
        parts.append(f"强制执行: {', '.join(rule.enforce)}")

    embedding_text = "。".join(parts) + "。"

    return MetricDocument(
        doc_id=doc_id,
        domain=domain,
        name=rule.id,
        name_zh=rule.description[:50] if rule.description else rule.id,
        aliases=[rule.id],
        description=rule.description,
        formula=rule.sql_template or "",
        metric_type="derived",
        aggregation="",
        dimensions=[],
        time_grain="day",
        precision=2,
        sql_template=rule.sql_template,
        embedding_text=embedding_text,
    )
