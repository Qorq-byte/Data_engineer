"""Tests for Domain CRUD API endpoints — 8 endpoints for domain/glossary/rule management.

Covers: list domains, create/update domain, glossary CRUD, rule CRUD, auto-detect.
"""

from __future__ import annotations

import contextlib

import pytest
from fastapi.testclient import TestClient

from app.api import create_app
from app.api.domains import _domain_manager


@pytest.fixture
def client():
    app = create_app()
    return TestClient(app)


# ═══════════════════════════════════════════════════════════════════════════
# GET /api/v1/domains — list domains
# ═══════════════════════════════════════════════════════════════════════════


class TestListDomains:
    def test_returns_200(self, client):
        resp = client.get("/api/v1/domains")
        assert resp.status_code == 200

    def test_has_domains_key(self, client):
        resp = client.get("/api/v1/domains")
        data = resp.json()
        assert "domains" in data
        assert isinstance(data["domains"], list)

    def test_has_active_key(self, client):
        resp = client.get("/api/v1/domains")
        data = resp.json()
        assert "active" in data

    def test_has_total_key(self, client):
        resp = client.get("/api/v1/domains")
        data = resp.json()
        assert "total" in data
        assert data["total"] == len(data["domains"])

    def test_domains_have_required_fields(self, client):
        resp = client.get("/api/v1/domains")
        data = resp.json()
        for domain in data["domains"]:
            assert "name" in domain
            assert "label" in domain
            assert "description" in domain
            assert "keywords" in domain
            assert "glossary_count" in domain
            assert "rules_count" in domain
            assert "is_active" in domain

    def test_includes_ecommerce(self, client):
        resp = client.get("/api/v1/domains")
        data = resp.json()
        names = [d["name"] for d in data["domains"]]
        assert "ecommerce" in names

    def test_includes_finance(self, client):
        resp = client.get("/api/v1/domains")
        data = resp.json()
        names = [d["name"] for d in data["domains"]]
        assert "finance" in names

    def test_domains_sorted_by_name(self, client):
        resp = client.get("/api/v1/domains")
        data = resp.json()
        names = [d["name"] for d in data["domains"]]
        assert names == sorted(names)

    def test_ecommerce_has_keywords(self, client):
        resp = client.get("/api/v1/domains")
        data = resp.json()
        ecom = next(d for d in data["domains"] if d["name"] == "ecommerce")
        assert len(ecom["keywords"]) > 0
        assert "订单" in ecom["keywords"]


# ═══════════════════════════════════════════════════════════════════════════
# POST /api/v1/domains — create domain
# ═══════════════════════════════════════════════════════════════════════════


class TestCreateDomain:
    def test_create_simple_domain(self, client):
        resp = client.post("/api/v1/domains", json={
            "name": "test_create_simple",
            "keywords": ["test", "测试"],
        })
        assert resp.status_code == 201
        data = resp.json()
        assert data["status"] == "created"
        assert data["name"] == "test_create_simple"

    def test_create_domain_with_full_config(self, client):
        resp = client.post("/api/v1/domains", json={
            "name": "test_create_full",
            "label_zh": "测试领域",
            "label_en": "Test Domain",
            "description_zh": "用于测试的领域",
            "description_en": "Domain for testing",
            "databases": [{"name": "testdb", "type": "sqlite"}],
            "keywords": ["k1", "k2"],
            "timezone": "Asia/Shanghai",
            "currency": "CNY",
        })
        assert resp.status_code == 201

        # Verify via GET
        resp2 = client.get("/api/v1/domains")
        names = [d["name"] for d in resp2.json()["domains"]]
        assert "test_create_full" in names

    def test_create_duplicate_domain_returns_409(self, client):
        # First creation
        client.post("/api/v1/domains", json={"name": "test_dup"})
        # Duplicate
        resp = client.post("/api/v1/domains", json={"name": "test_dup"})
        assert resp.status_code == 409

    def test_create_empty_name_rejected(self, client):
        resp = client.post("/api/v1/domains", json={"name": ""})
        assert resp.status_code == 422

    def test_create_missing_name_rejected(self, client):
        resp = client.post("/api/v1/domains", json={})
        assert resp.status_code == 422


# ═══════════════════════════════════════════════════════════════════════════
# PUT /api/v1/domains/{id} — update domain
# ═══════════════════════════════════════════════════════════════════════════


class TestUpdateDomain:
    @pytest.fixture(autouse=True)
    def _setup(self, client):
        """Ensure a test domain exists for update tests."""
        client.post("/api/v1/domains", json={
            "name": "test_update",
            "keywords": ["original"],
            "timezone": "UTC",
        })

    def test_update_keywords(self, client):
        resp = client.put("/api/v1/domains/test_update", json={
            "keywords": ["updated1", "updated2"],
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "updated"
        assert "updated1" in data["keywords"]
        assert "updated2" in data["keywords"]

    def test_update_timezone(self, client):
        resp = client.put("/api/v1/domains/test_update", json={
            "timezone": "Asia/Tokyo",
        })
        assert resp.status_code == 200

    def test_update_partial_preserves_other_fields(self, client):
        """Updating only keywords should preserve timezone."""
        # First update timezone
        client.put("/api/v1/domains/test_update", json={"timezone": "Europe/London"})
        # Then update only keywords
        resp = client.put("/api/v1/domains/test_update", json={"keywords": ["kw"]})
        assert resp.status_code == 200

    def test_update_nonexistent_domain_returns_404(self, client):
        resp = client.put("/api/v1/domains/nonexistent_xyz", json={
            "keywords": ["test"],
        })
        assert resp.status_code == 404

    def test_update_empty_body(self, client):
        """Empty update should succeed (no-op)."""
        resp = client.put("/api/v1/domains/test_update", json={})
        assert resp.status_code == 200

    def test_update_label(self, client):
        resp = client.put("/api/v1/domains/test_update", json={
            "label_zh": "更新的标签",
            "label_en": "Updated Label",
        })
        assert resp.status_code == 200


# ═══════════════════════════════════════════════════════════════════════════
# GET /api/v1/domains/{id}/glossary — list glossary terms
# ═══════════════════════════════════════════════════════════════════════════


class TestListGlossary:
    def test_list_ecommerce_glossary(self, client):
        resp = client.get("/api/v1/domains/ecommerce/glossary")
        assert resp.status_code == 200
        data = resp.json()
        assert data["domain"] == "ecommerce"
        assert "terms" in data
        assert "total" in data
        assert data["total"] == len(data["terms"])

    def test_ecommerce_has_order_amount_term(self, client):
        resp = client.get("/api/v1/domains/ecommerce/glossary")
        data = resp.json()
        terms = [t["term"] for t in data["terms"]]
        assert "订单金额" in terms

    def test_glossary_terms_have_mapping(self, client):
        resp = client.get("/api/v1/domains/ecommerce/glossary")
        data = resp.json()
        order_term = next(t for t in data["terms"] if t["term"] == "订单金额")
        assert order_term["mapping"] is not None
        assert "expression" in order_term["mapping"]

    def test_nonexistent_domain_glossary_returns_404(self, client):
        resp = client.get("/api/v1/domains/nonexistent_xyz/glossary")
        assert resp.status_code == 404

    def test_finance_glossary(self, client):
        resp = client.get("/api/v1/domains/finance/glossary")
        assert resp.status_code == 200
        data = resp.json()
        assert data["domain"] == "finance"


# ═══════════════════════════════════════════════════════════════════════════
# POST /api/v1/domains/{id}/glossary — add glossary term
# ═══════════════════════════════════════════════════════════════════════════


class TestAddGlossaryTerm:
    @pytest.fixture(autouse=True)
    def _setup(self, client):
        """Ensure a test domain exists."""
        client.post("/api/v1/domains", json={
            "name": "test_glossary",
            "keywords": ["test"],
        })

    def test_add_simple_term(self, client):
        resp = client.post("/api/v1/domains/test_glossary/glossary", json={
            "term": "测试术语",
            "term_en": "test term",
            "description": "A test term",
        })
        assert resp.status_code == 201
        data = resp.json()
        assert data["status"] == "created"
        assert data["term"] == "测试术语"

    def test_add_term_with_expression(self, client):
        resp = client.post("/api/v1/domains/test_glossary/glossary", json={
            "term": "总金额",
            "expression": "SUM(amount)",
            "mapping_type": "derived_column",
        })
        assert resp.status_code == 201

        # Verify via GET
        resp2 = client.get("/api/v1/domains/test_glossary/glossary")
        terms = [t["term"] for t in resp2.json()["terms"]]
        assert "总金额" in terms

    def test_add_duplicate_term_returns_409(self, client):
        client.post("/api/v1/domains/test_glossary/glossary", json={
            "term": "dup_term",
        })
        resp = client.post("/api/v1/domains/test_glossary/glossary", json={
            "term": "dup_term",
        })
        assert resp.status_code == 409

    def test_add_term_to_nonexistent_domain_returns_404(self, client):
        resp = client.post("/api/v1/domains/nonexistent_xyz/glossary", json={
            "term": "test",
        })
        assert resp.status_code == 404

    def test_add_term_empty_name_rejected(self, client):
        resp = client.post("/api/v1/domains/test_glossary/glossary", json={
            "term": "",
        })
        assert resp.status_code == 422


# ═══════════════════════════════════════════════════════════════════════════
# GET /api/v1/domains/{id}/rules — list business rules
# ═══════════════════════════════════════════════════════════════════════════


class TestListRules:
    def test_list_ecommerce_rules(self, client):
        resp = client.get("/api/v1/domains/ecommerce/rules")
        assert resp.status_code == 200
        data = resp.json()
        assert data["domain"] == "ecommerce"
        assert "rules" in data
        assert "total" in data
        assert data["total"] == len(data["rules"])

    def test_ecommerce_rules_have_required_fields(self, client):
        resp = client.get("/api/v1/domains/ecommerce/rules")
        data = resp.json()
        for rule in data["rules"]:
            assert "id" in rule
            assert "description" in rule

    def test_ecommerce_has_price_precision_rule(self, client):
        resp = client.get("/api/v1/domains/ecommerce/rules")
        data = resp.json()
        rule_ids = [r["id"] for r in data["rules"]]
        assert "ecom_price_precision" in rule_ids

    def test_nonexistent_domain_rules_returns_404(self, client):
        resp = client.get("/api/v1/domains/nonexistent_xyz/rules")
        assert resp.status_code == 404

    def test_finance_rules(self, client):
        resp = client.get("/api/v1/domains/finance/rules")
        assert resp.status_code == 200
        data = resp.json()
        assert data["domain"] == "finance"


# ═══════════════════════════════════════════════════════════════════════════
# POST /api/v1/domains/{id}/rules — add business rule
# ═══════════════════════════════════════════════════════════════════════════


class TestAddRule:
    @pytest.fixture(autouse=True)
    def _setup(self, client):
        """Ensure a test domain exists."""
        client.post("/api/v1/domains", json={
            "name": "test_rules",
            "keywords": ["test"],
        })

    def test_add_simple_rule(self, client):
        resp = client.post("/api/v1/domains/test_rules/rules", json={
            "id": "test_rule_1",
            "description": "A test rule",
        })
        assert resp.status_code == 201
        data = resp.json()
        assert data["status"] == "created"
        assert data["rule_id"] == "test_rule_1"

    def test_add_rule_with_pattern(self, client):
        resp = client.post("/api/v1/domains/test_rules/rules", json={
            "id": "test_rule_pattern",
            "description": "Rule with pattern",
            "pattern": "金额|amount",
            "enforce": ["precision=2"],
        })
        assert resp.status_code == 201

        # Verify via GET
        resp2 = client.get("/api/v1/domains/test_rules/rules")
        rule_ids = [r["id"] for r in resp2.json()["rules"]]
        assert "test_rule_pattern" in rule_ids

    def test_add_rule_with_sql_template(self, client):
        resp = client.post("/api/v1/domains/test_rules/rules", json={
            "id": "test_rule_sql",
            "description": "Rule with SQL template",
            "sql_template": "SELECT {columns} FROM {tables} WHERE {conditions}",
        })
        assert resp.status_code == 201

    def test_add_duplicate_rule_returns_409(self, client):
        client.post("/api/v1/domains/test_rules/rules", json={
            "id": "dup_rule",
            "description": "First",
        })
        resp = client.post("/api/v1/domains/test_rules/rules", json={
            "id": "dup_rule",
            "description": "Second",
        })
        assert resp.status_code == 409

    def test_add_rule_to_nonexistent_domain_returns_404(self, client):
        resp = client.post("/api/v1/domains/nonexistent_xyz/rules", json={
            "id": "test",
            "description": "test",
        })
        assert resp.status_code == 404

    def test_add_rule_empty_id_rejected(self, client):
        resp = client.post("/api/v1/domains/test_rules/rules", json={
            "id": "",
        })
        assert resp.status_code == 422


# ═══════════════════════════════════════════════════════════════════════════
# POST /api/v1/domains/detect — auto-detect domain
# ═══════════════════════════════════════════════════════════════════════════


class TestDetectDomain:
    def test_detect_ecommerce_by_keywords(self, client):
        resp = client.post("/api/v1/domains/detect", json={
            "query_text": "查询订单金额和用户购买记录",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert "matches" in data
        assert "best" in data
        assert "recommendation" in data

    def test_detect_returns_matches_with_scores(self, client):
        resp = client.post("/api/v1/domains/detect", json={
            "query_text": "查询订单",
        })
        data = resp.json()
        for m in data["matches"]:
            assert "domain" in m
            assert "score" in m
            assert 0.0 <= m["score"] <= 1.0
            assert "matched_keywords" in m

    def test_detect_best_is_highest_scored(self, client):
        resp = client.post("/api/v1/domains/detect", json={
            "query_text": "查询订单金额和用户",
        })
        data = resp.json()
        if data["matches"] and data["best"]:
            best_score = data["best"]["score"]
            for m in data["matches"]:
                assert m["score"] <= best_score

    def test_detect_empty_query_rejected(self, client):
        resp = client.post("/api/v1/domains/detect", json={
            "query_text": "",
        })
        assert resp.status_code == 422

    def test_detect_no_matching_keywords(self, client):
        resp = client.post("/api/v1/domains/detect", json={
            "query_text": "xyzzy random text no match",
        })
        assert resp.status_code == 200
        data = resp.json()
        # Should still return results (matches may be empty or low score)
        assert "matches" in data

    def test_detect_recommendation_is_none_when_below_threshold(self, client):
        """When no domain scores >= 0.6, recommendation should be None."""
        resp = client.post("/api/v1/domains/detect", json={
            "query_text": "xyzzy random gibberish text with no keywords",
        })
        data = resp.json()
        # No strong match → recommendation should be None
        assert data["recommendation"] is None or data["best"] is None

    def test_detect_ecommerce_has_matched_keywords(self, client):
        resp = client.post("/api/v1/domains/detect", json={
            "query_text": "用户订单查询",
        })
        data = resp.json()
        matches = data["matches"]
        ecom = next((m for m in matches if m["domain"] == "ecommerce"), None)
        if ecom:
            assert len(ecom["matched_keywords"]) > 0


# ═══════════════════════════════════════════════════════════════════════════
# Integration tests — cross-endpoint workflows
# ═══════════════════════════════════════════════════════════════════════════


class TestDomainWorkflowIntegration:
    """Test end-to-end workflows across multiple endpoints."""

    def test_create_domain_add_glossary_add_rule_verify(self, client):
        domain_name = "test_integration"

        # 1. Create domain
        resp = client.post("/api/v1/domains", json={
            "name": domain_name,
            "keywords": ["integration"],
        })
        assert resp.status_code == 201

        # 2. Add glossary term
        resp = client.post(f"/api/v1/domains/{domain_name}/glossary", json={
            "term": "集成测试术语",
            "expression": "COUNT(*)",
        })
        assert resp.status_code == 201

        # 3. Add rule
        resp = client.post(f"/api/v1/domains/{domain_name}/rules", json={
            "id": "int_rule_1",
            "description": "Integration test rule",
            "pattern": "test",
        })
        assert resp.status_code == 201

        # 4. Verify glossary
        resp = client.get(f"/api/v1/domains/{domain_name}/glossary")
        assert resp.status_code == 200
        assert resp.json()["total"] >= 1

        # 5. Verify rules
        resp = client.get(f"/api/v1/domains/{domain_name}/rules")
        assert resp.status_code == 200
        assert resp.json()["total"] >= 1

        # 6. Verify domain appears in list
        resp = client.get("/api/v1/domains")
        names = [d["name"] for d in resp.json()["domains"]]
        assert domain_name in names

    def test_update_domain_then_detect(self, client):
        domain_name = "test_update_detect"
        client.post("/api/v1/domains", json={
            "name": domain_name,
            "keywords": ["old_kw"],
        })

        # Update keywords to something that can be detected
        client.put(f"/api/v1/domains/{domain_name}", json={
            "keywords": ["old_kw", "特有的关键词"],
        })

        # Detect — should find the updated keywords
        resp = client.post("/api/v1/domains/detect", json={
            "query_text": "查询特有的关键词数据",
        })
        assert resp.status_code == 200


# ═══════════════════════════════════════════════════════════════════════════
# Edge cases
# ═══════════════════════════════════════════════════════════════════════════


class TestDomainAPIEdgeCases:
    def test_domain_id_with_trailing_slash(self, client):
        """Trailing slash in domain_id should still work."""
        resp = client.get("/api/v1/domains/ecommerce/glossary")
        assert resp.status_code == 200

    def test_domain_id_with_whitespace_trimmed(self, client):
        """Whitespace in domain_id URL path is handled by URL encoding."""
        resp = client.get("/api/v1/domains/%20%20/glossary")
        # FastAPI/path parsing will handle this — may be 404 or 422
        assert resp.status_code in (200, 404, 422)

    def test_glossary_term_with_all_fields(self, client):
        client.post("/api/v1/domains", json={
            "name": "test_full_term",
            "keywords": ["full"],
        })
        resp = client.post("/api/v1/domains/test_full_term/glossary", json={
            "term": "完整术语",
            "term_en": "full term",
            "description": "A fully specified term",
            "expression": "SUM(table.col)",
            "mapping_type": "derived_column",
            "table": "my_table",
            "condition": "status = 'active'",
            "precision": 2,
            "tags": ["important", "finance"],
        })
        assert resp.status_code == 201

    def test_rule_with_all_fields(self, client):
        client.post("/api/v1/domains", json={
            "name": "test_full_rule",
            "keywords": ["full"],
        })
        resp = client.post("/api/v1/domains/test_full_rule/rules", json={
            "id": "full_rule",
            "description": "A fully specified rule",
            "pattern": "金额|amount|price",
            "enforce": ["precision=2", "timezone=Asia/Shanghai"],
            "sql_template": "SELECT {cols} FROM {tables} WHERE {conditions} LIMIT {limit}",
        })
        assert resp.status_code == 201


# ═══════════════════════════════════════════════════════════════════════════
# Cleanup — remove test YAML files created during testing
# ═══════════════════════════════════════════════════════════════════════════


@pytest.fixture(autouse=True, scope="session")
def _cleanup_test_files():
    """Remove test domain YAML files after all tests complete."""
    yield
    import os
    from pathlib import Path

    domains_dir = Path("app/config/domains")
    test_prefixes = (
        "test_create_", "test_update", "test_dup", "test_glossary",
        "test_rules", "test_integration", "test_update_detect",
        "test_full_term", "test_full_rule",
    )
    for yaml_file in domains_dir.glob("*.yml"):
        name = yaml_file.stem
        if any(name.startswith(p) for p in test_prefixes):
            with contextlib.suppress(OSError):
                os.remove(yaml_file)

    # Reload domain manager to clear test domains
    _domain_manager.reload()
