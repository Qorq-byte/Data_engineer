"""Real retrieval must preserve namespace provenance through schema linking.

The deterministic embedding provider checks wiring only, never semantic quality.
Both LanceDB and SQLite FTS5 remain real for this regression.
"""

import pytest

from app.knowledge.retrieval.bm25_index import BM25Index
from app.knowledge.retrieval.embedding import EmbeddingGenerator, MockEmbeddingProvider
from app.knowledge.retrieval.lancedb_store import LanceDBStore
from app.knowledge.retrieval.schema_rag import SchemaMetadataRAG, SchemaSearchResult
from app.knowledge.schema_retriever import SchemaRetriever
from app.models.query import SQR
from app.models.rag_schema import SchemaDocument
from app.models.schema import ColumnSchema, SchemaSnapshot, TableSchema
from app.nodes.base import NodeInput
from app.nodes.schema_linking import SchemaLinkingNode


@pytest.mark.parametrize("namespace", ["main", "public"])
async def test_real_rag_namespace_survives_retriever_and_linking_node(tmp_path, namespace):
    dense = LanceDBStore(dimension=16, uri=str(tmp_path / "vectors"))
    sparse = BM25Index(":memory:")
    query = "semantic revenue cue"  # No table/column keyword can hide a rejected RAG hit.
    rag = SchemaMetadataRAG(
        lancedb_store=dense,
        bm25_index=sparse,
        embedding_generator=EmbeddingGenerator(MockEmbeddingProvider(dimension=16)),
    )
    try:
        await rag.index_schemas([SchemaDocument(
            doc_id="fixture.orders.amount", db_id="fixture", schema_name=namespace,
            table_name="orders", column_name="amount", data_type="REAL",
            embedding_text=query,
        )])
        schema = SchemaSnapshot("sqlite", "synthetic", tables={
            "orders": TableSchema("orders", schema_name=namespace, columns=[
                ColumnSchema("id", "INTEGER"), ColumnSchema("amount", "REAL"),
            ]),
        })
        retriever = SchemaRetriever(rag)
        result = await retriever.retrieve(
            query, schema, sqr=SQR(raw_text=query), db_id="fixture", fk_expand=False,
        )
        assert result.method == "hybrid"
        assert result.warnings == []
        assert [(hit.doc_id, hit.schema_name) for hit in result.search_results] == [
            ("fixture.orders.amount", namespace),
        ]
        assert result.search_results[0].dense_rank == result.search_results[0].sparse_rank == 1
        assert [(column.table, column.name) for column in result.columns] == [
            ("orders", "amount"),
        ]

        output = await SchemaLinkingNode(retriever).execute(NodeInput(
            query, context={"sqr": SQR(raw_text=query), "schema": schema},
            config={"db_id": "fixture", "fk_expand": False},
        ))
        assert output.metadata["method"] == "hybrid"
        assert output.context["schema_linking_warnings"] == []
        assert output.context["linked_schema"].tables["orders"].schema_name == namespace
        assert output.context["linked_schema"].tables["orders"].column_names == ["id", "amount"]
    finally:
        sparse.close()
        dense.close()


def test_namespace_append_preserves_every_existing_positional_argument():
    raw = {"existing": "raw row"}
    result = SchemaSearchResult(
        "doc", "db", "orders", "amount", "REAL", "comment", True, False,
        "embedding", 0.5, 1, 2, raw,
    )
    assert result.raw is raw
    assert result.schema_name is None
    assert result.table_name == "orders" and result.column_name == "amount"
    assert result.rrf_score == 0.5 and result.dense_rank == 1 and result.sparse_rank == 2
