"""Tests for POST /api/v1/query — the full NL→SQL pipeline endpoint."""

import pytest
from fastapi.testclient import TestClient

from app.api import create_app


@pytest.fixture
def client():
    app = create_app()
    return TestClient(app)


# ═══════════════════════════════════════════════════════════════════════════
# Basic endpoint tests
# ═══════════════════════════════════════════════════════════════════════════


class TestQueryEndpoint:
    def test_basic_query_returns_200(self, client):
        resp = client.post("/api/v1/query", json={"nl_text": "查询所有订单"})
        assert resp.status_code == 200
        data = resp.json()
        assert "query_id" in data
        assert data["nl_text"] == "查询所有订单"

    def test_english_query(self, client):
        resp = client.post("/api/v1/query", json={"nl_text": "find all orders"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["language"] == "en"

    def test_chinese_query(self, client):
        resp = client.post("/api/v1/query", json={"nl_text": "统计上个月的销售额"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["language"] == "zh"

    def test_empty_nl_text_rejected(self, client):
        resp = client.post("/api/v1/query", json={"nl_text": ""})
        assert resp.status_code == 422  # validation error


# ═══════════════════════════════════════════════════════════════════════════
# Response structure
# ═══════════════════════════════════════════════════════════════════════════


class TestResponseStructure:
    def test_has_candidates(self, client):
        resp = client.post("/api/v1/query", json={"nl_text": "查询所有订单"})
        data = resp.json()
        assert "candidates" in data
        assert len(data["candidates"]) >= 1

    def test_candidate_structure(self, client):
        resp = client.post("/api/v1/query", json={"nl_text": "查询所有订单"})
        data = resp.json()
        cand = data["candidates"][0]
        assert "id" in cand
        assert "sql_text" in cand
        assert "confidence" in cand
        assert "generation_mode" in cand

    def test_has_primary_sql(self, client):
        resp = client.post("/api/v1/query", json={"nl_text": "查询所有订单"})
        data = resp.json()
        assert "primary_sql" in data
        assert data["primary_sql"] is not None

    def test_has_validation(self, client):
        resp = client.post("/api/v1/query", json={"nl_text": "查询所有订单"})
        data = resp.json()
        assert "validation" in data
        if data["validation"] is not None:
            assert "passed" in data["validation"]
            assert "score" in data["validation"]

    def test_has_language_and_intent(self, client):
        resp = client.post("/api/v1/query", json={"nl_text": "统计上个月的销售额"})
        data = resp.json()
        assert data["language"] in ("zh", "en", "mixed")
        assert data["intent"] != "UNKNOWN"

    def test_aggregate_query_intent(self, client):
        resp = client.post("/api/v1/query", json={"nl_text": "统计各地区的销售额"})
        data = resp.json()
        assert data["intent"] == "AGGREGATE"


# ═══════════════════════════════════════════════════════════════════════════
# Config options
# ═══════════════════════════════════════════════════════════════════════════


class TestConfigOptions:
    def test_multiple_candidates(self, client):
        resp = client.post("/api/v1/query", json={
            "nl_text": "查询所有订单",
            "num_candidates": 3,
        })
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["candidates"]) == 3

    def test_skip_validation(self, client):
        resp = client.post("/api/v1/query", json={
            "nl_text": "查询所有订单",
            "validate": False,  # maps to run_validation via validation_alias
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["validation"] is None

    def test_skip_schema_linking(self, client):
        resp = client.post("/api/v1/query", json={
            "nl_text": "查询所有订单",
            "link_schema": False,
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["linked_tables"] == []

    def test_dialect_override(self, client):
        resp = client.post("/api/v1/query", json={
            "nl_text": "find orders",
            "dialect": "mysql",
        })
        assert resp.status_code == 200

    def test_execute_false_by_default(self, client):
        resp = client.post("/api/v1/query", json={"nl_text": "查询订单"})
        data = resp.json()
        assert data["execution"] is None


# ═══════════════════════════════════════════════════════════════════════════
# Generate-only endpoint
# ═══════════════════════════════════════════════════════════════════════════


class TestGenerateOnlyEndpoint:
    def test_generate_only_returns_200(self, client):
        resp = client.post("/api/v1/query/generate", json={"nl_text": "查询订单"})
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["candidates"]) >= 1

    def test_generate_only_no_schema(self, client):
        resp = client.post("/api/v1/query/generate", json={"nl_text": "find orders"})
        data = resp.json()
        assert data["linked_tables"] == []
        assert data["execution"] is None


# ═══════════════════════════════════════════════════════════════════════════
# Edge cases
# ═══════════════════════════════════════════════════════════════════════════


class TestEdgeCases:
    def test_very_short_query(self, client):
        resp = client.post("/api/v1/query", json={"nl_text": "hi"})
        assert resp.status_code == 200
        data = resp.json()
        assert "candidates" in data

    def test_long_chinese_query(self, client):
        resp = client.post("/api/v1/query", json={
            "nl_text": "统计上个月各地区的销售额前10名按金额降序"
        })
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["candidates"]) >= 1

    def test_query_with_entities(self, client):
        resp = client.post("/api/v1/query", json={
            "nl_text": '查找产品"iPhone 15"的销量'
        })
        assert resp.status_code == 200


# ═══════════════════════════════════════════════════════════════════════════
# OpenAPI docs
# ═══════════════════════════════════════════════════════════════════════════


class TestOpenAPI:
    def test_docs_available(self, client):
        resp = client.get("/docs")
        assert resp.status_code == 200

    def test_openapi_schema(self, client):
        resp = client.get("/openapi.json")
        assert resp.status_code == 200
        schema_data = resp.json()
        paths = schema_data.get("paths", {})
        assert "/api/v1/query" in paths
        assert "/api/v1/query/generate" in paths
        assert "/api/v1/query/stream" in paths


# ═══════════════════════════════════════════════════════════════════════════
# Streaming endpoint (SSE)
# ═══════════════════════════════════════════════════════════════════════════


class TestStreamingEndpoint:
    def test_stream_returns_200(self, client):
        resp = client.post("/api/v1/query/stream", json={"nl_text": "查询订单"})
        assert resp.status_code == 200
        assert "text/event-stream" in resp.headers.get("content-type", "")

    def test_stream_has_parse_event(self, client):
        resp = client.post("/api/v1/query/stream", json={"nl_text": "find all orders"})
        body = resp.text
        assert "event: parse" in body

    def test_stream_has_token_events(self, client):
        resp = client.post("/api/v1/query/stream", json={"nl_text": "查询所有订单"})
        body = resp.text
        assert "event: token" in body

    def test_stream_has_done_event(self, client):
        resp = client.post("/api/v1/query/stream", json={"nl_text": "查询订单"})
        body = resp.text
        assert "event: done" in body

    def test_stream_done_has_sql(self, client):
        resp = client.post("/api/v1/query/stream", json={"nl_text": "find orders"})
        body = resp.text
        assert "primary_sql" in body

    def test_stream_empty_input(self, client):
        resp = client.post("/api/v1/query/stream", json={"nl_text": ""})
        assert resp.status_code == 422

    def test_stream_with_validation(self, client):
        resp = client.post("/api/v1/query/stream", json={
            "nl_text": "find all orders",
            "validate": True,
        })
        body = resp.text
        assert "event: validation" in body

    def test_stream_skip_validation(self, client):
        resp = client.post("/api/v1/query/stream", json={
            "nl_text": "find orders",
            "validate": False,
        })
        body = resp.text
        assert "event: validation" not in body

    def test_stream_has_cache_headers(self, client):
        resp = client.post("/api/v1/query/stream", json={"nl_text": "test"})
        assert resp.headers.get("cache-control") == "no-cache"
        assert resp.headers.get("connection") == "keep-alive"

    def test_stream_event_format(self, client):
        """SSE format: 'event: <name>\\ndata: <json>\\n\\n'"""
        resp = client.post("/api/v1/query/stream", json={"nl_text": "find orders"})
        body = resp.text
        # Each event should be separated by double newline
        events = body.strip().split("\n\n")
        assert len(events) >= 2
        for event in events:
            if event.startswith("event: done"):
                assert "data:" in event
                # data should be valid JSON
                data_start = event.index("data:") + 5
                data_str = event[data_start:].strip()
                import json
                parsed = json.loads(data_str)
                assert "query_id" in parsed
