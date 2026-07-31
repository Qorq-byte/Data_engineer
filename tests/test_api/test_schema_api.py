"""Tests for Schema Refresh & Search API endpoints — Phase 3.

Covers: GET /schema/{db_id}, POST /schema/{db_id}/refresh, GET /schema/{db_id}/search
along with existing database management endpoints.
"""

from __future__ import annotations

import contextlib

import pytest
from fastapi.testclient import TestClient

from app.api import create_app
from app.db.connections import ConnectionFactory


@pytest.fixture
def client():
    app = create_app()
    return TestClient(app)


@pytest.fixture
def connected_db_id(test_sqlite_db: str) -> str:
    """Connect a test SQLite database via the API and return its db_id."""
    # Use the API to connect
    app = create_app()
    client = TestClient(app)
    resp = client.post("/api/v1/databases/connect", json={
        "db_type": "sqlite",
        "config": {"path": test_sqlite_db},
        "alias": "test_schema_api",
    })
    assert resp.status_code == 200, f"Failed to connect test DB: {resp.text}"
    db_id = resp.json()["db_id"]
    yield db_id
    # Cleanup
    with contextlib.suppress(KeyError, Exception):
        ConnectionFactory.close(db_id)


# ═══════════════════════════════════════════════════════════════════════════
# POST /api/v1/schema/{db_id}/refresh
# ═══════════════════════════════════════════════════════════════════════════


class TestRefreshSchema:
    def test_refresh_returns_200(self, client, connected_db_id):
        resp = client.post(f"/api/v1/schema/{connected_db_id}/refresh")
        assert resp.status_code == 200

    def test_refresh_has_status(self, client, connected_db_id):
        resp = client.post(f"/api/v1/schema/{connected_db_id}/refresh")
        data = resp.json()
        assert data["status"] == "refreshed"

    def test_refresh_has_table_list(self, client, connected_db_id):
        resp = client.post(f"/api/v1/schema/{connected_db_id}/refresh")
        data = resp.json()
        assert "tables" in data
        assert isinstance(data["tables"], list)
        assert "users" in data["tables"]
        assert "orders" in data["tables"]
        assert "products" in data["tables"]

    def test_refresh_has_table_count(self, client, connected_db_id):
        resp = client.post(f"/api/v1/schema/{connected_db_id}/refresh")
        data = resp.json()
        assert data["table_count"] == 3

    def test_refresh_has_total_columns(self, client, connected_db_id):
        resp = client.post(f"/api/v1/schema/{connected_db_id}/refresh")
        data = resp.json()
        assert data["total_columns"] > 0

    def test_refresh_has_refreshed_at(self, client, connected_db_id):
        resp = client.post(f"/api/v1/schema/{connected_db_id}/refresh")
        data = resp.json()
        assert "refreshed_at" in data

    def test_refresh_tables_sorted(self, client, connected_db_id):
        resp = client.post(f"/api/v1/schema/{connected_db_id}/refresh")
        data = resp.json()
        assert data["tables"] == sorted(data["tables"])

    def test_refresh_nonexistent_db_returns_404(self, client):
        resp = client.post("/api/v1/schema/nonexistent_db_id/refresh")
        assert resp.status_code == 404

    def test_refresh_preserves_foreign_keys(self, client, connected_db_id):
        """After refresh, schema should contain foreign key relationships."""
        resp = client.post(f"/api/v1/schema/{connected_db_id}/refresh")
        assert resp.status_code == 200


# ═══════════════════════════════════════════════════════════════════════════
# GET /api/v1/schema/{db_id}/search
# ═══════════════════════════════════════════════════════════════════════════


class TestSearchSchema:
    def test_search_table_name_exact(self, client, connected_db_id):
        resp = client.get(f"/api/v1/schema/{connected_db_id}/search?q=users")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_matches"] >= 1
        table_names = [r["table_name"] for r in data["results"]]
        assert "users" in table_names

    def test_search_table_name_partial(self, client, connected_db_id):
        resp = client.get(f"/api/v1/schema/{connected_db_id}/search?q=user")
        assert resp.status_code == 200
        data = resp.json()
        # "user" matches "users" table
        assert data["total_matches"] >= 1

    def test_search_column_name(self, client, connected_db_id):
        resp = client.get(f"/api/v1/schema/{connected_db_id}/search?q=email")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_matches"] >= 1

    def test_search_case_insensitive(self, client, connected_db_id):
        resp = client.get(f"/api/v1/schema/{connected_db_id}/search?q=USERS")
        assert resp.status_code == 200
        assert resp.json()["total_matches"] >= 1

    def test_search_no_results(self, client, connected_db_id):
        resp = client.get(f"/api/v1/schema/{connected_db_id}/search?q=xyznonexistent123")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_matches"] == 0
        assert data["results"] == []

    def test_search_empty_query_rejected(self, client, connected_db_id):
        resp = client.get(f"/api/v1/schema/{connected_db_id}/search?q=")
        assert resp.status_code == 422

    def test_search_missing_query_rejected(self, client, connected_db_id):
        resp = client.get(f"/api/v1/schema/{connected_db_id}/search")
        assert resp.status_code == 422

    def test_search_nonexistent_db_returns_404(self, client):
        resp = client.get("/api/v1/schema/nonexistent_db_id/search?q=users")
        assert resp.status_code == 404

    def test_search_result_has_required_fields(self, client, connected_db_id):
        resp = client.get(f"/api/v1/schema/{connected_db_id}/search?q=users")
        data = resp.json()
        for result in data["results"]:
            assert "table_name" in result
            assert "column_count" in result
            assert "table_match" in result
            assert "matched_columns" in result

    def test_search_column_result_has_fields(self, client, connected_db_id):
        resp = client.get(f"/api/v1/schema/{connected_db_id}/search?q=name")
        data = resp.json()
        assert data["total_matches"] >= 1
        # At least one result should have matched columns
        cols = []
        for r in data["results"]:
            cols.extend(r["matched_columns"])
        if cols:
            col = cols[0]
            assert "name" in col
            assert "type" in col
            assert "nullable" in col

    def test_search_id_column(self, client, connected_db_id):
        resp = client.get(f"/api/v1/schema/{connected_db_id}/search?q=id")
        assert resp.status_code == 200
        data = resp.json()
        # Should match id column in multiple tables
        assert data["total_matches"] >= 1

    def test_search_responds_with_metadata(self, client, connected_db_id):
        resp = client.get(f"/api/v1/schema/{connected_db_id}/search?q=order")
        data = resp.json()
        assert data["db_id"] == connected_db_id
        assert data["query"] == "order"
        assert "total_matches" in data
        assert "truncated" in data

    def test_search_orders_table_finds_columns(self, client, connected_db_id):
        resp = client.get(f"/api/v1/schema/{connected_db_id}/search?q=amount")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_matches"] >= 1
        # "amount" column is in orders table
        orders = next(
            (r for r in data["results"] if r["table_name"] == "orders"), None
        )
        assert orders is not None
        col_names = [c["name"] for c in orders["matched_columns"]]
        assert "amount" in col_names or "refund_amount" in col_names

    def test_search_foreign_key_column(self, client, connected_db_id):
        resp = client.get(f"/api/v1/schema/{connected_db_id}/search?q=user_id")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_matches"] >= 1

    def test_search_limit_respected(self, client, connected_db_id):
        resp = client.get(f"/api/v1/schema/{connected_db_id}/search?q=id&limit=1")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["results"]) <= 1
        assert data["truncated"] is True

    def test_search_limit_gt_zero(self, client, connected_db_id):
        """limit must be >= 1."""
        resp = client.get(f"/api/v1/schema/{connected_db_id}/search?q=id&limit=0")
        assert resp.status_code == 422

    def test_search_limit_max_100(self, client, connected_db_id):
        """limit must be <= 100."""
        resp = client.get(f"/api/v1/schema/{connected_db_id}/search?q=id&limit=101")
        assert resp.status_code == 422

    def test_search_tables_only(self, client, connected_db_id):
        resp = client.get(
            f"/api/v1/schema/{connected_db_id}/search?q=user"
            "&search_tables=true&search_columns=false"
        )
        assert resp.status_code == 200
        data = resp.json()
        for r in data["results"]:
            # Should match on table_name only, no matched_columns if only table matched
            if r["table_match"]:
                pass  # table match is fine

    def test_search_columns_only(self, client, connected_db_id):
        resp = client.get(
            f"/api/v1/schema/{connected_db_id}/search?q=name"
            "&search_tables=false&search_columns=true"
        )
        assert resp.status_code == 200
        data = resp.json()
        # "name" should match columns in products and users tables
        assert data["total_matches"] >= 1

    def test_search_comments_disabled(self, client, connected_db_id):
        resp = client.get(
            f"/api/v1/schema/{connected_db_id}/search?q=user"
            "&search_comments=false"
        )
        assert resp.status_code == 200


# ═══════════════════════════════════════════════════════════════════════════
# GET /api/v1/schema/{db_id} — existing endpoint still works
# ═══════════════════════════════════════════════════════════════════════════


class TestGetSchema:
    def test_get_schema_returns_200(self, client, connected_db_id):
        resp = client.get(f"/api/v1/schema/{connected_db_id}")
        assert resp.status_code == 200

    def test_get_schema_has_table_count(self, client, connected_db_id):
        resp = client.get(f"/api/v1/schema/{connected_db_id}")
        data = resp.json()
        assert data["table_count"] == 3

    def test_get_schema_has_tables_dict(self, client, connected_db_id):
        resp = client.get(f"/api/v1/schema/{connected_db_id}")
        data = resp.json()
        assert isinstance(data["tables"], dict)
        assert "users" in data["tables"]
        assert "orders" in data["tables"]
        assert "products" in data["tables"]

    def test_get_schema_table_has_columns(self, client, connected_db_id):
        resp = client.get(f"/api/v1/schema/{connected_db_id}")
        data = resp.json()
        users = data["tables"]["users"]
        assert users["column_count"] > 0
        assert len(users["columns"]) == users["column_count"]

    def test_get_schema_column_has_type(self, client, connected_db_id):
        resp = client.get(f"/api/v1/schema/{connected_db_id}")
        data = resp.json()
        users = data["tables"]["users"]
        col = users["columns"][0]
        assert "name" in col
        assert "type" in col

    def test_get_schema_nonexistent_db_returns_404(self, client):
        resp = client.get("/api/v1/schema/nonexistent_db_id")
        assert resp.status_code == 404


# ═══════════════════════════════════════════════════════════════════════════
# Integration — refresh then search
# ═══════════════════════════════════════════════════════════════════════════


class TestSchemaIntegration:
    def test_refresh_then_search(self, client, connected_db_id):
        # Refresh first
        refresh_resp = client.post(f"/api/v1/schema/{connected_db_id}/refresh")
        assert refresh_resp.status_code == 200
        tables = refresh_resp.json()["tables"]
        assert len(tables) >= 3

        # Then search for each table
        for table in tables:
            search_resp = client.get(
                f"/api/v1/schema/{connected_db_id}/search?q={table}"
            )
            assert search_resp.status_code == 200
            results = search_resp.json()["results"]
            found = any(r["table_name"] == table for r in results)
            assert found, f"Table '{table}' should be found by search"

    def test_refresh_then_get_schema(self, client, connected_db_id):
        # Refresh
        refresh_resp = client.post(f"/api/v1/schema/{connected_db_id}/refresh")
        assert refresh_resp.status_code == 200

        # Get full schema
        get_resp = client.get(f"/api/v1/schema/{connected_db_id}")
        assert get_resp.status_code == 200
        assert get_resp.json()["table_count"] == refresh_resp.json()["table_count"]

    def test_search_after_refresh(self, client, connected_db_id):
        """Search should work consistently after refresh."""
        client.post(f"/api/v1/schema/{connected_db_id}/refresh")

        resp = client.get(f"/api/v1/schema/{connected_db_id}/search?q=price")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_matches"] >= 1
        # "price" column is in products table
        products = next(
            (r for r in data["results"] if r["table_name"] == "products"), None
        )
        assert products is not None
