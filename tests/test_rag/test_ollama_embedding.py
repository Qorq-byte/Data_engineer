"""Tests for OllamaEmbeddingProvider — hermetic via httpx.MockTransport.

No real Ollama server is contacted: the HTTP boundary is faked while the
provider's request construction, normalisation and error handling run for real.
"""

from __future__ import annotations

import json

import httpx
import pytest

from app.knowledge.retrieval.embedding import OllamaEmbeddingProvider

DIM = 4  # small dimension for fast tests


def _fake_ollama(vectors: dict[str, list[float]], status: int = 200):
    """Build a provider wired to a MockTransport returning *vectors* per prompt."""

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode("utf-8"))
        vec = vectors.get(body["prompt"])
        if vec is None:
            return httpx.Response(404, json={"error": f"model not found: {body['model']}"})
        return httpx.Response(status, json={"embedding": vec})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = OllamaEmbeddingProvider(
        model="embeddinggemma",
        base_url="http://ollama.test",
        dimension=DIM,
        client=client,
    )
    return provider


async def test_embed_batch_dimensions():
    provider = _fake_ollama({"a": [1.0] * DIM, "b": [2.0] * DIM})
    result = await provider.embed(["a", "b"])
    assert result.dimension == DIM
    assert result.model == "ollama:embeddinggemma"
    assert len(result.vectors) == 2
    assert all(len(v) == DIM for v in result.vectors)


async def test_vectors_are_l2_normalised():
    """Normalised vectors make LanceDB L2 distance equal cosine similarity."""
    provider = _fake_ollama({"x": [3.0, 4.0, 0.0, 0.0]})
    result = await provider.embed(["x"])
    vec = result.vectors[0]
    norm = sum(v * v for v in vec)
    assert abs(norm - 1.0) < 1e-6
    assert abs(vec[0] - 0.6) < 1e-6  # 3/5


async def test_request_uses_configured_model_and_prompt():
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content.decode("utf-8")))
        return httpx.Response(200, json={"embedding": [0.5] * DIM})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = OllamaEmbeddingProvider(
        model="custom-emb", base_url="http://ollama.test:9999", dimension=DIM, client=client
    )
    await provider.embed(["销售额最高的产品"])
    assert seen == [{"model": "custom-emb", "prompt": "销售额最高的产品"}]


async def test_connection_error_has_guidance():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = OllamaEmbeddingProvider(dimension=DIM, client=client)
    with pytest.raises(RuntimeError, match="Ollama 不可达"):
        await provider.embed(["hello"])


async def test_http_error_mentions_model_pull():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"error": "model 'embeddinggemma' not found"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = OllamaEmbeddingProvider(dimension=DIM, client=client)
    with pytest.raises(RuntimeError, match="embeddinggemma"):
        await provider.embed(["hello"])


async def test_missing_embedding_field_raises():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"something": "else"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = OllamaEmbeddingProvider(dimension=DIM, client=client)
    with pytest.raises(RuntimeError, match="未返回 embedding"):
        await provider.embed(["hello"])


async def test_empty_texts_raises():
    provider = _fake_ollama({})
    with pytest.raises(ValueError):
        await provider.embed([])


def test_env_defaults(monkeypatch):
    monkeypatch.setenv("OLLAMA_EMBEDDING_MODEL", "nomic-embed-text")
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://localhost:12345")
    monkeypatch.setenv("OLLAMA_EMBEDDING_DIM", "768")
    provider = OllamaEmbeddingProvider()
    assert provider.model_name == "ollama:nomic-embed-text"
    assert provider.dimension == 768
    assert provider._base_url == "http://localhost:12345"
