"""Real provider validation with a hermetic HTTP boundary, before semantic measurement."""

import json

import httpx
import pytest

from app.knowledge.retrieval.embedding import OllamaEmbeddingProvider


@pytest.mark.parametrize("vector", [[1.0, 2.0], [float("nan")] * 4, [0.0] * 4])
async def test_ollama_rejects_invalid_vectors_before_indexing(vector):
    async with httpx.AsyncClient(transport=httpx.MockTransport(
        lambda request: httpx.Response(200, content=json.dumps({"embedding": vector}))
    )) as client:
        provider = OllamaEmbeddingProvider(dimension=4, client=client)
        with pytest.raises(RuntimeError):
            await provider.embed(["synthetic fixture"])


def test_empty_sqr_keeps_embedding_query_inside_frozen_inputs():
    from app.knowledge.schema_retriever import _build_search_query
    from app.models.query import SQR

    query = "orders user_id users id 关联键"
    assert _build_search_query(query, SQR(raw_text=query)) == query
