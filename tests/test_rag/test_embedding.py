"""Tests for embedding providers and EmbeddingGenerator.

Covers MockEmbeddingProvider (deterministic), EmbeddingGenerator facade,
and validates the abstract EmbeddingProvider contract.
"""

from __future__ import annotations

import math
import sys

import pytest

from app.knowledge.retrieval.embedding import (
    MOCK_EMBEDDING_DIM,
    BGEEmbeddingProvider,
    EmbeddingGenerator,
    EmbeddingResult,
    MockEmbeddingProvider,
)

# ── MockEmbeddingProvider ──────────────────────────────────────────────


class TestMockEmbeddingProvider:
    """Tests for the deterministic mock embedding provider."""

    @pytest.fixture
    def provider(self) -> MockEmbeddingProvider:
        return MockEmbeddingProvider()

    @pytest.mark.asyncio
    async def test_embed_single_text(self, provider: MockEmbeddingProvider):
        result = await provider.embed(["hello"])
        assert isinstance(result, EmbeddingResult)
        assert len(result.vectors) == 1
        assert len(result.vectors[0]) == MOCK_EMBEDDING_DIM
        assert result.model == "mock-deterministic"
        assert result.dimension == MOCK_EMBEDDING_DIM
        assert result.tokens_used == 0

    @pytest.mark.asyncio
    async def test_embed_multiple_texts(self, provider: MockEmbeddingProvider):
        texts = ["hello", "world", "foo", "bar"]
        result = await provider.embed(texts)
        assert len(result.vectors) == 4
        for vec in result.vectors:
            assert len(vec) == MOCK_EMBEDDING_DIM

    @pytest.mark.asyncio
    async def test_embed_deterministic(self, provider: MockEmbeddingProvider):
        """Same text should produce the same vector."""
        r1 = await provider.embed(["hello"])
        r2 = await provider.embed(["hello"])
        assert r1.vectors[0] == r2.vectors[0]

    @pytest.mark.asyncio
    async def test_embed_different_texts_different_vectors(
        self, provider: MockEmbeddingProvider
    ):
        """Different texts should produce different vectors."""
        r = await provider.embed(["apple", "banana"])
        assert r.vectors[0] != r.vectors[1]

    @pytest.mark.asyncio
    async def test_embed_vectors_are_unit_length(self, provider: MockEmbeddingProvider):
        """Output vectors should be L2-normalised (unit length)."""
        result = await provider.embed(["hello", "world", "test"])
        for vec in result.vectors:
            norm = math.sqrt(sum(v * v for v in vec))
            assert math.isclose(norm, 1.0, rel_tol=1e-6)

    @pytest.mark.asyncio
    async def test_embed_empty_list_raises(self, provider: MockEmbeddingProvider):
        with pytest.raises(ValueError, match="texts must be non-empty"):
            await provider.embed([])

    @pytest.mark.asyncio
    async def test_dimension_property(self, provider: MockEmbeddingProvider):
        assert provider.dimension == MOCK_EMBEDDING_DIM

    @pytest.mark.asyncio
    async def test_model_name_property(self, provider: MockEmbeddingProvider):
        assert provider.model_name == "mock-deterministic"

    @pytest.mark.asyncio
    async def test_custom_dimension(self):
        provider = MockEmbeddingProvider(dimension=128)
        result = await provider.embed(["test"])
        assert len(result.vectors[0]) == 128
        assert provider.dimension == 128

    @pytest.mark.asyncio
    async def test_large_batch(self, provider: MockEmbeddingProvider):
        """Should handle 500 texts without issue."""
        texts = [f"text_{i}" for i in range(500)]
        result = await provider.embed(texts)
        assert len(result.vectors) == 500


# ── EmbeddingGenerator ─────────────────────────────────────────────────


class TestEmbeddingGenerator:
    """Tests for the EmbeddingGenerator facade."""

    @pytest.fixture
    def gen(self) -> EmbeddingGenerator:
        return EmbeddingGenerator(provider=MockEmbeddingProvider())

    @pytest.mark.asyncio
    async def test_embed_delegates(self, gen: EmbeddingGenerator):
        result = await gen.embed(["hello", "world"])
        assert len(result.vectors) == 2

    @pytest.mark.asyncio
    async def test_embed_single(self, gen: EmbeddingGenerator):
        vec = await gen.embed_single("hello")
        assert isinstance(vec, list)
        assert len(vec) == MOCK_EMBEDDING_DIM
        # Should be unit length
        norm = math.sqrt(sum(v * v for v in vec))
        assert math.isclose(norm, 1.0, rel_tol=1e-6)

    @pytest.mark.asyncio
    async def test_batch_embed(self, gen: EmbeddingGenerator):
        texts = [f"item_{i}" for i in range(250)]
        result = await gen.batch_embed(texts, batch_size=50)
        assert len(result.vectors) == 250
        for vec in result.vectors:
            assert len(vec) == MOCK_EMBEDDING_DIM

    @pytest.mark.asyncio
    async def test_batch_embed_preserves_order(self, gen: EmbeddingGenerator):
        texts = ["alpha", "beta", "gamma", "delta", "epsilon"]
        # Use batch_size=2 to force chunking
        result = await gen.batch_embed(texts, batch_size=2)
        # Deterministic check: same text → same vector whether in batch or not
        single_results = await gen.embed(texts)
        assert result.vectors == single_results.vectors

    @pytest.mark.asyncio
    async def test_dimension_property(self, gen: EmbeddingGenerator):
        assert gen.dimension == MOCK_EMBEDDING_DIM


# ── BGE local provider ─────────────────────────────────────────────────


class TestBGEEmbeddingProvider:
    """Tests for the local BGE provider (lazy-loaded optional dependency).

    The real model is never downloaded here: import failure is simulated by
    poisoning ``sys.modules`` so the lazy import raises ImportError.
    """

    @pytest.fixture
    def provider(self) -> BGEEmbeddingProvider:
        return BGEEmbeddingProvider()

    def test_dimension(self, provider: BGEEmbeddingProvider):
        assert provider.dimension == 1024

    def test_model_name_default(self, provider: BGEEmbeddingProvider):
        assert provider.model_name == "BAAI/bge-large-zh-v1.5"

    def test_custom_model_name(self):
        provider = BGEEmbeddingProvider(model_name="custom/bge-model")
        assert provider.model_name == "custom/bge-model"

    def test_init_is_lazy_no_dependency_needed(self, monkeypatch):
        """Constructing the provider must not import sentence-transformers."""
        monkeypatch.setitem(sys.modules, "sentence_transformers", None)
        provider = BGEEmbeddingProvider()
        assert provider._model is None

    @pytest.mark.asyncio
    async def test_embed_without_dependency_raises_runtime_error(self, monkeypatch):
        """Missing sentence-transformers → RuntimeError with install guidance."""
        monkeypatch.setitem(sys.modules, "sentence_transformers", None)
        provider = BGEEmbeddingProvider()
        with pytest.raises(RuntimeError, match="pip install sentence-transformers"):
            await provider.embed(["hello"])

    @pytest.mark.asyncio
    async def test_embed_empty_list_raises_value_error(
        self, provider: BGEEmbeddingProvider
    ):
        with pytest.raises(ValueError, match="texts must be non-empty"):
            await provider.embed([])
