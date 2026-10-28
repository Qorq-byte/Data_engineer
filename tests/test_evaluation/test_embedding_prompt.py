"""Synthetic vectors verify the real schema-to-prompt path; no semantic-quality claim."""

import json
import os

import pytest

from app.evaluation.embedding_probe import (
    EXPECTED_LIFECYCLE,
    ORDER_COLUMN_MARKER,
    QUERY_TEXT,
    UNRELATED_COLUMN,
    USER_COLUMN_MARKER,
    schema_prompt_probe,
)
from app.knowledge.retrieval.bm25_index import BM25Index
from app.knowledge.retrieval.embedding import EmbeddingGenerator, MockEmbeddingProvider
from app.knowledge.retrieval.lancedb_store import LanceDBStore
from app.knowledge.retrieval.schema_rag import SchemaMetadataRAG
from app.models.rag_schema import SchemaDocument


class RecordedSyntheticProvider(MockEmbeddingProvider):
    def __init__(self):
        super().__init__(dimension=8)
        self.inputs = []

    async def embed(self, texts):
        self.inputs.extend(texts)
        return await super().embed(texts)


@pytest.fixture
async def probe_rag(tmp_path):
    dense = LanceDBStore(8, str(tmp_path / "lance"), embedding_id="synthetic-probe-only")
    sparse = BM25Index(":memory:")
    provider = RecordedSyntheticProvider()
    rag = SchemaMetadataRAG(dense, sparse, EmbeddingGenerator(provider))
    try:
        await rag.index_schemas([
            SchemaDocument("a.join", "shop_a", schema_name="main", table_name="orders",
                           embedding_text=QUERY_TEXT),
            SchemaDocument("b.join", "shop_b", schema_name="main", table_name="orders",
                           embedding_text=QUERY_TEXT),
        ])
        yield rag, provider
    finally:
        sparse.close()
        dense.close()


async def test_actual_rag_context_enters_generator_via_fixed_harness(probe_rag, tmp_path,
                                                                   monkeypatch):
    rag, provider = probe_rag
    external = tmp_path / "external-traces-must-stay-empty"
    monkeypatch.setenv("DE_TRACE_DIR", str(external))
    prior = dict(os.environ)
    run_dir = tmp_path / "run"
    report = await schema_prompt_probe(rag, QUERY_TEXT, run_dir)
    assert dict(os.environ) == prior
    assert not external.exists()
    assert provider.inputs == [QUERY_TEXT, QUERY_TEXT, QUERY_TEXT]
    assert report["status"] == "passed"
    assert report["trace_order"] == ["schema_linking", "generate_sql"]
    assert report["node_lifecycle"] == EXPECTED_LIFECYCLE
    assert report["link_method"] == "hybrid" and report["link_warnings"] == []
    assert report["source_namespace"] == "main"
    assert report["retrieved_doc_ids"] == ["a.join"]
    assert report["retrieved_hit_evidence"] == [{"doc_id": "a.join", "db_id": "shop_a",
                                                 "schema_name": "main", "table_name": "orders"}]
    assert {"orders", "users"} <= set(report["linked_tables"])
    assert report["linked_columns"]["unrelated"] == []
    assert report["deterministic_router_calls"] == report["harness_llm_calls_reserved"] == 1
    assert report["real_llm_calls"] == 0 and report["actual_cost_usd"] is None
    prompt = json.loads((run_dir / report["artifacts"]["prompt"]).read_text(encoding="utf-8"))
    text = "\n".join(message["content"] for message in prompt)
    assert ORDER_COLUMN_MARKER in text and USER_COLUMN_MARKER in text
    assert UNRELATED_COLUMN not in text
    trace = json.loads((run_dir / report["artifacts"]["trace"]).read_text(encoding="utf-8"))
    assert trace["status"] == "completed" and len(trace["nodes"]) == 2
    assert [node["node_name"] for node in trace["nodes"]] == report["trace_order"]


async def test_probe_unknown_query_never_requests_an_extra_embedding(probe_rag, tmp_path):
    rag, provider = probe_rag
    before = list(provider.inputs)
    with pytest.raises(ValueError, match="frozen"):
        await schema_prompt_probe(rag, "additional query not in manifest", tmp_path / "run")
    assert provider.inputs == before
    assert not (tmp_path / "run").exists()


async def test_probe_cannot_reuse_previous_artifacts(probe_rag, tmp_path):
    rag, provider = probe_rag
    run_dir = tmp_path / "run"
    await schema_prompt_probe(rag, QUERY_TEXT, run_dir)
    before = list(provider.inputs)
    trace = (run_dir / "schema-prompt/workflow-trace.json").read_bytes()
    with pytest.raises(FileExistsError):
        await schema_prompt_probe(rag, QUERY_TEXT, run_dir)
    assert provider.inputs == before
    assert (run_dir / "schema-prompt/workflow-trace.json").read_bytes() == trace


@pytest.mark.parametrize("defect", ["empty", "wrong_namespace", "unrelated_table"])
async def test_probe_refuses_retrieval_fallback_or_unrelated_hits(defect, tmp_path):
    dense = LanceDBStore(8, str(tmp_path / "lance"), embedding_id="synthetic-probe-only")
    sparse = BM25Index(":memory:")
    provider = RecordedSyntheticProvider()
    rag = SchemaMetadataRAG(dense, sparse, EmbeddingGenerator(provider))
    try:
        if defect != "empty":
            await rag.index_schemas([SchemaDocument(
                "wrong", "shop_a", schema_name="other" if defect == "wrong_namespace" else "main",
                table_name="unrelated" if defect == "unrelated_table" else "orders",
                embedding_text=QUERY_TEXT,
            )])
        with pytest.raises(RuntimeError, match="proof failed"):
            await schema_prompt_probe(rag, QUERY_TEXT, tmp_path / "run")
        failed = json.loads((tmp_path / "run/schema-prompt/workflow-trace.json")
                            .read_text(encoding="utf-8"))
        assert failed["status"] == "failed"
    finally:
        sparse.close()
        dense.close()
