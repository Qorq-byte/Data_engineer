"""Tests for SchemaMetadataRAG — hybrid schema search (LanceDB + BM25 + RRF).

Integration tests that exercise the full indexing → search pipeline.
"""

from __future__ import annotations

import pytest

from app.knowledge.retrieval.bm25_index import BM25Index
from app.knowledge.retrieval.embedding import EmbeddingGenerator, MockEmbeddingProvider
from app.knowledge.retrieval.lancedb_store import LanceDBStore
from app.knowledge.retrieval.rrf_fusion import RRFFusion
from app.knowledge.retrieval.schema_rag import (
    SchemaMetadataRAG,
    SchemaSearchResult,
    _extract_index_meta,
    _row_to_schema_result,
)
from app.models.rag_schema import SchemaDocument

# ── Helpers ────────────────────────────────────────────────────────────

DIM = 128  # small dimension for fast tests


def _make_schema_doc(
    doc_id: str,
    db_id: str = "test_db",
    table_name: str = "users",
    column_name: str | None = "id",
    data_type: str = "INTEGER",
    comment: str = "test column",
    is_pk: bool = False,
    is_fk: bool = False,
    embedding_text: str = "",
) -> SchemaDocument:
    return SchemaDocument(
        doc_id=doc_id,
        db_id=db_id,
        schema_name="public",
        table_name=table_name,
        column_name=column_name,
        data_type=data_type,
        comment=comment,
        is_primary_key=is_pk,
        is_foreign_key=is_fk,
        embedding_text=embedding_text or f"{table_name}.{column_name} — {comment}",
    )


def _make_rag(
    tmp_path, alpha: float = 0.5, k: int = 60
) -> SchemaMetadataRAG:
    """Build a SchemaMetadataRAG with file-backed LanceDB + in-memory BM25."""
    lancedb_uri = str(tmp_path / "lancedb_schema_rag")
    return SchemaMetadataRAG(
        lancedb_store=LanceDBStore(dimension=DIM, uri=lancedb_uri),
        bm25_index=BM25Index(":memory:"),
        embedding_generator=EmbeddingGenerator(provider=MockEmbeddingProvider(dimension=DIM)),
        rrf_fusion=RRFFusion(k=k, alpha=alpha),
    )


# ── Helper function tests ──────────────────────────────────────────────


class TestHelpers:
    def test_extract_index_meta(self):
        doc = _make_schema_doc("db.public.orders.id", table_name="orders", column_name="id")
        meta = _extract_index_meta(doc)
        assert meta["db_id"] == "test_db"
        assert meta["table_name"] == "orders"
        assert meta["column_name"] == "id"
        assert meta["is_primary_key"] is False
        assert meta["is_foreign_key"] is False

    def test_row_to_schema_result(self):
        row = {
            "doc_id": "db.public.orders.price",
            "db_id": "test_db",
            "table_name": "orders",
            "column_name": "price",
            "data_type": "DECIMAL",
            "comment": "unit price",
            "is_primary_key": False,
            "is_foreign_key": False,
            "embedding_text": "orders.price column",
            "rrf_score": 0.015,
            "dense_rank": 1,
            "sparse_rank": 2,
        }
        sr = _row_to_schema_result(row)
        assert sr.doc_id == "db.public.orders.price"
        assert sr.table_name == "orders"
        assert sr.column_name == "price"
        assert sr.rrf_score == 0.015
        assert sr.dense_rank == 1
        assert sr.sparse_rank == 2
        assert sr.is_table_level is False

    def test_row_to_schema_result_table_level(self):
        row = {
            "doc_id": "db.public.orders",
            "db_id": "test_db",
            "table_name": "orders",
            "column_name": None,
            "data_type": None,
            "comment": "order table",
            "is_primary_key": False,
            "is_foreign_key": False,
            "embedding_text": "orders table",
            "rrf_score": 0.02,
            "dense_rank": 1,
            "sparse_rank": 1,
        }
        sr = _row_to_schema_result(row)
        assert sr.is_table_level is True
        assert sr.column_name is None


# ── Integration tests ──────────────────────────────────────────────────


class TestSchemaMetadataRAG:
    """Integration tests for the full index → search pipeline."""

    @pytest.fixture
    def rag(self, tmp_path) -> SchemaMetadataRAG:
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

    def test_namespace_is_schema_metadata(self, rag: SchemaMetadataRAG):
        assert rag.NAMESPACE == "schema_metadata"

    # ── Indexing ───────────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_index_schemas_populates_both_backends(self, rag: SchemaMetadataRAG):
        docs = [
            _make_schema_doc("db.public.orders.id", table_name="orders", column_name="id"),
            _make_schema_doc("db.public.orders.price", table_name="orders", column_name="price"),
            _make_schema_doc("db.public.users.id", table_name="users", column_name="id"),
        ]
        count = await rag.index_schemas(docs)
        assert count == 3

        stats = rag.stats()
        assert stats["lancedb_count"] == 3
        assert stats["bm25_count"] == 3

    @pytest.mark.asyncio
    async def test_index_empty_list(self, rag: SchemaMetadataRAG):
        count = await rag.index_schemas([])
        assert count == 0

    @pytest.mark.asyncio
    async def test_index_single_doc(self, rag: SchemaMetadataRAG):
        doc = _make_schema_doc("db.public.orders.id", table_name="orders", column_name="id")
        count = await rag.index_schema(doc)
        assert count == 1

    @pytest.mark.asyncio
    async def test_index_overwrites_duplicate(self, rag: SchemaMetadataRAG):
        doc1 = _make_schema_doc("dup.id", table_name="t1", column_name="c1")
        doc2 = _make_schema_doc("dup.id", table_name="t2", column_name="c2")
        await rag.index_schemas([doc1])
        await rag.index_schemas([doc2])
        stats = rag.stats()
        assert stats["lancedb_count"] == 1
        assert stats["bm25_count"] == 1

    # ── Search ─────────────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_find_relevant_tables_returns_results(self, rag: SchemaMetadataRAG):
        docs = [
            _make_schema_doc(
                "db.public.orders.price", table_name="orders", column_name="price",
                comment="unit price in cents", embedding_text="orders.price unit price",
            ),
            _make_schema_doc(
                "db.public.users.name", table_name="users", column_name="name",
                comment="user full name", embedding_text="users.name full name",
            ),
            _make_schema_doc(
                "db.public.orders.created_at", table_name="orders", column_name="created_at",
                comment="order creation timestamp", embedding_text="orders.created_at timestamp",
            ),
        ]
        await rag.index_schemas(docs)

        results = await rag.find_relevant_tables("price of orders", top_k=3)
        assert len(results) >= 1
        # "orders.price" should be top result
        assert results[0].doc_id == "db.public.orders.price"
        assert results[0].table_name == "orders"

    @pytest.mark.asyncio
    async def test_find_relevant_tables_respects_top_k(self, rag: SchemaMetadataRAG):
        docs = [
            _make_schema_doc(f"db.public.users.c{i}", table_name="users", column_name=f"c{i}",
                             embedding_text=f"column {i}")
            for i in range(10)
        ]
        await rag.index_schemas(docs)
        results = await rag.find_relevant_tables("column 1", top_k=3)
        assert len(results) == 3

    @pytest.mark.asyncio
    async def test_find_relevant_tables_empty_index(self, rag: SchemaMetadataRAG):
        results = await rag.find_relevant_tables("anything")
        assert results == []

    @pytest.mark.asyncio
    async def test_find_relevant_tables_chinese(self, rag: SchemaMetadataRAG):
        docs = [
            _make_schema_doc(
                "db.public.orders.amount", table_name="orders", column_name="amount",
                comment="订单金额", embedding_text="订单金额 amount",
            ),
            _make_schema_doc(
                "db.public.users.name", table_name="users", column_name="name",
                comment="用户姓名", embedding_text="用户姓名 name",
            ),
        ]
        await rag.index_schemas(docs, language="zh")
        results = await rag.find_relevant_tables("订单金额", top_k=3, language="zh")
        assert len(results) >= 1
        assert results[0].doc_id == "db.public.orders.amount"

    @pytest.mark.asyncio
    async def test_find_relevant_tables_with_db_filter(self, rag: SchemaMetadataRAG):
        docs = [
            _make_schema_doc("db_a.public.t.c1", db_id="db_a", table_name="t", column_name="c1",
                             embedding_text="column one"),
            _make_schema_doc("db_b.public.t.c2", db_id="db_b", table_name="t", column_name="c2",
                             embedding_text="column two"),
        ]
        await rag.index_schemas(docs)

        results = await rag.find_relevant_tables("column", top_k=10, db_id="db_a")
        for r in results:
            assert r.db_id == "db_a"

    @pytest.mark.asyncio
    async def test_find_relevant_tables_has_rrf_metadata(self, rag: SchemaMetadataRAG):
        docs = [_make_schema_doc(f"db.public.t.c{i}", table_name="t", column_name=f"c{i}",
                                 embedding_text=f"col {i}") for i in range(5)]
        await rag.index_schemas(docs)
        results = await rag.find_relevant_tables("col 0", top_k=3)
        for r in results:
            assert r.rrf_score > 0
            assert isinstance(r.dense_rank, int) or r.dense_rank is None
            assert isinstance(r.sparse_rank, int) or r.sparse_rank is None

    # ── Management ─────────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_clear_removes_all(self, rag: SchemaMetadataRAG):
        docs = [_make_schema_doc(f"db.public.t.c{i}", table_name="t", column_name=f"c{i}",
                                 embedding_text=f"col {i}") for i in range(3)]
        await rag.index_schemas(docs)
        assert rag.stats()["lancedb_count"] == 3

        rag.clear()
        stats = rag.stats()
        assert stats["lancedb_count"] == 0
        assert stats["bm25_count"] == 0

    @pytest.mark.asyncio
    async def test_clear_then_reindex(self, rag: SchemaMetadataRAG):
        await rag.index_schemas([_make_schema_doc("db.public.t.c1", table_name="t",
                                                    column_name="c1", embedding_text="col 1")])
        rag.clear()
        await rag.index_schemas([_make_schema_doc("db.public.t.c2", table_name="t",
                                                    column_name="c2", embedding_text="col 2")])
        results = await rag.find_relevant_tables("col 2", top_k=3)
        assert len(results) >= 1
        assert results[0].doc_id == "db.public.t.c2"

    # ── Convenience methods ────────────────────────────────────────────

    def test_get_distinct_tables(self, rag: SchemaMetadataRAG):
        results = [
            SchemaSearchResult(doc_id="d1", db_id="db", table_name="orders", column_name="id",
                               rrf_score=0.02),
            SchemaSearchResult(doc_id="d2", db_id="db", table_name="orders", column_name="price",
                               rrf_score=0.015),
            SchemaSearchResult(doc_id="d3", db_id="db", table_name="users", column_name="id",
                               rrf_score=0.01),
        ]
        tables = rag.get_distinct_tables(results)
        assert tables == ["orders", "users"]

    def test_get_distinct_tables_empty(self, rag: SchemaMetadataRAG):
        assert rag.get_distinct_tables([]) == []

    def test_get_columns_for_table(self, rag: SchemaMetadataRAG):
        results = [
            SchemaSearchResult(doc_id="d1", db_id="db", table_name="orders", column_name="id",
                               rrf_score=0.02),
            SchemaSearchResult(doc_id="d2", db_id="db", table_name="orders", column_name="price",
                               rrf_score=0.015),
            SchemaSearchResult(doc_id="d3", db_id="db", table_name="users", column_name="id",
                               rrf_score=0.01),
        ]
        cols = rag.get_columns_for_table(results, "orders")
        assert len(cols) == 2
        assert {c.column_name for c in cols} == {"id", "price"}

    def test_get_columns_for_table_excludes_table_level(self, rag: SchemaMetadataRAG):
        results = [
            SchemaSearchResult(doc_id="d1", db_id="db", table_name="orders", column_name="id",
                               rrf_score=0.02),
            SchemaSearchResult(doc_id="d2", db_id="db", table_name="orders", column_name=None,
                               rrf_score=0.015),
        ]
        cols = rag.get_columns_for_table(results, "orders")
        assert len(cols) == 1
        assert cols[0].column_name == "id"

    @pytest.mark.asyncio
    async def test_table_level_docs_are_searchable(self, rag: SchemaMetadataRAG):
        """Table-level SchemaDocuments (column_name=None) should be indexed and found."""
        docs = [
            _make_schema_doc("db.public.orders", table_name="orders", column_name=None,
                             data_type=None, comment="the main orders table",
                             embedding_text="orders table containing all orders"),
        ]
        await rag.index_schemas(docs)
        results = await rag.find_relevant_tables("orders table", top_k=3)
        assert len(results) >= 1
        assert results[0].is_table_level is True


class TestSchemaMetadataRAGAlpha:
    """Tests for different alpha (dense/sparse) weightings."""

    @pytest.mark.asyncio
    async def test_dense_only_alpha(self, tmp_path):
        """alpha=1.0 should give dense-only ordering."""
        rag = _make_rag(tmp_path, alpha=1.0)
        docs = [
            _make_schema_doc(f"d{i}", table_name="t", column_name=f"c{i}",
                             embedding_text="sales revenue monthly report")
            for i in range(3)
        ]
        await rag.index_schemas(docs)
        results = await rag.find_relevant_tables("sales revenue", top_k=3)
        assert len(results) >= 1

    @pytest.mark.asyncio
    async def test_sparse_only_alpha(self, tmp_path):
        """alpha=0.0 should give sparse-only ordering."""
        rag = _make_rag(tmp_path, alpha=0.0)
        docs = [
            _make_schema_doc(f"d{i}", table_name="t", column_name=f"c{i}",
                             embedding_text="sales revenue monthly report")
            for i in range(3)
        ]
        await rag.index_schemas(docs)
        results = await rag.find_relevant_tables("sales revenue", top_k=3)
        assert len(results) >= 1
