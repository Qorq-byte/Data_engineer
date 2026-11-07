"""Exercise both transports, source provenance and actual SQLite results."""

import json
from dataclasses import asdict

import pytest

from app.api import create_app, query
from app.core.query_evidence import candidate_evidence, supplied_schema
from app.core.sql_validator import SQLValidator
from app.db.connections import ConnectionFactory, DatabaseType
from app.db.schema_extractor import SchemaExtractor
from app.llm.router import LiteLLMRouter, RouterConfig
from app.models.query import SQLCandidate
from tests.api_client import authenticated_client

COMPLEX = """
WITH monthly AS (
  SELECT u.id, SUM(CASE WHEN o.status = 'completed' THEN o.amount ELSE 0 END) AS total,
         strftime('%Y-%m', o.created_at) AS month
  FROM users u JOIN orders o ON o.user_id = u.id
  WHERE o.amount > (SELECT AVG(amount) FROM orders)
  GROUP BY u.id, strftime('%Y-%m', o.created_at)
)
SELECT id, month, total, RANK() OVER (PARTITION BY month ORDER BY total DESC) AS position,
       LAG(total) OVER (PARTITION BY id ORDER BY month) AS previous
FROM monthly ORDER BY id
"""
DDL = """
CREATE TABLE users (id INTEGER PRIMARY KEY);
CREATE TABLE orders (id INTEGER, user_id INTEGER REFERENCES users(id),
  amount REAL, status TEXT, created_at TEXT);
"""


@pytest.mark.parametrize("protocol", ["json", "sse"])
@pytest.mark.parametrize("case", [
    "inferred", "supplied", "notes", "connected", "missing_column", "unavailable", "clarify",
    "execution_failure",
])
async def test_generation_evidence(test_sqlite_db, monkeypatch, protocol, case):
    monkeypatch.setattr(ConnectionFactory, "_connections", {})
    monkeypatch.setattr(ConnectionFactory, "_infos", {})
    sql = COMPLEX
    if case == "missing_column":
        sql = "SELECT not_a_real_column FROM orders"
    if case == "execution_failure":
        sql = "SELECT ABS(-9223372036854775808) FROM orders"
    if case == "clarify":
        sql = "-- CLARIFICATION: 请指定净收入是否扣除退款\nSELECT NULL WHERE 1 = 0"

    class ControlledRouter(LiteLLMRouter):
        async def complete(self, messages, **kwargs):
            prompt = messages[-1]["content"]
            assert "CLARIFICATION:" in prompt
            if case == "notes":
                assert "amount is revenue" in prompt
            return {"choices": [{"message": {"content": sql}}]}

    monkeypatch.setattr(
        query, "get_default_router", lambda: ControlledRouter(RouterConfig(), mock_mode=True),
    )
    connected = case in {
        "connected", "missing_column", "unavailable", "clarify", "execution_failure",
    }
    body = {
        "nl_text": "按用户统计每月已完成订单收入、排名及上一期收入",
        "dialect": "sqlite", "num_candidates": 1, "execute": True,
    }
    if connected:
        await ConnectionFactory.create(
            DatabaseType.SQLITE, {"path": test_sqlite_db}, db_id="evidence",
        )
        body["database"] = "evidence"
    if case == "supplied":
        body["schema_ddl"] = DDL
    if case == "notes":
        body["schema_notes"] = "orders.amount is revenue; users.id joins orders.user_id"
    if case == "unavailable":
        async def failed_extract(*args, **kwargs):
            raise ConnectionError("synthetic disconnected server")
        monkeypatch.setattr(SchemaExtractor, "cached_extract", failed_extract)
    client = authenticated_client(create_app())
    try:
        response = client.post(
            "/api/v1/query" + ("/stream" if protocol == "sse" else ""), json=body,
        )
        assert response.status_code == 200
        if protocol == "sse":
            payloads = [
                json.loads(block.split("data: ", 1)[1])
                for block in response.text.split("\n\n") if block.startswith("event: done\n")
            ]
            assert len(payloads) == 1, response.text
            result = payloads[0]
        else:
            result = response.json()
        assert result["primary_sql"]
        candidate = result["candidates"][0]
        assert candidate["confidence"] == 0
        evidence = candidate["evidence"]
        checks = {item["name"]: item["status"] for item in evidence["checks"]}
        assert evidence["dialect"] == "sqlite"
        if case == "connected":
            assert result["status"] == "completed", result
            assert result["execution"]["rows"] == [
                [1, "2026-07", 5999.0, 1, None],
                [2, "2026-07", 5999.0, 1, None],
            ]
            assert checks["表与字段"] == "passed"
            assert checks["数据库实际执行"] == "passed"
            assert checks["关联关系"] == "passed"
            second = client.post("/api/v1/query", json={**body, "execute": False}).json()
            assert second["candidates"][0]["evidence"]["source"] == "cached_schema"
        elif case == "supplied":
            assert evidence["source"] == "user_schema"
            assert checks["表与字段"] == "passed", result
            assert checks["关联关系"] == "passed", result
        elif case == "missing_column":
            assert checks["表与字段"] == "failed"
            assert result["execution"] is None
        elif case == "clarify":
            assert evidence["clarifications"]
            assert result["execution"] is None
        elif case == "execution_failure":
            assert result["status"] == "failed"
            assert checks["表与字段"] == "passed"
            assert checks["数据库实际执行"] == "failed", result["errors"]
        else:
            assert checks["表与字段"] == "unverified"
            assert evidence["assumptions"]
            assert "未经真实数据库验证" in result["source_note"]
        if case not in {"connected", "execution_failure"}:
            assert checks["数据库实际执行"] == "unverified"
            assert result["execution"] is None
        if case == "unavailable":
            assert any("extraction failed" in warning for warning in result["warnings"])
    finally:
        if connected:
            ConnectionFactory.close("evidence")


@pytest.mark.parametrize("body", [
    {"schema_ddl": "DROP TABLE orders"},
    {"schema_ddl": "CREATE TABLE orders"},
    {"dialect": "fictional"},
])
def test_invalid_schema_and_dialect_are_explicit(body):
    response = authenticated_client(create_app()).post(
        "/api/v1/query", json={"nl_text": "统计订单", **body},
    )
    assert response.status_code == 422


@pytest.mark.parametrize("sql, passed", [
    ("SELECT 1;\n-- ASSUMPTION: example input", True),
    ("SELECT 1; SELECT 2;\n-- ASSUMPTION: example input", False),
])
async def test_generated_comments_preserve_single_statement_policy(sql, passed):
    report = await SQLValidator("sqlite").validate(sql)
    assert report.passed is passed


def test_driver_dialect_alias_is_normalized_for_schema_and_validation():
    body = query.QueryRequest(
        nl_text="列出事件", dialect="PostgreSQL",
        schema_ddl="CREATE TABLE events (id BIGINT, payload JSONB)",
    )
    assert body.dialect == "postgres"
    assert SQLValidator("postgresql").dialect == "postgres"


@pytest.mark.parametrize("condition", [
    "o.parent_id = p.id",
    "o.parent_id = p.id AND o.tenant_id = p.tenant_id",
])
def test_composite_foreign_key_requires_complete_relation_verification(condition):
    schema = supplied_schema(
        "CREATE TABLE parents (id INT, tenant_id INT); "
        "CREATE TABLE orders (parent_id INT, tenant_id INT, "
        "FOREIGN KEY (parent_id, tenant_id) REFERENCES parents (id, tenant_id));",
        "sqlite",
    )
    candidate = SQLCandidate(
        id="composite", sql_text=f"SELECT o.parent_id FROM orders o JOIN parents p ON {condition}",
        dialect="sqlite",
    )
    evidence = candidate_evidence(candidate, schema, "user_schema", [])
    relation = next(check for check in evidence["checks"] if check["name"] == "关联关系")
    assert relation["status"] == "unverified"
    assert any("待确认" in assumption for assumption in evidence["assumptions"])


async def test_type_warnings_do_not_appear_as_passed_checks():
    schema = supplied_schema("CREATE TABLE t (id INT)", "sqlite")
    sql = "SELECT id FROM t WHERE id = 'abc'"
    report = await SQLValidator("sqlite").validate(sql, schema=schema)
    assert report.passed
    assert report.type_errors
    candidate = SQLCandidate(
        id="type-warning", sql_text=sql, dialect="sqlite", validation=asdict(report),
    )
    evidence = candidate_evidence(candidate, schema, "user_schema", [])
    check = next(item for item in evidence["checks"] if item["name"] == "字段类型启发式检查")
    assert check["status"] == "unverified"
    assert "TYPE_WARNING" in check["detail"]
