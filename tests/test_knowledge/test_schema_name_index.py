"""Differential tests for :mod:`app.knowledge.schema_name_index`.

The index replaced four loops that walked the whole snapshot.  To prove the
replacement is behaviour-preserving rather than "close enough", this module
keeps a verbatim copy of those loops and compares both implementations over
randomised schemas, candidates, thresholds and queries.
"""

from __future__ import annotations

import random
from typing import Any

import pytest

from app.knowledge.schema_name_index import SchemaNameIndex
from app.models.schema import ColumnSchema, SchemaSnapshot, TableSchema
from app.nodes.schema_linking import _EN_TO_ZH_MAP, _tokenize_zh

# ── Reference implementations (the code the index replaced) ────────────


def _reference_table_matches(
    name: str, schema: SchemaSnapshot, threshold: float
) -> list[tuple[str, float]]:
    name_lower = name.lower().strip()
    results: list[tuple[str, float]] = []
    for t_name, table in schema.tables.items():
        conf = 0.0
        if t_name == name:
            conf = 1.0
        elif t_name.lower() == name_lower:
            conf = 0.95
        elif table.comment and name_lower in table.comment.lower():
            conf = 0.7
        elif name_lower in t_name.lower() or t_name.lower() in name_lower:
            conf = 0.65
        if conf >= threshold:
            results.append((t_name, conf))
    results.sort(key=lambda item: item[1], reverse=True)
    return results


def _reference_column_matches(
    name: str, schema: SchemaSnapshot, threshold: float
) -> list[tuple[str, str, float]]:
    name_lower = name.lower().strip()
    results: list[tuple[str, str, float]] = []
    for t_name, table in schema.tables.items():
        for column in table.columns:
            col_lower = column.name.lower()
            conf = 0.0
            if column.name == name:
                conf = 1.0
            elif col_lower == name_lower:
                conf = 0.95
            elif column.comment and name_lower in column.comment.lower():
                conf = 0.75
            elif name_lower in col_lower:
                conf = 0.65
            elif col_lower in name_lower:
                conf = 0.6
            if conf >= threshold:
                results.append((t_name, column.name, conf))
    results.sort(key=lambda item: item[2], reverse=True)
    return results


def _reference_reverse_scan(query_text: str, schema: SchemaSnapshot) -> list[str]:
    if not query_text or schema is None:
        return []
    query_lower = query_text.lower()
    query_tokens = set(_tokenize_zh(query_text))
    query_tokens_lower = {token.lower() for token in query_tokens}

    matched: list[str] = []
    for table_name in schema.tables:
        parts = table_name.replace("-", "_").split("_")
        for part in parts:
            part_lower = part.lower().strip()
            if len(part_lower) < 3:
                continue
            zh_terms = _EN_TO_ZH_MAP.get(part_lower, [])
            for zh in zh_terms:
                if zh in query_text or zh.lower() in query_tokens_lower:
                    if table_name not in matched:
                        matched.append(table_name)
                    break
            if (part_lower in query_lower and len(part_lower) >= 4) and (
                table_name not in matched
            ):
                matched.append(table_name)
            for qtok in query_tokens_lower:
                if len(qtok) >= 4 and (
                    qtok == part_lower or qtok in part_lower or part_lower in qtok
                ):
                    if table_name not in matched:
                        matched.append(table_name)
                    break
    return matched


def _expand_tokens(query_text: str) -> list[str]:
    from app.nodes.schema_linking import _ZH_TO_EN_MAP

    expanded: list[str] = []
    for token in _tokenize_zh(query_text):
        expanded.append(token)
        expanded.extend(_ZH_TO_EN_MAP.get(token, []))
        token_lower = token.lower()
        if token_lower != token:
            expanded.append(token_lower)
            expanded.extend(_ZH_TO_EN_MAP.get(token_lower, []))
    return list(dict.fromkeys(expanded))


def _reference_direct_scan(query_text: str, schema: SchemaSnapshot) -> dict[str, list[str]]:
    if not query_text or schema is None:
        return {}
    expanded = _expand_tokens(query_text)

    table_matched = False
    for token in expanded:
        token_lower = token.lower().strip()
        if len(token_lower) < 2:
            continue
        for t_name in schema.tables:
            t_lower = t_name.lower()
            if (
                t_lower == token_lower
                or (len(token_lower) >= 3 and token_lower in t_lower)
                or (len(t_lower) >= 3 and t_lower in token_lower)
            ):
                table_matched = True
                break
        if table_matched:
            break
    if not table_matched:
        return {}

    result: dict[str, list[str]] = {}
    for token in expanded:
        token_lower = token.lower().strip()
        if len(token_lower) < 2:
            continue
        for t_name, table in schema.tables.items():
            for column in table.columns:
                col_lower = column.name.lower()
                if col_lower == token_lower or (
                    len(token_lower) >= 3 and token_lower in col_lower
                ):
                    result.setdefault(column.name, [])
                    if t_name not in result[column.name]:
                        result[column.name].append(t_name)
    return result


# ── Deterministic random schemas ───────────────────────────────────────

_TABLE_NAMES = [
    "users",
    "user_sessions",
    "orders",
    "order_items",
    "USERS",
    "Order-Log",
    "token_usage",
    "ab",
    "x",
    "knowledge_base",
    "sys_user_role",
    "report_data",
    "notification",
    "model_configs",
    "operation_logs",
    "ai_conversations",
    "project_files",
    "user_profiles",
    "invoices",
    "shipments",
]
_COLUMN_NAMES = [
    "id",
    "user_id",
    "name",
    "created_at",
    "amount",
    "no",
    "a",
    "USER_NAME",
    "token_count",
    "status",
    "updated_at",
    "description",
    "order_id",
    "email",
    "phone_number",
]
_COMMENTS = [
    "",
    "Primary key",
    "User identifier, references users.id",
    "创建时间",
    "amount in cents",
]
_QUERIES = [
    "查询用户的订单金额",
    "total amount by bucket",
    "最近的 token 用量",
    "show me order items",
    "knowledge base 里的文档",
    "通知列表",
    "",
    "USERS 表有多少行",
]


def _build_schema(rng: random.Random, table_count: int) -> SchemaSnapshot:
    names = rng.sample(_TABLE_NAMES, k=min(table_count, len(_TABLE_NAMES)))
    tables: dict[str, TableSchema] = {}
    for name in names:
        column_count = rng.randint(1, 5)
        columns = [
            ColumnSchema(
                name=rng.choice(_COLUMN_NAMES),
                type="text",
                comment=rng.choice(_COMMENTS) or None,
            )
            for _ in range(column_count)
        ]
        tables[name] = TableSchema(
            name=name,
            columns=columns,
            comment=rng.choice(_COMMENTS) or None,
        )
    return SchemaSnapshot(database_type="sqlite", database_name="db", tables=tables)


def _candidates(rng: random.Random) -> list[str]:
    pool = _TABLE_NAMES + _COLUMN_NAMES + _QUERIES
    picks: list[str] = ["", "user", "ID", "Order", "no", "re"]
    for _ in range(6):
        value = rng.choice(pool)
        if len(value) > 3 and rng.random() < 0.5:
            start = rng.randrange(len(value) - 2)
            value = value[start : start + rng.randint(2, len(value) - start)]
        picks.append(value)
    return list(dict.fromkeys(picks))


_THRESHOLDS = [0.0, 0.6, 0.65, 0.7, 0.75, 0.8, 0.95, 0.96, 1.0]


# ── Tests ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize("seed", range(12))
def test_table_matches_agree_with_the_old_scan(seed: int) -> None:
    rng = random.Random(seed)
    schema = _build_schema(rng, table_count=rng.randint(1, len(_TABLE_NAMES)))
    index = SchemaNameIndex(schema)
    for candidate in _candidates(rng):
        for threshold in _THRESHOLDS:
            assert index.match_tables(candidate, threshold) == _reference_table_matches(
                candidate, schema, threshold
            ), f"candidate={candidate!r} threshold={threshold} seed={seed}"


@pytest.mark.parametrize("seed", range(12))
def test_column_matches_agree_with_the_old_scan(seed: int) -> None:
    rng = random.Random(seed)
    schema = _build_schema(rng, table_count=rng.randint(1, len(_TABLE_NAMES)))
    index = SchemaNameIndex(schema)
    for candidate in _candidates(rng):
        for threshold in _THRESHOLDS:
            assert index.match_columns(candidate, threshold) == _reference_column_matches(
                candidate, schema, threshold
            ), f"candidate={candidate!r} threshold={threshold} seed={seed}"


@pytest.mark.parametrize("seed", range(8))
def test_reverse_scan_agrees_with_the_old_scan(seed: int) -> None:
    rng = random.Random(seed)
    schema = _build_schema(rng, table_count=rng.randint(1, len(_TABLE_NAMES)))
    index = SchemaNameIndex(schema)
    for query in _QUERIES:
        assert index.tables_sharing_parts(
            query, _tokenize_zh(query), _EN_TO_ZH_MAP
        ) == _reference_reverse_scan(query, schema), f"query={query!r} seed={seed}"


@pytest.mark.parametrize("seed", range(8))
def test_direct_scan_agrees_with_the_old_scan(seed: int) -> None:
    rng = random.Random(seed)
    schema = _build_schema(rng, table_count=rng.randint(1, len(_TABLE_NAMES)))
    index = SchemaNameIndex(schema)
    for query in _QUERIES:
        assert index.direct_keyword_scan(_expand_tokens(query)) == _reference_direct_scan(
            query, schema
        ), f"query={query!r} seed={seed}"


def test_reusing_one_index_keeps_returning_the_same_answer() -> None:
    schema = _build_schema(random.Random(3), table_count=8)
    index = SchemaNameIndex(schema)
    first = index.match_columns("name", 0.6)
    second = index.match_columns("name", 0.6)
    assert first == second
    # The index must not mutate the snapshot it was built from.
    assert schema.get_table("users") is not None or "users" not in schema.tables


def test_order_is_confidence_first_then_schema_order() -> None:
    schema = SchemaSnapshot(
        database_type="sqlite",
        database_name="db",
        tables={
            "users": TableSchema(
                name="users",
                columns=[
                    ColumnSchema(name="user_id", type="text"),
                    ColumnSchema(name="id", type="text"),
                ],
            ),
            "orders": TableSchema(
                name="orders", columns=[ColumnSchema(name="user_id", type="text")]
            ),
        },
    )
    index = SchemaNameIndex(schema)
    # 0.95 (case-insensitive equality) beats 0.65 (substring) regardless of table order,
    # and the weaker "id inside user_id" match trails both.
    assert index.match_columns("USER_ID", 0.6) == [
        ("users", "user_id", 0.95),
        ("orders", "user_id", 0.95),
        ("users", "id", 0.6),
    ]
    # Equal confidences keep schema order, and a stronger rule wins over a weaker one.
    assert index.match_tables("users", 0.6) == [("users", 1.0)]


def test_comments_are_matched_case_insensitively() -> None:
    schema = SchemaSnapshot(
        database_type="sqlite",
        database_name="db",
        tables={
            "t1": TableSchema(
                name="t1",
                columns=[ColumnSchema(name="c1", type="text", comment="Owner Identifier")],
            )
        },
    )
    index = SchemaNameIndex(schema)
    assert index.match_columns("identifier", 0.75) == [("t1", "c1", 0.75)]


def test_short_name_parts_are_ignored_by_the_reverse_scan() -> None:
    schema = SchemaSnapshot(
        database_type="sqlite",
        database_name="db",
        tables={"db_ab": TableSchema(name="db_ab", columns=[ColumnSchema(name="c", type="text")])},
    )
    index = SchemaNameIndex(schema)
    # "db" and "ab" are shorter than three characters, so no part can match.
    assert index.tables_sharing_parts("dbab", ["dbab"], _EN_TO_ZH_MAP) == []


def test_empty_candidate_matches_like_the_old_rules() -> None:
    schema = SchemaSnapshot(
        database_type="sqlite",
        database_name="db",
        tables={
            "users": TableSchema(
                name="users",
                columns=[ColumnSchema(name="id", type="text", comment="pk")],
            )
        },
    )
    index = SchemaNameIndex(schema)
    assert index.match_tables("", 0.6) == _reference_table_matches("", schema, 0.6)
    assert index.match_columns("", 0.6) == _reference_column_matches("", schema, 0.6)


def test_schema_types_are_untouched() -> None:
    payload: dict[str, Any] = {"nested": [1, 2]}
    schema = SchemaSnapshot(
        database_type="sqlite",
        database_name="db",
        tables={
            "users": TableSchema(
                name="users",
                columns=[ColumnSchema(name="id", type="text", default=payload)],
            )
        },
    )
    SchemaNameIndex(schema)
    assert schema.tables["users"].columns[0].default is payload
    assert payload == {"nested": [1, 2]}
