"""Tests for MetricRAG — hybrid metric search (LanceDB + BM25 + RRF).

Integration tests that exercise the full indexing → search pipeline for
business KPIs and metric definitions.
"""

from __future__ import annotations

import pytest

from app.knowledge.retrieval.bm25_index import BM25Index
from app.knowledge.retrieval.embedding import EmbeddingGenerator, MockEmbeddingProvider
from app.knowledge.retrieval.lancedb_store import LanceDBStore
from app.knowledge.retrieval.metric_rag import (
    MetricRAG,
    MetricSearchResult,
    _extract_index_meta,
    _parse_json_list,
    _row_to_metric_result,
)
from app.knowledge.retrieval.rrf_fusion import RRFFusion
from app.models.rag_metric import MetricDocument

# ── Helpers ────────────────────────────────────────────────────────────

DIM = 128  # small dimension for fast tests


def _make_metric_doc(
    doc_id: str,
    domain: str = "sales",
    name: str = "revenue",
    name_zh: str = "营收",
    aliases: list[str] | None = None,
    description: str = "Total revenue from orders",
    formula: str = "SUM(amount)",
    metric_type: str = "derived",
    aggregation: str = "sum",
    dimensions: list[str] | None = None,
    time_grain: str = "day",
    precision: int = 2,
    sql_template: str = "",
    embedding_text: str = "",
) -> MetricDocument:
    return MetricDocument(
        doc_id=doc_id,
        domain=domain,
        name=name,
        name_zh=name_zh,
        aliases=aliases or [],
        description=description,
        formula=formula,
        metric_type=metric_type,
        aggregation=aggregation,
        dimensions=dimensions or ["date", "region"],
        time_grain=time_grain,
        precision=precision,
        sql_template=sql_template,
        embedding_text=embedding_text or f"{name} — {description}",
    )


def _make_rag(
    tmp_path, alpha: float = 0.5, k: int = 60
) -> MetricRAG:
    """Build a MetricRAG with file-backed LanceDB + in-memory BM25."""
    lancedb_uri = str(tmp_path / "lancedb_metric_rag")
    return MetricRAG(
        lancedb_store=LanceDBStore(dimension=DIM, uri=lancedb_uri),
        bm25_index=BM25Index(":memory:"),
        embedding_generator=EmbeddingGenerator(provider=MockEmbeddingProvider(dimension=DIM)),
        rrf_fusion=RRFFusion(k=k, alpha=alpha),
    )


# ── Helper function tests ──────────────────────────────────────────────


class TestHelpers:
    def test_extract_index_meta(self):
        doc = _make_metric_doc("sales.revenue", domain="sales", name="revenue")
        meta = _extract_index_meta(doc)
        assert meta["domain"] == "sales"
        assert meta["name"] == "revenue"
        assert meta["name_zh"] == "营收"
        assert meta["formula"] == "SUM(amount)"
        assert meta["metric_type"] == "derived"
        assert meta["aggregation"] == "sum"

    def test_row_to_metric_result(self):
        row = {
            "doc_id": "sales.revenue",
            "domain": "sales",
            "name": "revenue",
            "name_zh": "营收",
            "aliases_json": '["income", "sales_revenue"]',
            "description": "Total revenue",
            "formula": "SUM(amount)",
            "metric_type": "derived",
            "aggregation": "sum",
            "dimensions_json": '["date", "region"]',
            "time_grain": "month",
            "precision": 2,
            "sql_template": "",
            "embedding_text": "revenue — Total revenue",
            "rrf_score": 0.015,
            "dense_rank": 1,
            "sparse_rank": 2,
        }
        mr = _row_to_metric_result(row)
        assert mr.doc_id == "sales.revenue"
        assert mr.name == "revenue"
        assert mr.name_zh == "营收"
        assert mr.aliases == ["income", "sales_revenue"]
        assert mr.dimensions == ["date", "region"]
        assert mr.formula == "SUM(amount)"
        assert mr.rrf_score == 0.015
        assert mr.dense_rank == 1
        assert mr.sparse_rank == 2
        assert mr.has_formula is True
        assert mr.has_sql_template is False

    def test_row_to_metric_result_with_sql_template(self):
        row = {
            "doc_id": "sales.revenue",
            "domain": "sales",
            "name": "revenue",
            "name_zh": "",
            "aliases_json": "[]",
            "description": "",
            "formula": "",
            "metric_type": "derived",
            "aggregation": "sum",
            "dimensions_json": "[]",
            "time_grain": "day",
            "precision": 2,
            "sql_template": "SELECT SUM(amount) FROM orders",
            "embedding_text": "revenue",
            "rrf_score": 0.01,
            "dense_rank": 1,
            "sparse_rank": None,
        }
        mr = _row_to_metric_result(row)
        assert mr.has_formula is False
        assert mr.has_sql_template is True

    def test_parse_json_list_already_list(self):
        assert _parse_json_list(["a", "b"]) == ["a", "b"]

    def test_parse_json_list_valid_json(self):
        assert _parse_json_list('["x", "y"]') == ["x", "y"]

    def test_parse_json_list_invalid_json(self):
        assert _parse_json_list("not-json") == []

    def test_parse_json_list_none(self):
        assert _parse_json_list(None) == []


# ── Integration tests ──────────────────────────────────────────────────


class TestMetricRAG:
    """Integration tests for the full index → search pipeline."""

    @pytest.fixture
    def rag(self, tmp_path) -> MetricRAG:
        return _make_rag(tmp_path)

    # ── Construction ───────────────────────────────────────────────────

    def test_construction_defaults(self, tmp_path):
        rag = _make_rag(tmp_path)
        assert rag.embedding_dimension == DIM
        assert rag.rrf_alpha == 0.5
        assert rag.rrf_k == 60

    def test_construction_custom_rrf(self, tmp_path):
        rag = _make_rag(tmp_path, alpha=0.7, k=30)
        assert rag.rrf_alpha == 0.7
        assert rag.rrf_k == 30

    def test_namespace_is_metrics(self, rag: MetricRAG):
        assert rag.NAMESPACE == "metrics"

    # ── Indexing ───────────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_index_metrics_populates_both_backends(self, rag: MetricRAG):
        docs = [
            _make_metric_doc("sales.revenue", name="revenue"),
            _make_metric_doc("sales.profit", name="profit",
                             formula="SUM(amount) - SUM(cost)"),
            _make_metric_doc("marketing.cac", domain="marketing", name="cac",
                             description="Customer acquisition cost"),
        ]
        count = await rag.index_metrics(docs)
        assert count == 3

        stats = rag.stats()
        assert stats["lancedb_count"] == 3
        assert stats["bm25_count"] == 3

    @pytest.mark.asyncio
    async def test_index_empty_list(self, rag: MetricRAG):
        count = await rag.index_metrics([])
        assert count == 0

    @pytest.mark.asyncio
    async def test_index_single_doc(self, rag: MetricRAG):
        doc = _make_metric_doc("sales.revenue", name="revenue")
        count = await rag.index_metric(doc)
        assert count == 1

    @pytest.mark.asyncio
    async def test_index_overwrites_duplicate(self, rag: MetricRAG):
        doc1 = _make_metric_doc("dup.id", name="revenue", description="old")
        doc2 = _make_metric_doc("dup.id", name="profit", description="new")
        await rag.index_metrics([doc1])
        await rag.index_metrics([doc2])
        stats = rag.stats()
        assert stats["lancedb_count"] == 1
        assert stats["bm25_count"] == 1

    # ── Search ─────────────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_match_metrics_returns_results(self, rag: MetricRAG):
        docs = [
            _make_metric_doc(
                "sales.revenue", name="revenue", name_zh="营收",
                description="Total revenue from all orders",
                embedding_text="revenue total revenue from orders",
            ),
            _make_metric_doc(
                "sales.profit", name="profit", name_zh="利润",
                description="Net profit after costs",
                embedding_text="profit net profit after costs",
            ),
            _make_metric_doc(
                "marketing.cac", domain="marketing", name="cac",
                description="Customer acquisition cost",
                embedding_text="cac customer acquisition cost",
            ),
        ]
        await rag.index_metrics(docs)

        results = await rag.match_metrics("revenue from orders", top_k=3)
        assert len(results) >= 1
        assert results[0].name == "revenue"
        assert results[0].doc_id == "sales.revenue"

    @pytest.mark.asyncio
    async def test_match_metrics_respects_top_k(self, rag: MetricRAG):
        docs = [
            _make_metric_doc(f"sales.m{i}", name=f"metric_{i}",
                             embedding_text=f"metric number {i}")
            for i in range(10)
        ]
        await rag.index_metrics(docs)
        results = await rag.match_metrics("metric number 1", top_k=3)
        assert len(results) == 3

    @pytest.mark.asyncio
    async def test_match_metrics_empty_index(self, rag: MetricRAG):
        results = await rag.match_metrics("anything")
        assert results == []

    @pytest.mark.asyncio
    async def test_match_metrics_chinese(self, rag: MetricRAG):
        docs = [
            _make_metric_doc(
                "sales.revenue", name="revenue", name_zh="营业收入",
                description="主营业务收入",
                embedding_text="营业收入 主营业务收入 total revenue",
            ),
            _make_metric_doc(
                "sales.gross_margin", name="gross_margin", name_zh="毛利率",
                description="毛利润率",
                embedding_text="毛利率 毛利润率 gross margin",
            ),
        ]
        await rag.index_metrics(docs, language="zh")
        results = await rag.match_metrics("营业收入", top_k=3, language="zh")
        assert len(results) >= 1
        assert results[0].doc_id == "sales.revenue"

    @pytest.mark.asyncio
    async def test_match_metrics_with_domain_filter(self, rag: MetricRAG):
        docs = [
            _make_metric_doc("sales.revenue", domain="sales", name="revenue",
                             embedding_text="total revenue"),
            _make_metric_doc("marketing.cac", domain="marketing", name="cac",
                             embedding_text="customer acquisition cost"),
        ]
        await rag.index_metrics(docs)

        results = await rag.match_metrics("cost revenue", top_k=10, domain="sales")
        for r in results:
            assert r.domain == "sales"

    @pytest.mark.asyncio
    async def test_match_metrics_has_rrf_metadata(self, rag: MetricRAG):
        docs = [
            _make_metric_doc(f"sales.m{i}", name=f"metric_{i}",
                             embedding_text=f"metric {i}")
            for i in range(5)
        ]
        await rag.index_metrics(docs)
        results = await rag.match_metrics("metric 0", top_k=3)
        for r in results:
            assert r.rrf_score > 0
            assert isinstance(r.dense_rank, int) or r.dense_rank is None
            assert isinstance(r.sparse_rank, int) or r.sparse_rank is None

    @pytest.mark.asyncio
    async def test_match_metrics_preserves_dimensions(self, rag: MetricRAG):
        doc = _make_metric_doc(
            "sales.revenue", name="revenue",
            dimensions=["date", "region", "product"],
            embedding_text="revenue by product region date",
        )
        await rag.index_metrics([doc])
        results = await rag.match_metrics("revenue by product", top_k=3)
        assert len(results) >= 1
        assert "date" in results[0].dimensions
        assert "region" in results[0].dimensions
        assert "product" in results[0].dimensions

    @pytest.mark.asyncio
    async def test_match_metrics_chinese_dimensions(self, rag: MetricRAG):
        doc = _make_metric_doc(
            "sales.revenue", name="revenue", name_zh="营收",
            dimensions=["日期", "地区", "产品"],
            embedding_text="营收按产品地区日期",
        )
        await rag.index_metrics([doc], language="zh")
        results = await rag.match_metrics("营收", top_k=3, language="zh")
        assert len(results) >= 1
        assert "日期" in results[0].dimensions

    # ── Management ─────────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_clear_removes_all(self, rag: MetricRAG):
        docs = [
            _make_metric_doc(f"sales.m{i}", name=f"metric_{i}",
                             embedding_text=f"metric {i}")
            for i in range(3)
        ]
        await rag.index_metrics(docs)
        assert rag.stats()["lancedb_count"] == 3

        rag.clear()
        stats = rag.stats()
        assert stats["lancedb_count"] == 0
        assert stats["bm25_count"] == 0

    @pytest.mark.asyncio
    async def test_clear_then_reindex(self, rag: MetricRAG):
        await rag.index_metrics([
            _make_metric_doc("sales.revenue", name="revenue",
                             embedding_text="total revenue")
        ])
        rag.clear()
        await rag.index_metrics([
            _make_metric_doc("sales.profit", name="profit",
                             embedding_text="net profit")
        ])
        results = await rag.match_metrics("profit", top_k=3)
        assert len(results) >= 1
        assert results[0].name == "profit"

    # ── Convenience methods ────────────────────────────────────────────

    def test_get_metric_names(self, rag: MetricRAG):
        results = [
            MetricSearchResult(doc_id="d1", name="revenue", rrf_score=0.02),
            MetricSearchResult(doc_id="d2", name="revenue", rrf_score=0.015),
            MetricSearchResult(doc_id="d3", name="profit", rrf_score=0.01),
        ]
        names = rag.get_metric_names(results)
        assert names == ["revenue", "profit"]

    def test_get_metric_names_empty(self, rag: MetricRAG):
        assert rag.get_metric_names([]) == []

    def test_suggest_dimensions(self, rag: MetricRAG):
        results = [
            MetricSearchResult(doc_id="d1", name="revenue",
                               dimensions=["date", "region"], rrf_score=0.02),
            MetricSearchResult(doc_id="d2", name="revenue",
                               dimensions=["date", "product"], rrf_score=0.015),
            MetricSearchResult(doc_id="d3", name="profit",
                               dimensions=["date", "channel"], rrf_score=0.01),
        ]
        dims = rag.suggest_dimensions(results)
        assert dims == ["channel", "date", "product", "region"]

    def test_suggest_dimensions_with_name_filter(self, rag: MetricRAG):
        results = [
            MetricSearchResult(doc_id="d1", name="revenue",
                               dimensions=["date", "region"], rrf_score=0.02),
            MetricSearchResult(doc_id="d2", name="profit",
                               dimensions=["date", "channel"], rrf_score=0.01),
        ]
        dims = rag.suggest_dimensions(results, metric_name="revenue")
        assert dims == ["date", "region"]

    def test_suggest_dimensions_empty(self, rag: MetricRAG):
        assert rag.suggest_dimensions([]) == []

    @pytest.mark.asyncio
    async def test_end_to_end_match_and_suggest_dims(self, rag: MetricRAG):
        """Full integration: index → match → suggest dimensions."""
        docs = [
            _make_metric_doc(
                "sales.revenue", name="revenue",
                dimensions=["date", "region", "product"],
                embedding_text="revenue by region product date",
            ),
            _make_metric_doc(
                "sales.profit", name="profit",
                dimensions=["date", "channel"],
                embedding_text="profit by channel date",
            ),
        ]
        await rag.index_metrics(docs)
        results = await rag.match_metrics("revenue", top_k=5)
        dims = rag.suggest_dimensions(results)
        assert "date" in dims
        assert "region" in dims
        assert "product" in dims

    @pytest.mark.asyncio
    async def test_match_metrics_preserves_formula(self, rag: MetricRAG):
        doc = _make_metric_doc(
            "sales.revenue", name="revenue",
            formula="SUM(amount) - SUM(refund)",
            embedding_text="revenue sum amount minus refund",
        )
        await rag.index_metrics([doc])
        results = await rag.match_metrics("revenue", top_k=3)
        assert len(results) >= 1
        assert results[0].formula == "SUM(amount) - SUM(refund)"
        assert results[0].has_formula is True

    @pytest.mark.asyncio
    async def test_match_metrics_preserves_sql_template(self, rag: MetricRAG):
        doc = _make_metric_doc(
            "sales.revenue", name="revenue",
            sql_template="SELECT SUM(amount) FROM orders WHERE created_at >= {start}",
            embedding_text="revenue sql template",
        )
        await rag.index_metrics([doc])
        results = await rag.match_metrics("revenue", top_k=3)
        assert len(results) >= 1
        assert results[0].has_sql_template is True


class TestMetricRAGAlpha:
    """Tests for different alpha (dense/sparse) weightings."""

    @pytest.mark.asyncio
    async def test_dense_only_alpha(self, tmp_path):
        """alpha=1.0 should give dense-only ordering."""
        rag = _make_rag(tmp_path, alpha=1.0)
        docs = [
            _make_metric_doc(f"sales.m{i}", name=f"metric_{i}",
                             embedding_text="revenue profit growth kpi")
            for i in range(3)
        ]
        await rag.index_metrics(docs)
        results = await rag.match_metrics("revenue profit", top_k=3)
        assert len(results) >= 1

    @pytest.mark.asyncio
    async def test_sparse_only_alpha(self, tmp_path):
        """alpha=0.0 should give sparse-only ordering."""
        rag = _make_rag(tmp_path, alpha=0.0)
        docs = [
            _make_metric_doc(f"sales.m{i}", name=f"metric_{i}",
                             embedding_text="revenue profit growth kpi")
            for i in range(3)
        ]
        await rag.index_metrics(docs)
        results = await rag.match_metrics("revenue profit", top_k=3)
        assert len(results) >= 1
