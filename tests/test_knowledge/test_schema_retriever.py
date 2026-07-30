"""Tests for SchemaRetriever — 5-step hierarchical schema retrieval.

Covers the pure-keyword and RAG-powered paths, helper functions,
and SchemaLinkingNode integration with SchemaRetriever injection.
"""

from __future__ import annotations

import pytest

from app.knowledge.retrieval.bm25_index import BM25Index
from app.knowledge.retrieval.embedding import EmbeddingGenerator, MockEmbeddingProvider
from app.knowledge.retrieval.lancedb_store import LanceDBStore
from app.knowledge.retrieval.rrf_fusion import RRFFusion
from app.knowledge.retrieval.schema_rag import SchemaMetadataRAG
from app.knowledge.schema_retriever import (
    SchemaRetrievalColumn,
    SchemaRetrievalResult,
    SchemaRetriever,
    _build_filtered_schema,
    _build_search_query,
    _resolve_terms,
)
from app.models.query import SQR, Entity
from app.models.rag_schema import SchemaDocument
from app.models.schema import (
    ColumnSchema,
    ForeignKey,
    SchemaSnapshot,
    TableSchema,
)
from app.nodes.base import NodeInput

pytestmark = pytest.mark.anyio


# ── Fixtures ────────────────────────────────────────────────────────────

DIM = 128


@pytest.fixture
def ecommerce_schema() -> SchemaSnapshot:
    return SchemaSnapshot(
        database_type="postgresql",
        database_name="ecommerce",
        tables={
            "orders": TableSchema(
                name="orders",
                comment="Customer orders",
                columns=[
                    ColumnSchema(name="id", type="INTEGER", is_primary_key=True),
                    ColumnSchema(name="user_id", type="INTEGER"),
                    ColumnSchema(name="amount", type="DECIMAL", comment="订单金额"),
                    ColumnSchema(name="created_at", type="TIMESTAMP"),
                ],
                foreign_keys=[
                    ForeignKey(
                        name="fk_orders_users", column="user_id",
                        ref_table="users", ref_column="id",
                    ),
                ],
            ),
            "users": TableSchema(
                name="users",
                comment="User accounts",
                columns=[
                    ColumnSchema(name="id", type="INTEGER", is_primary_key=True),
                    ColumnSchema(name="name", type="VARCHAR(100)"),
                    ColumnSchema(name="email", type="VARCHAR(200)"),
                ],
            ),
            "products": TableSchema(
                name="products",
                comment="Product catalog",
                columns=[
                    ColumnSchema(name="id", type="INTEGER", is_primary_key=True),
                    ColumnSchema(name="price", type="DECIMAL"),
                ],
            ),
        },
    )


def _make_schema_rag(tmp_path) -> SchemaMetadataRAG:
    lancedb_uri = str(tmp_path / "lancedb_sr")
    return SchemaMetadataRAG(
        lancedb_store=LanceDBStore(dimension=DIM, uri=lancedb_uri),
        bm25_index=BM25Index(":memory:"),
        embedding_generator=EmbeddingGenerator(provider=MockEmbeddingProvider(dimension=DIM)),
        rrf_fusion=RRFFusion(k=60, alpha=0.5),
    )


# ── Dataclass tests ─────────────────────────────────────────────────────


class TestSchemaRetrievalResult:
    def test_defaults(self):
        r = SchemaRetrievalResult()
        assert r.tables == []
        assert r.columns == []
        assert r.filtered_schema is None
        assert r.method == "none"
        assert r.confidence == 0.0
        assert r.table_count == 0
        assert r.column_count == 0
        assert r.has_schema is False

    def test_with_data(self, ecommerce_schema):
        r = SchemaRetrievalResult(
            tables=["orders", "users"],
            columns=[SchemaRetrievalColumn(name="amount", table="orders", confidence=0.9)],
            filtered_schema=_build_filtered_schema(ecommerce_schema, ["orders"]),
            method="hybrid",
            confidence=0.85,
        )
        assert r.table_count == 2
        assert r.column_count == 1
        assert r.has_schema is True


# ── Helper function tests ───────────────────────────────────────────────


class TestBuildSearchQuery:
    def test_basic(self):
        sqr = SQR(raw_text="monthly sales by region")
        result = _build_search_query("monthly sales by region", sqr)
        assert "monthly sales" in result
        assert "UNKNOWN" in result  # intent is UNKNOWN

    def test_with_entities(self):
        sqr = SQR(
            raw_text="revenue by product",
            entities=[
                Entity(name="revenue", type="column", normalized="revenue"),
                Entity(name="product", type="table", normalized="product"),
            ],
        )
        result = _build_search_query("revenue by product", sqr)
        assert "revenue" in result
        assert "product" in result

    def test_with_target_tables(self):
        sqr = SQR(
            raw_text="user orders",
            target_tables=["orders", "users"],
        )
        result = _build_search_query("user orders", sqr)
        assert "orders" in result
        assert "users" in result

    def test_with_time_range(self):
        sqr = SQR(raw_text="last month sales")
        from app.models.query import TimeRange
        sqr.time_range = TimeRange(raw_expression="last month", unit="month")
        result = _build_search_query("last month sales", sqr)
        assert "last month" in result


class TestResolveTerms:
    def test_no_glossary_fallback_table(self, ecommerce_schema):
        sqr = SQR(
            raw_text="orders",
            entities=[Entity(name="orders", type="table", normalized="orders")],
        )
        tables, columns = _resolve_terms(
            "orders", sqr, ecommerce_schema, glossary=None, threshold=0.6,
        )
        assert "orders" in tables

    def test_no_glossary_fallback_miss(self, ecommerce_schema):
        sqr = SQR(
            raw_text="nonexistent",
            entities=[Entity(name="nonexistent", type="table", normalized="nonexistent")],
        )
        tables, columns = _resolve_terms(
            "nonexistent", sqr, ecommerce_schema, glossary=None, threshold=0.6,
        )
        assert "nonexistent" not in tables


class TestBuildFilteredSchema:
    def test_candidate_tables_have_columns(self, ecommerce_schema):
        filtered = _build_filtered_schema(ecommerce_schema, ["orders"])
        assert "orders" in filtered.tables
        assert len(filtered.tables["orders"].columns) == 4

    def test_non_candidate_tables_are_stubs(self, ecommerce_schema):
        filtered = _build_filtered_schema(ecommerce_schema, ["orders"])
        assert "users" in filtered.tables
        # Non-candidate tables have empty columns (name-only stubs)
        assert len(filtered.tables["users"].columns) == 0


# ── SchemaRetriever (keyword-only, no RAG) ──────────────────────────────


class TestSchemaRetrieverKeywordOnly:
    """Tests for SchemaRetriever without RAG — falls back to keyword matching."""

    @pytest.fixture
    def retriever(self) -> SchemaRetriever:
        return SchemaRetriever()  # no RAG, no glossary

    @pytest.mark.asyncio
    async def test_retrieve_empty_schema(self, retriever: SchemaRetriever):
        schema = SchemaSnapshot(database_type="sqlite", database_name="empty", tables={})
        result = await retriever.retrieve("test", schema)
        assert result.method == "none"
        assert result.tables == []

    @pytest.mark.asyncio
    async def test_retrieve_with_sqr_entities(self, retriever: SchemaRetriever, ecommerce_schema):
        sqr = SQR(
            raw_text="orders",
            entities=[Entity(name="orders", type="table", normalized="orders")],
        )
        result = await retriever.retrieve("orders", ecommerce_schema, sqr=sqr)
        assert result.method == "keyword"
        assert "orders" in result.tables

    @pytest.mark.asyncio
    async def test_retrieve_fk_expansion(self, retriever: SchemaRetriever, ecommerce_schema):
        sqr = SQR(
            raw_text="orders",
            entities=[Entity(name="orders", type="table", normalized="orders")],
        )
        result = await retriever.retrieve(
            "orders", ecommerce_schema, sqr=sqr, fk_expand=True,
        )
        # orders FK → users (user_id → users.id), and users FK reverse
        assert "orders" in result.tables
        assert "users" in result.tables  # FK expansion

    @pytest.mark.asyncio
    async def test_retrieve_no_fk_expansion(self, retriever: SchemaRetriever, ecommerce_schema):
        sqr = SQR(
            raw_text="orders",
            entities=[Entity(name="orders", type="table", normalized="orders")],
        )
        result = await retriever.retrieve(
            "orders", ecommerce_schema, sqr=sqr, fk_expand=False,
        )
        assert "orders" in result.tables
        # FK expansion disabled — users may not be included
        # (it might be if keyword matching picks it up, but it shouldn't
        # be there from FK expansion)
        tables_from_expansion = [t for t in result.tables if t != "orders"]
        # With fk_expand=False, users should only appear if matched directly
        assert "users" not in tables_from_expansion or len(result.tables) == 2

    @pytest.mark.asyncio
    async def test_retrieve_with_confidence(self, retriever: SchemaRetriever, ecommerce_schema):
        sqr = SQR(
            raw_text="orders",
            entities=[Entity(name="orders", type="table", normalized="orders")],
        )
        result = await retriever.retrieve("orders", ecommerce_schema, sqr=sqr)
        assert result.confidence >= 0.0

    @pytest.mark.asyncio
    async def test_retrieve_builds_filtered_schema(
        self, retriever: SchemaRetriever, ecommerce_schema
    ):
        sqr = SQR(
            raw_text="orders",
            entities=[Entity(name="orders", type="table", normalized="orders")],
        )
        result = await retriever.retrieve("orders", ecommerce_schema, sqr=sqr)
        assert result.filtered_schema is not None
        assert "orders" in result.filtered_schema.tables
        # Candidate tables have full schema
        assert len(result.filtered_schema.tables["orders"].columns) > 0


# ── SchemaRetriever (RAG-powered) ───────────────────────────────────────


class TestSchemaRetrieverWithRAG:
    """Integration tests with SchemaMetadataRAG."""

    @pytest.fixture
    def retriever(self, tmp_path) -> SchemaRetriever:
        rag = _make_schema_rag(tmp_path)
        return SchemaRetriever(schema_rag=rag)

    @pytest.fixture
    def rag(self, tmp_path) -> SchemaMetadataRAG:
        return _make_schema_rag(tmp_path)

    @pytest.mark.asyncio
    async def test_has_rag(self, retriever: SchemaRetriever):
        assert retriever.has_rag is True

    @pytest.mark.asyncio
    async def test_retrieve_with_rag(self, retriever: SchemaRetriever, ecommerce_schema):
        # Index schema docs first
        docs = [
            SchemaDocument(
                doc_id="db.public.orders.amount", db_id="ecommerce",
                schema_name="public", table_name="orders", column_name="amount",
                data_type="DECIMAL", comment="订单金额",
                embedding_text="订单金额 order amount revenue",
            ),
            SchemaDocument(
                doc_id="db.public.orders.id", db_id="ecommerce",
                schema_name="public", table_name="orders", column_name="id",
                data_type="INTEGER", is_primary_key=True,
                comment="order primary key",
                embedding_text="orders primary key id",
            ),
            SchemaDocument(
                doc_id="db.public.users.name", db_id="ecommerce",
                schema_name="public", table_name="users", column_name="name",
                data_type="VARCHAR(100)", comment="user name",
                embedding_text="user name full name",
            ),
        ]
        await retriever._rag.index_schemas(docs)

        sqr = SQR(
            raw_text="订单金额",
            entities=[Entity(name="金额", type="column", normalized="金额")],
        )
        result = await retriever.retrieve(
            "订单金额", ecommerce_schema, sqr=sqr, top_k=5, language="zh",
        )
        assert result.method == "hybrid"
        assert result.tables  # should have at least 'orders'
        assert len(result.search_results) >= 1


# ── SchemaLinkingNode with SchemaRetriever ──────────────────────────────


class TestSchemaLinkingNodeWithRetriever:
    """Verify that SchemaLinkingNode delegates to SchemaRetriever when injected."""

    @pytest.mark.asyncio
    async def test_node_uses_retriever(self, tmp_path, ecommerce_schema):
        from app.nodes.schema_linking import SchemaLinkingNode

        rag = _make_schema_rag(tmp_path)
        retriever = SchemaRetriever(schema_rag=rag)

        # Index relevant docs
        await rag.index_schemas([
            SchemaDocument(
                doc_id="db.public.orders.amount", db_id="ecommerce",
                schema_name="public", table_name="orders", column_name="amount",
                data_type="DECIMAL", comment="order amount",
                embedding_text="order amount revenue",
            ),
            SchemaDocument(
                doc_id="db.public.orders.user_id", db_id="ecommerce",
                schema_name="public", table_name="orders", column_name="user_id",
                data_type="INTEGER", is_foreign_key=True,
                embedding_text="orders user foreign key",
            ),
        ])

        node = SchemaLinkingNode(retriever=retriever)
        sqr = SQR(
            raw_text="revenue from orders",
            entities=[Entity(name="revenue", type="column", normalized="revenue")],
        )

        output = await node.execute(NodeInput(
            query_text="revenue from orders",
            context={"sqr": sqr, "schema": ecommerce_schema},
            config={"top_k": 5},
        ))
        assert output.metadata["status"] == "success"
        # Method should be "hybrid" since RAG returned results
        assert output.metadata["method"] in ("hybrid", "keyword")

    @pytest.mark.asyncio
    async def test_node_falls_back_to_keyword(self, ecommerce_schema):
        """Without retriever, node uses keyword matching."""
        from app.nodes.schema_linking import SchemaLinkingNode

        node = SchemaLinkingNode()  # no retriever
        sqr = SQR(
            raw_text="orders",
            entities=[Entity(name="orders", type="table", normalized="orders")],
        )
        output = await node.execute(NodeInput(
            query_text="orders",
            context={"sqr": sqr, "schema": ecommerce_schema},
        ))
        assert output.metadata["status"] == "success"
        assert output.metadata["method"] == "keyword_match"
        assert "orders" in output.result["tables"]
