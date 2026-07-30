"""Tests for DomainManager — YAML loading, switching, and auto-detection.

Uses temporary directories with inline YAML files to test the full
load → activate → detect pipeline.
"""

from __future__ import annotations

import pytest

from app.knowledge.domain_manager import DomainManager
from app.models.domain import DomainConfig, DomainMatch

# ── Helpers ────────────────────────────────────────────────────────────

ECOMMERCE_YAML = """
name: ecommerce
label:
  zh: "电商"
  en: "E-commerce"
description:
  zh: "电商领域"
  en: "E-commerce domain"
databases:
  - name: ecommerce
    type: postgresql
    tables: [orders, order_items, users, products, categories]
timezone: "Asia/Shanghai"
currency: "CNY"
keywords:
  - "订单"
  - "用户"
  - "商品"
  - "库存"
  - "order"
  - "user"
  - "product"
  - "revenue"
  - "customer"
glossary:
  - term: "订单金额"
    term_en: "order amount"
    description: "订单支付金额"
    mapping:
      expression: "SUM(orders.amount)"
      type: derived_column
  - term: "客单价"
    term_en: "AOV"
    description: "平均订单金额"
    mapping:
      expression: "AVG(orders.amount)"
      type: derived_column
rules:
  - id: "ecom_price"
    description: "金额保留2位小数"
    pattern: "金额|amount"
    enforce: ["precision=2"]
"""

FINANCE_YAML = """
name: finance
label:
  zh: "金融"
  en: "Finance"
description:
  zh: "金融领域"
  en: "Finance domain"
databases:
  - name: finance
    type: postgresql
    tables: [accounts, transactions, assets, portfolios]
timezone: "UTC"
currency: "USD"
keywords:
  - "账户"
  - "交易"
  - "资产"
  - "风控"
  - "account"
  - "transaction"
  - "asset"
  - "risk"
  - "portfolio"
glossary:
  - term: "净资产"
    term_en: "NAV"
    description: "净资产值"
    mapping:
      expression: "SUM(assets.value) - SUM(liabilities.value)"
      type: derived_column
rules:
  - id: "fin_precision"
    description: "金融金额保留4位小数"
    pattern: "金额|amount|nav"
    enforce: ["precision=4"]
"""

BAD_YAML = """
name: bad_domain
label: "this is not a dict"  # should be a dict
keywords:
  - test
"""


def _write_domain_files(tmp_path, files: dict[str, str]) -> str:
    """Write domain YAML files into a temp directory and return the path."""
    d = tmp_path / "domains"
    d.mkdir()
    for name, content in files.items():
        (d / f"{name}.yml").write_text(content, encoding="utf-8")
    return str(d)


# ── Loading tests ──────────────────────────────────────────────────────


class TestDomainManagerLoad:
    def test_load_all(self, tmp_path):
        domain_dir = _write_domain_files(tmp_path, {
            "ecommerce": ECOMMERCE_YAML,
            "finance": FINANCE_YAML,
        })
        mgr = DomainManager(domain_dir)
        assert len(mgr) == 2
        assert "ecommerce" in mgr
        assert "finance" in mgr

    def test_load_empty_dir(self, tmp_path):
        d = tmp_path / "empty"
        d.mkdir()
        mgr = DomainManager(str(d))
        assert len(mgr) == 0

    def test_load_nonexistent_dir(self):
        mgr = DomainManager("/no/such/dir", auto_load=True)
        assert len(mgr) == 0

    def test_load_no_auto(self, tmp_path):
        domain_dir = _write_domain_files(tmp_path, {"ecommerce": ECOMMERCE_YAML})
        mgr = DomainManager(domain_dir, auto_load=False)
        assert len(mgr) == 0
        mgr.load_all()
        assert len(mgr) == 1

    def test_reload(self, tmp_path):
        domain_dir = _write_domain_files(tmp_path, {"ecommerce": ECOMMERCE_YAML})
        mgr = DomainManager(domain_dir)
        assert len(mgr) == 1
        # Add a new file externally, then reload
        (tmp_path / "domains" / "finance.yml").write_text(FINANCE_YAML, encoding="utf-8")
        mgr.reload()
        assert len(mgr) == 2

    def test_skips_malformed_files(self, tmp_path, caplog):
        """Malformed YAML files should be skipped with a warning, not crash."""
        domain_dir = _write_domain_files(tmp_path, {
            "ecommerce": ECOMMERCE_YAML,
            "bad": "::: not valid yaml :::",
        })
        import logging
        caplog.set_level(logging.WARNING)
        mgr = DomainManager(domain_dir)
        # Should still load the valid one
        assert "ecommerce" in mgr
        # "bad" should not exist as a domain
        assert "bad" not in mgr

    def test_load_preserves_fields(self, tmp_path):
        domain_dir = _write_domain_files(tmp_path, {"ecommerce": ECOMMERCE_YAML})
        mgr = DomainManager(domain_dir)
        domain = mgr.get("ecommerce")
        assert domain is not None
        assert domain.name == "ecommerce"
        assert domain.label["zh"] == "电商"
        assert domain.timezone == "Asia/Shanghai"
        assert domain.currency == "CNY"
        assert len(domain.keywords) >= 5
        assert len(domain.glossary) == 2
        assert len(domain.rules) == 1
        assert domain.databases[0]["name"] == "ecommerce"


# ── Access tests ───────────────────────────────────────────────────────


class TestDomainManagerAccess:
    @pytest.fixture
    def mgr(self, tmp_path) -> DomainManager:
        domain_dir = _write_domain_files(tmp_path, {
            "ecommerce": ECOMMERCE_YAML,
            "finance": FINANCE_YAML,
        })
        return DomainManager(domain_dir)

    def test_get_existing(self, mgr: DomainManager):
        d = mgr.get("ecommerce")
        assert d is not None
        assert isinstance(d, DomainConfig)
        assert d.name == "ecommerce"

    def test_get_missing(self, mgr: DomainManager):
        assert mgr.get("nonexistent") is None

    def test_list_all(self, mgr: DomainManager):
        names = mgr.list_all()
        assert names == ["ecommerce", "finance"]

    def test_len(self, mgr: DomainManager):
        assert len(mgr) == 2

    def test_contains(self, mgr: DomainManager):
        assert "ecommerce" in mgr
        assert "unknown" not in mgr


# ── Activation tests ───────────────────────────────────────────────────


class TestDomainManagerActivation:
    @pytest.fixture
    def mgr(self, tmp_path) -> DomainManager:
        domain_dir = _write_domain_files(tmp_path, {
            "ecommerce": ECOMMERCE_YAML,
            "finance": FINANCE_YAML,
        })
        return DomainManager(domain_dir)

    def test_set_active(self, mgr: DomainManager):
        mgr.set_active("ecommerce")
        assert mgr.active_name == "ecommerce"
        assert mgr.active_domain is not None
        assert mgr.active_domain.name == "ecommerce"

    def test_switch_active(self, mgr: DomainManager):
        mgr.set_active("ecommerce")
        mgr.set_active("finance")
        assert mgr.active_name == "finance"

    def test_set_active_unknown_raises(self, mgr: DomainManager):
        with pytest.raises(KeyError, match="not found"):
            mgr.set_active("unknown")

    def test_clear_active(self, mgr: DomainManager):
        mgr.set_active("ecommerce")
        mgr.clear_active()
        assert mgr.active_name is None
        assert mgr.active_domain is None

    def test_active_never_set(self, mgr: DomainManager):
        assert mgr.active_name is None
        assert mgr.active_domain is None


# ── Detection tests ────────────────────────────────────────────────────


class TestDomainManagerDetect:
    @pytest.fixture
    def mgr(self, tmp_path) -> DomainManager:
        domain_dir = _write_domain_files(tmp_path, {
            "ecommerce": ECOMMERCE_YAML,
            "finance": FINANCE_YAML,
        })
        return DomainManager(domain_dir)

    def test_detect_returns_all_sorted(self, mgr: DomainManager):
        matches = mgr.detect("查询用户订单")
        assert len(matches) == 2
        # E-commerce should rank higher for this query
        assert matches[0].domain.name == "ecommerce"
        assert matches[0].score >= matches[1].score

    def test_detect_keyword_matching(self, mgr: DomainManager):
        """Strong ecommerce keywords should give high score."""
        matches = mgr.detect("订单 用户 商品 revenue customer")
        best = matches[0]
        assert best.domain.name == "ecommerce"
        assert len(best.matched_keywords) >= 4

    def test_detect_finance_keywords(self, mgr: DomainManager):
        matches = mgr.detect("账户 交易 资产 risk portfolio")
        best = matches[0]
        assert best.domain.name == "finance"
        assert len(best.matched_keywords) >= 4

    def test_detect_term_matching(self, mgr: DomainManager):
        """Queries containing glossary terms should match."""
        matches = mgr.detect("订单金额是多少")
        best = matches[0]
        assert best.domain.name == "ecommerce"
        assert "订单金额" in best.matched_terms

    def test_detect_term_english(self, mgr: DomainManager):
        matches = mgr.detect("what is the AOV")
        best = matches[0]
        assert best.domain.name == "ecommerce"
        assert "客单价" in best.matched_terms  # matched via term_en "AOV"

    def test_detect_empty_query(self, mgr: DomainManager):
        matches = mgr.detect("")
        assert len(matches) == 2
        # All scores should be 0 (or very close)
        for m in matches:
            assert m.score == 0.0

    def test_detect_unknown_domain(self, mgr: DomainManager):
        """Query with no matching keywords should give low scores."""
        matches = mgr.detect("xyz foo bar baz qux")
        for m in matches:
            assert m.score <= 0.2

    def test_detect_with_schema(self, mgr: DomainManager):
        """Schema matching L3 should boost ecommerce score."""
        from app.models.schema import SchemaSnapshot
        # Create a schema that has ecommerce tables
        schema = SchemaSnapshot(
            database_type="postgresql",
            database_name="test",
            tables={"orders": None, "users": None},
        )
        matches = mgr.detect("query", schema=schema)
        # E-commerce should have a non-zero L3 component
        ecom = next(m for m in matches if m.domain.name == "ecommerce")
        fin = next(m for m in matches if m.domain.name == "finance")
        # With schema matching, ecom should score higher
        assert ecom.score >= fin.score

    def test_detect_score_range(self, mgr: DomainManager):
        matches = mgr.detect("订单 用户")
        for m in matches:
            assert 0.0 <= m.score <= 1.0

    def test_detect_chinese_preference(self, mgr: DomainManager):
        """Chinese ecommerce terms should strongly match ecommerce."""
        matches = mgr.detect("查询用户的订单金额和商品库存")
        best = matches[0]
        assert best.domain.name == "ecommerce"
        # Should have matched multiple keywords
        assert len(best.matched_keywords) >= 2

    def test_detect_english_preference(self, mgr: DomainManager):
        """English finance terms should match finance."""
        matches = mgr.detect("show me the account transactions and risk metrics")
        best = matches[0]
        assert best.domain.name == "finance"

    def test_detect_ambiguous_query(self, mgr: DomainManager):
        """When query is ambiguous, scores should be close."""
        # "account" appears in neither domain's keywords — test pure ambiguity
        matches = mgr.detect("get data")
        score_diff = abs(matches[0].score - matches[1].score)
        # Scores should be very close for a query matching no keywords
        assert score_diff <= 0.05


# ── detect_best tests ──────────────────────────────────────────────────


class TestDomainManagerDetectBest:
    @pytest.fixture
    def mgr(self, tmp_path) -> DomainManager:
        domain_dir = _write_domain_files(tmp_path, {
            "ecommerce": ECOMMERCE_YAML,
            "finance": FINANCE_YAML,
        })
        return DomainManager(domain_dir)

    def test_detect_best_above_threshold(self, mgr: DomainManager):
        best = mgr.detect_best("订单 用户 商品 revenue", threshold=0.1)
        assert best is not None
        assert best.domain.name == "ecommerce"

    def test_detect_best_below_threshold(self, mgr: DomainManager):
        best = mgr.detect_best("xyz foo bar", threshold=0.6)
        assert best is None  # no strong match

    def test_detect_best_default_threshold(self, mgr: DomainManager):
        """Default threshold 0.6 should filter weak matches."""
        best = mgr.detect_best("xyz foo bar")
        assert best is None

    def test_detect_best_custom_threshold(self, mgr: DomainManager):
        """With a very low threshold, even weak matches pass."""
        best = mgr.detect_best("xyz foo bar", threshold=0.0)
        assert best is not None  # always returns top match with threshold=0

    def test_detect_best_empty_manager(self):
        mgr = DomainManager("/no/such/dir", auto_load=True)
        assert mgr.detect_best("anything") is None


# ── Edge cases ────────────────────────────────────────────────────────


class TestDomainManagerEdgeCases:
    def test_detect_with_no_keywords_domain(self, tmp_path):
        """Domain with no keywords should get score 0.0 from L1."""
        yaml = """
name: minimal
label:
  zh: "最小"
databases: []
keywords: []
glossary: []
rules: []
"""
        domain_dir = _write_domain_files(tmp_path, {"minimal": yaml})
        mgr = DomainManager(domain_dir)
        matches = mgr.detect("any query")
        assert len(matches) == 1
        assert matches[0].score == 0.0

    def test_domain_match_attributes(self, tmp_path):
        domain_dir = _write_domain_files(tmp_path, {"ecommerce": ECOMMERCE_YAML})
        mgr = DomainManager(domain_dir)
        matches = mgr.detect("订单金额")
        best = matches[0]
        assert isinstance(best, DomainMatch)
        assert isinstance(best.domain, DomainConfig)
        assert best.score > 0.0
        assert isinstance(best.matched_keywords, list)
        assert isinstance(best.matched_terms, list)

    def test_reload_preserves_active(self, tmp_path):
        domain_dir = _write_domain_files(tmp_path, {"ecommerce": ECOMMERCE_YAML})
        mgr = DomainManager(domain_dir)
        mgr.set_active("ecommerce")
        mgr.reload()
        assert mgr.active_name == "ecommerce"

    def test_match_does_not_case_sensitive(self, tmp_path):
        domain_dir = _write_domain_files(tmp_path, {"ecommerce": ECOMMERCE_YAML})
        mgr = DomainManager(domain_dir)
        # Mixed case should still match
        matches = mgr.detect("Order User Revenue Customer")
        best = matches[0]
        assert len(best.matched_keywords) >= 2
