"""Tests for HybridSearchNode — RAG hybrid search wrapped as a Harness Node.

Covers all target modes, error handling, registry integration, and
update_context convenience key extraction.
"""

from __future__ import annotations

import pytest

from app.knowledge.retrieval.bm25_index import BM25Index
from app.knowledge.retrieval.document_store import DocumentStore
from app.knowledge.retrieval.embedding import EmbeddingGenerator, MockEmbeddingProvider
from app.knowledge.retrieval.lancedb_store import LanceDBStore
from app.knowledge.retrieval.metric_rag import MetricRAG
from app.knowledge.retrieval.rrf_fusion import RRFFusion
from app.knowledge.retrieval.schema_rag import SchemaMetadataRAG
from app.models.rag_document import Document
from app.models.rag_metric import MetricDocument
from app.models.rag_schema import SchemaDocument
from app.nodes.base import NodeInput
from app.nodes.hybrid_search import HybridSearchNode, HybridSearchOutput

# ── Helpers ────────────────────────────────────────────────────────────

DIM = 128


def _make_schema_rag(tmp_path) -> SchemaMetadataRAG:
    lancedb_uri = str(tmp_path / "lancedb_hs_schema")
    return SchemaMetadataRAG(
        lancedb_store=LanceDBStore(dimension=DIM, uri=lancedb_uri),
        bm25_index=BM25Index(":memory:"),
        embedding_generator=EmbeddingGenerator(provider=MockEmbeddingProvider(dimension=DIM)),
        rrf_fusion=RRFFusion(k=60, alpha=0.5),
    )


def _make_metric_rag(tmp_path) -> MetricRAG:
    lancedb_uri = str(tmp_path / "lancedb_hs_metric")
    return MetricRAG(
        lancedb_store=LanceDBStore(dimension=DIM, uri=lancedb_uri),
        bm25_index=BM25Index(":memory:"),
        embedding_generator=EmbeddingGenerator(provider=MockEmbeddingProvider(dimension=DIM)),
        rrf_fusion=RRFFusion(k=60, alpha=0.5),
    )


def _make_doc_store(tmp_path) -> DocumentStore:
    lancedb_uri = str(tmp_path / "lancedb_hs_doc")
    return DocumentStore(
        lancedb_store=LanceDBStore(dimension=DIM, uri=lancedb_uri),
        bm25_index=BM25Index(":memory:"),
        embedding_generator=EmbeddingGenerator(provider=MockEmbeddingProvider(dimension=DIM)),
        rrf_fusion=RRFFusion(k=60, alpha=0.5),
    )


def _make_schema_doc(doc_id: str, table_name: str = "orders",
                     column_name: str | None = "id",
                     embedding_text: str = "") -> SchemaDocument:
    return SchemaDocument(
        doc_id=doc_id, db_id="test_db", schema_name="public",
        table_name=table_name, column_name=column_name,
        data_type="INTEGER", comment="test",
        embedding_text=embedding_text or f"{table_name}.{column_name}",
    )


def _make_metric_doc(doc_id: str, name: str = "revenue",
                     embedding_text: str = "") -> MetricDocument:
    return MetricDocument(
        doc_id=doc_id, domain="sales", name=name,
        description="test metric",
        embedding_text=embedding_text or name,
    )


def _make_document(doc_id: str, title: str = "User Guide",
                   content: str = "Sample content.") -> Document:
    return Document(
        doc_id=doc_id, title=title, content=content,
        embedding_text=f"{title} — {content[:60]}",
    )


# ── HybridSearchOutput tests ──────────────────────────────────────────


class TestHybridSearchOutput:
    def test_defaults(self):
        out = HybridSearchOutput()
        assert out.query == ""
        assert out.target == "all"
        assert out.schema_results == []
        assert out.metric_results == []
        assert out.document_results == []
        assert out.total_hits == 0
        assert out.best_table is None
        assert out.best_metric is None

    def test_best_table(self):
        from app.knowledge.retrieval.schema_rag import SchemaSearchResult
        out = HybridSearchOutput(
            schema_results=[
                SchemaSearchResult(doc_id="d1", db_id="db", table_name="orders",
                                   column_name="id", rrf_score=0.02),
                SchemaSearchResult(doc_id="d2", db_id="db", table_name="users",
                                   column_name="name", rrf_score=0.01),
            ]
        )
        assert out.best_table == "orders"

    def test_best_metric(self):
        from app.knowledge.retrieval.metric_rag import MetricSearchResult
        out = HybridSearchOutput(
            metric_results=[
                MetricSearchResult(doc_id="m1", name="revenue", rrf_score=0.02),
                MetricSearchResult(doc_id="m2", name="profit", rrf_score=0.01),
            ]
        )
        assert out.best_metric == "revenue"

    def test_total_hits(self):
        from app.knowledge.retrieval.schema_rag import SchemaSearchResult
        out = HybridSearchOutput(
            schema_results=[
                SchemaSearchResult(doc_id="d1", db_id="db", table_name="t",
                                   rrf_score=0.02),
            ],
            metric_results=[],
            document_results=[],
        )
        assert out.total_hits == 0  # not computed unless set explicitly
        out.total_hits = 3
        assert out.total_hits == 3


# ── Construction tests ─────────────────────────────────────────────────


class TestHybridSearchNodeConstruction:
    def test_default_construction(self):
        node = HybridSearchNode()
        assert node.name == "hybrid_search"
        assert node.has_schema is False
        assert node.has_metrics is False
        assert node.has_documents is False

    def test_with_all_backends(self, tmp_path):
        node = HybridSearchNode(
            schema_rag=_make_schema_rag(tmp_path),
            metric_rag=_make_metric_rag(tmp_path),
            document_store=_make_doc_store(tmp_path),
        )
        assert node.has_schema is True
        assert node.has_metrics is True
        assert node.has_documents is True

    def test_with_schema_only(self, tmp_path):
        node = HybridSearchNode(schema_rag=_make_schema_rag(tmp_path))
        assert node.has_schema is True
        assert node.has_metrics is False
        assert node.has_documents is False


# ── Registry tests ─────────────────────────────────────────────────────


class TestHybridSearchNodeRegistry:
    """NodeRegistry uses a class-level ``_nodes`` dict — we must clean
    up between tests to avoid cross-test pollution."""

    @pytest.fixture(autouse=True)
    def _clear_registry(self):
        from app.nodes.registry import NodeRegistry
        NodeRegistry._nodes.clear()
        yield
        NodeRegistry._nodes.clear()

    def test_can_register(self):
        from app.nodes.registry import NodeRegistry
        registry = NodeRegistry()
        node = HybridSearchNode()
        registry.register(node)
        assert "hybrid_search" in registry
        assert registry.get("hybrid_search") is node

    def test_duplicate_raises(self):
        from app.nodes.registry import NodeRegistry
        registry = NodeRegistry()
        registry.register(HybridSearchNode())
        with pytest.raises(ValueError, match="already registered"):
            registry.register(HybridSearchNode())


# ── Integration tests ──────────────────────────────────────────────────


class TestHybridSearchNode:
    """Integration tests exercising the full index → search → output pipeline."""

    @pytest.fixture
    def node_with_all(self, tmp_path) -> HybridSearchNode:
        return HybridSearchNode(
            schema_rag=_make_schema_rag(tmp_path),
            metric_rag=_make_metric_rag(tmp_path),
            document_store=_make_doc_store(tmp_path),
        )

    # ── Error handling ─────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_empty_query(self, node_with_all: HybridSearchNode):
        output = await node_with_all.execute(NodeInput(query_text=""))
        assert output.errors == ["Empty query"]
        assert output.metadata["status"] == "empty_query"

    @pytest.mark.asyncio
    async def test_whitespace_query(self, node_with_all: HybridSearchNode):
        output = await node_with_all.execute(NodeInput(query_text="   "))
        assert output.errors == ["Empty query"]

    @pytest.mark.asyncio
    async def test_unknown_target(self, node_with_all: HybridSearchNode):
        output = await node_with_all.execute(NodeInput(
            query_text="revenue", config={"target": "unknown_target"},
        ))
        assert "Unknown target" in output.errors[0]
        assert output.metadata["status"] == "unknown_target"

    # ── Schema target ──────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_schema_target(self, node_with_all: HybridSearchNode):
        # Index some schema docs first
        docs = [
            _make_schema_doc("db.public.orders.id", table_name="orders",
                             column_name="id", embedding_text="orders.id primary key"),
            _make_schema_doc("db.public.orders.amount", table_name="orders",
                             column_name="amount", embedding_text="orders.amount revenue"),
        ]
        await node_with_all._schema_rag.index_schemas(docs)

        output = await node_with_all.execute(NodeInput(
            query_text="revenue amount", config={"target": "schema", "top_k": 3},
        ))
        assert output.metadata["status"] == "success"
        result: HybridSearchOutput = output.result
        assert result.total_hits >= 1
        assert len(result.schema_results) >= 1
        assert result.metric_results == []
        assert result.document_results == []

    @pytest.mark.asyncio
    async def test_schema_target_with_filter(self, node_with_all: HybridSearchNode):
        docs = [
            _make_schema_doc("db_a.public.t.c1", table_name="t", column_name="c1",
                             embedding_text="column one"),
        ]
        docs[0].db_id = "db_a"
        docs.append(
            _make_schema_doc("db_b.public.t.c2", table_name="t", column_name="c2",
                             embedding_text="column two"),
        )
        docs[1].db_id = "db_b"
        await node_with_all._schema_rag.index_schemas(docs)

        output = await node_with_all.execute(NodeInput(
            query_text="column", config={"target": "schema", "top_k": 10, "db_id": "db_a"},
        ))
        result: HybridSearchOutput = output.result
        for r in result.schema_results:
            assert r.db_id == "db_a"

    # ── Metrics target ─────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_metrics_target(self, node_with_all: HybridSearchNode):
        docs = [
            _make_metric_doc("sales.revenue", name="revenue",
                             embedding_text="total revenue from orders"),
            _make_metric_doc("sales.profit", name="profit",
                             embedding_text="net profit after costs"),
        ]
        await node_with_all._metric_rag.index_metrics(docs)

        output = await node_with_all.execute(NodeInput(
            query_text="revenue", config={"target": "metrics", "top_k": 3},
        ))
        assert output.metadata["status"] == "success"
        result: HybridSearchOutput = output.result
        assert len(result.metric_results) >= 1
        assert result.schema_results == []
        assert result.document_results == []

    @pytest.mark.asyncio
    async def test_metrics_target_with_domain(self, node_with_all: HybridSearchNode):
        docs = [
            _make_metric_doc("sales.revenue", name="revenue",
                             embedding_text="revenue"),
        ]
        docs[0].domain = "sales"
        docs.append(
            _make_metric_doc("marketing.cac", name="cac", embedding_text="cac"),
        )
        docs[1].domain = "marketing"
        await node_with_all._metric_rag.index_metrics(docs)

        output = await node_with_all.execute(NodeInput(
            query_text="revenue cac",
            config={"target": "metrics", "top_k": 10, "domain": "sales"},
        ))
        result: HybridSearchOutput = output.result
        for r in result.metric_results:
            assert r.domain == "sales"

    # ── Documents target ───────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_documents_target(self, node_with_all: HybridSearchNode):
        docs = [
            _make_document("guide.sql", title="SQL Guide",
                           content="Use SELECT for queries. Use JOIN to combine tables."),
            _make_document("guide.py", title="Python Guide",
                           content="Use list comprehensions for iteration."),
        ]
        await node_with_all._doc_store.index_documents(docs)

        output = await node_with_all.execute(NodeInput(
            query_text="JOIN tables", config={"target": "documents", "top_k": 3},
        ))
        assert output.metadata["status"] == "success"
        result: HybridSearchOutput = output.result
        assert len(result.document_results) >= 1
        assert result.schema_results == []
        assert result.metric_results == []

    @pytest.mark.asyncio
    async def test_documents_target_with_content_type(self, node_with_all: HybridSearchNode):
        docs = [
            _make_document("faq.join", title="JOIN FAQ",
                           content="How to use JOIN in SQL?"),
        ]
        docs[0].content_type = "faq"
        docs.append(
            _make_document("best.join", title="JOIN Best Practice",
                           content="Always specify JOIN conditions."),
        )
        docs[1].content_type = "best_practice"
        await node_with_all._doc_store.index_documents(docs)

        output = await node_with_all.execute(NodeInput(
            query_text="JOIN",
            config={"target": "documents", "top_k": 10, "content_type": "faq"},
        ))
        result: HybridSearchOutput = output.result
        for r in result.document_results:
            assert r.content_type == "faq"

    # ── All targets ────────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_all_targets(self, node_with_all: HybridSearchNode):
        # Index into all three
        await node_with_all._schema_rag.index_schemas([
            _make_schema_doc("db.public.orders.amount", table_name="orders",
                             column_name="amount", embedding_text="revenue amount"),
        ])
        await node_with_all._metric_rag.index_metrics([
            _make_metric_doc("sales.revenue", name="revenue",
                             embedding_text="revenue from sales"),
        ])
        await node_with_all._doc_store.index_documents([
            _make_document("guide.revenue", title="Revenue Guide",
                           content="How to query revenue. Use the orders table."),
        ])

        output = await node_with_all.execute(NodeInput(
            query_text="revenue", config={"target": "all", "top_k": 3},
        ))
        assert output.metadata["status"] == "success"
        result: HybridSearchOutput = output.result
        assert result.total_hits >= 3  # at least one from each
        assert result.schema_results
        assert result.metric_results
        assert result.document_results

    # ── Missing backend ────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_missing_backend_skips_gracefully(self):
        """Node without any backends should return empty results, not crash."""
        node = HybridSearchNode()
        output = await node.execute(NodeInput(
            query_text="revenue", config={"target": "all", "top_k": 3},
        ))
        result: HybridSearchOutput = output.result
        assert result.total_hits == 0
        assert result.schema_results == []
        assert result.metric_results == []
        assert result.document_results == []

    @pytest.mark.asyncio
    async def test_missing_backend_reports_error(self, tmp_path):
        """Targeting a backend that is None should not crash."""
        node = HybridSearchNode(schema_rag=_make_schema_rag(tmp_path))  # no metric, no doc
        output = await node.execute(NodeInput(
            query_text="revenue", config={"target": "metrics", "top_k": 3},
        ))
        # Should just return empty metric results (no crash, no error)
        result: HybridSearchOutput = output.result
        assert result.metric_results == []

    # ── update_context ─────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_update_context_sets_convenience_keys(self, node_with_all: HybridSearchNode):
        # Index schema + metrics + docs
        await node_with_all._schema_rag.index_schemas([
            _make_schema_doc("db.public.orders.amount", table_name="orders",
                             column_name="amount", embedding_text="revenue orders amount"),
        ])
        await node_with_all._metric_rag.index_metrics([
            _make_metric_doc("sales.revenue", name="revenue",
                             embedding_text="revenue"),
        ])
        await node_with_all._doc_store.index_documents([
            _make_document("guide.sql", title="SQL Guide",
                           content="How to write queries."),
        ])

        output = await node_with_all.execute(NodeInput(
            query_text="revenue", config={"target": "all", "top_k": 3},
        ))
        ctx = await node_with_all.update_context(output, {})

        assert "hybrid_search_results" in ctx
        assert "relevant_tables" in ctx
        assert "orders" in ctx["relevant_tables"]
        assert "relevant_metrics" in ctx
        assert "revenue" in ctx["relevant_metrics"]
        assert "relevant_docs" in ctx
        assert "SQL Guide" in ctx["relevant_docs"]

    @pytest.mark.asyncio
    async def test_update_context_no_results(self, node_with_all: HybridSearchNode):
        output = await node_with_all.execute(NodeInput(
            query_text="xyz_nonexistent", config={"target": "all", "top_k": 3},
        ))
        ctx = await node_with_all.update_context(output, {"existing": "keep"})
        assert ctx["existing"] == "keep"  # preserves existing keys
        assert "hybrid_search_results" in ctx

    @pytest.mark.asyncio
    async def test_update_context_no_schema_results(self, node_with_all: HybridSearchNode):
        """When only metrics have results, relevant_tables should not be set."""
        await node_with_all._metric_rag.index_metrics([
            _make_metric_doc("sales.revenue", name="revenue",
                             embedding_text="revenue"),
        ])
        output = await node_with_all.execute(NodeInput(
            query_text="revenue", config={"target": "metrics", "top_k": 3},
        ))
        ctx = await node_with_all.update_context(output, {})
        assert "relevant_metrics" in ctx
        assert "relevant_tables" not in ctx
        assert "relevant_docs" not in ctx

    # ── Edge cases ─────────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_metadata_reflects_per_backend_hits(self, node_with_all: HybridSearchNode):
        await node_with_all._schema_rag.index_schemas([
            _make_schema_doc(f"db.public.t.c{i}", table_name="t", column_name=f"c{i}",
                             embedding_text=f"col {i}")
            for i in range(5)
        ])
        output = await node_with_all.execute(NodeInput(
            query_text="col", config={"target": "schema", "top_k": 3},
        ))
        assert output.metadata["schema_hits"] == 3
        assert output.metadata["metric_hits"] == 0
        assert output.metadata["document_hits"] == 0

    @pytest.mark.asyncio
    async def test_default_target_is_all(self, node_with_all: HybridSearchNode):
        """When no target is specified in config, default to 'all'."""
        await node_with_all._schema_rag.index_schemas([
            _make_schema_doc("db.public.orders.amount", table_name="orders",
                             column_name="amount", embedding_text="revenue"),
        ])
        output = await node_with_all.execute(NodeInput(
            query_text="revenue", config={"top_k": 3},
        ))
        assert output.metadata["target"] == "all"
        result: HybridSearchOutput = output.result
        assert result.schema_results

    @pytest.mark.asyncio
    async def test_language_passed_to_backends(self, node_with_all: HybridSearchNode):
        await node_with_all._schema_rag.index_schemas([
            _make_schema_doc("db.public.orders.amount", table_name="orders",
                             column_name="amount", embedding_text="订单金额 revenue"),
        ], language="zh")
        output = await node_with_all.execute(NodeInput(
            query_text="订单金额",
            config={"target": "schema", "top_k": 3, "language": "zh"},
        ))
        result: HybridSearchOutput = output.result
        assert len(result.schema_results) >= 1
