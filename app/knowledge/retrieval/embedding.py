"""Embedding generator — text vectorisation for RAG semantic search.

See SPEC §4.2.4 and implementation-plan.md §4.8 for the full RAG architecture.

Supports two embedding backends:

- **OpenAI** ``text-embedding-3-small`` (1536-d, cloud, default)
- **BGE-large-zh** (1024-d, local, optional ``sentence-transformers`` dependency,
  lazy-loaded on first use)

Also includes a deterministic mock provider for testing (no API key needed).
"""

from __future__ import annotations

import asyncio
import hashlib
import math
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

# ── OpenAI embedding (optional — graceful degradation) ────────────────
try:
    from openai import AsyncOpenAI

    _OPENAI_AVAILABLE = True
except ImportError:
    AsyncOpenAI = None  # type: ignore[assignment]
    _OPENAI_AVAILABLE = False


# ── Embedding dimension constants ─────────────────────────────────────

OPENAI_EMBEDDING_DIM = 1536  # text-embedding-3-small
BGE_EMBEDDING_DIM = 1024  # BGE-large-zh
MOCK_EMBEDDING_DIM = 256  # deterministic mock (fast for tests)


# ── Embedding result wrapper ──────────────────────────────────────────


@dataclass
class EmbeddingResult:
    """Result of a batch embedding call.

    Attributes:
        vectors: List of embedding vectors, each a list of floats.
        model: Name of the model that produced the embeddings.
        dimension: Dimension of each vector.
        tokens_used: Approximate token count (0 if unknown).
    """

    vectors: list[list[float]]
    model: str
    dimension: int
    tokens_used: int = 0


# ── Abstract base ─────────────────────────────────────────────────────


class EmbeddingProvider(ABC):
    """Abstract embedding provider — implement to add a new backend."""

    @abstractmethod
    async def embed(self, texts: list[str]) -> EmbeddingResult:
        """Embed a batch of texts.

        Args:
            texts: Non-empty list of strings to embed.

        Returns:
            :class:`EmbeddingResult` with vectors in the same order as *texts*.
        """
        ...

    @property
    @abstractmethod
    def dimension(self) -> int:
        """Output dimension of the embedding vectors."""
        ...

    @property
    @abstractmethod
    def model_name(self) -> str:
        """Identifier of the underlying model."""
        ...


# ── OpenAI provider ───────────────────────────────────────────────────


class OpenAIEmbeddingProvider(EmbeddingProvider):
    """Embedding via OpenAI ``text-embedding-3-small``.

    Requires ``OPENAI_API_KEY`` in the environment (or passed explicitly).

    Batch size is capped at 2048 texts per API call; larger batches are
    automatically chunked.
    """

    MAX_BATCH_SIZE = 2048
    MIN_BATCH_SIZE = 1

    def __init__(
        self,
        model: str = "text-embedding-3-small",
        api_key: str | None = None,
        base_url: str | None = None,
    ):
        if not _OPENAI_AVAILABLE:
            raise ImportError(
                "OpenAI embedding requires the 'openai' package. "
                "Install it with: pip install openai"
            )
        self._model = model
        self._client = AsyncOpenAI(
            api_key=api_key or os.getenv("OPENAI_API_KEY"),
            base_url=base_url,
        )

    @property
    def dimension(self) -> int:
        return OPENAI_EMBEDDING_DIM

    @property
    def model_name(self) -> str:
        return self._model

    async def embed(self, texts: list[str]) -> EmbeddingResult:
        if not texts:
            raise ValueError("texts must be non-empty")

        all_vectors: list[list[float]] = []
        total_usage = 0

        # Chunk to respect API limits
        for i in range(0, len(texts), self.MAX_BATCH_SIZE):
            chunk = texts[i : i + self.MAX_BATCH_SIZE]
            response = await self._client.embeddings.create(
                model=self._model,
                input=chunk,
            )
            # Sort by index to preserve input order
            sorted_data = sorted(response.data, key=lambda d: d.index)
            all_vectors.extend([d.embedding for d in sorted_data])
            total_usage += getattr(response, "usage", {}).get("total_tokens", 0)

        return EmbeddingResult(
            vectors=all_vectors,
            model=self._model,
            dimension=self.dimension,
            tokens_used=total_usage,
        )


# ── BGE local provider (optional sentence-transformers dependency) ────


class BGEEmbeddingProvider(EmbeddingProvider):
    """Local BGE embedding via ``sentence-transformers``, lazy-loaded.

    Constructing the provider is cheap: the optional ``sentence-transformers``
    package is imported and the model is loaded only on the first
    :meth:`embed` call. Vectors are L2-normalised (BGE recommendation).

    Optional dependency::

        pip install sentence-transformers

    If the package is missing, :meth:`embed` raises ``RuntimeError`` with
    install guidance (graceful degradation instead of an import-time crash).
    """

    def __init__(self, model_name: str = "BAAI/bge-large-zh-v1.5"):
        self._model_name = model_name
        self._model: Any = None

    @property
    def dimension(self) -> int:
        return BGE_EMBEDDING_DIM

    @property
    def model_name(self) -> str:
        return self._model_name

    def _load_model(self) -> Any:
        """Import sentence-transformers and load the model on first use."""
        if self._model is None:
            try:
                from sentence_transformers import SentenceTransformer
            except ImportError as e:
                raise RuntimeError(
                    "sentence-transformers not installed. "
                    "Install with: pip install sentence-transformers"
                ) from e
            self._model = SentenceTransformer(self._model_name)
        return self._model

    def _encode(self, texts: list[str]) -> list[list[float]]:
        """Blocking encode — run in a worker thread via :meth:`embed`."""
        model = self._load_model()
        raw = model.encode(texts, normalize_embeddings=True)
        return [[float(v) for v in vec] for vec in raw]

    async def embed(self, texts: list[str]) -> EmbeddingResult:
        if not texts:
            raise ValueError("texts must be non-empty")
        # Model load + encode are CPU/IO-blocking → off the event loop
        vectors = await asyncio.to_thread(self._encode, texts)
        return EmbeddingResult(
            vectors=vectors,
            model=self._model_name,
            dimension=self.dimension,
            tokens_used=0,
        )


# ── Ollama local provider (local semantic search, no API key) ────────


class OllamaEmbeddingProvider(EmbeddingProvider):
    """Local embeddings via a running Ollama server (default ``embeddinggemma``).

    Calls ``POST {base_url}/api/embeddings`` per text with bounded
    concurrency. Requires ``ollama serve`` and the model pulled
    (``ollama pull embeddinggemma``). Vectors are L2-normalised so LanceDB's
    L2 distance is equivalent to cosine similarity.

    Configuration (environment variables):
        - ``OLLAMA_BASE_URL`` — server URL (default ``http://127.0.0.1:11434``)
        - ``OLLAMA_EMBEDDING_MODEL`` — model name (default ``embeddinggemma``)
        - ``OLLAMA_EMBEDDING_DIM`` — output dimension (default 768)
    """

    DEFAULT_MODEL = "embeddinggemma"
    DEFAULT_BASE_URL = "http://127.0.0.1:11434"
    DEFAULT_DIM = 768
    MAX_CONCURRENCY = 8

    def __init__(
        self,
        model: str | None = None,
        base_url: str | None = None,
        dimension: int | None = None,
        client: Any = None,
    ):
        self._model = model or os.getenv("OLLAMA_EMBEDDING_MODEL", self.DEFAULT_MODEL)
        self._base_url = (
            base_url or os.getenv("OLLAMA_BASE_URL", self.DEFAULT_BASE_URL)
        ).rstrip("/")
        raw_dim = dimension or int(os.getenv("OLLAMA_EMBEDDING_DIM", str(self.DEFAULT_DIM)))
        self._dim = raw_dim
        self._client = client  # injectable for tests
        self._semaphore = asyncio.Semaphore(self.MAX_CONCURRENCY)

    @property
    def dimension(self) -> int:
        return self._dim

    @property
    def model_name(self) -> str:
        return f"ollama:{self._model}"

    async def _get_client(self) -> Any:
        if self._client is None:
            import httpx

            self._client = httpx.AsyncClient(timeout=120.0)
        return self._client

    async def _embed_one(self, text: str) -> list[float]:
        import httpx

        client = await self._get_client()
        try:
            async with self._semaphore:
                resp = await client.post(
                    f"{self._base_url}/api/embeddings",
                    json={"model": self._model, "prompt": text},
                )
                resp.raise_for_status()
                data = resp.json()
        except httpx.ConnectError as e:
            raise RuntimeError(
                f"Ollama 不可达({self._base_url})——请确认已运行 `ollama serve` "
                f"且已拉取模型 `ollama pull {self._model}`"
            ) from e
        except httpx.HTTPStatusError as e:
            raise RuntimeError(
                f"Ollama 返回错误 {e.response.status_code}:{e.response.text[:200]} "
                f"(模型 '{self._model}' 是否已 `ollama pull`?)"
            ) from e

        vec = data.get("embedding")
        if not vec:
            raise RuntimeError(
                f"Ollama 未返回 embedding(模型 '{self._model}'),响应: {str(data)[:200]}"
            )
        return self._normalize([float(x) for x in vec])

    @staticmethod
    def _normalize(vec: list[float]) -> list[float]:
        norm = math.sqrt(sum(v * v for v in vec))
        if norm <= 0:
            return vec
        return [v / norm for v in vec]

    async def embed(self, texts: list[str]) -> EmbeddingResult:
        if not texts:
            raise ValueError("texts must be non-empty")
        vectors = await asyncio.gather(*(self._embed_one(t) for t in texts))
        return EmbeddingResult(
            vectors=vectors,
            model=self.model_name,
            dimension=self._dim,
            tokens_used=0,
        )


# ── Mock provider (testing, no external deps) ─────────────────────────


class MockEmbeddingProvider(EmbeddingProvider):
    """Deterministic mock embedding provider for testing.

    Produces pseudo-random but deterministic vectors based on
    SHA-256 hashes of the input text. No API key or network needed.
    """

    def __init__(self, dimension: int = MOCK_EMBEDDING_DIM, seed: int = 42):
        self._dim = dimension

    @property
    def dimension(self) -> int:
        return self._dim

    @property
    def model_name(self) -> str:
        return "mock-deterministic"

    async def embed(self, texts: list[str]) -> EmbeddingResult:
        if not texts:
            raise ValueError("texts must be non-empty")
        vectors = [self._hash_vector(t) for t in texts]
        return EmbeddingResult(
            vectors=vectors,
            model=self.model_name,
            dimension=self._dim,
            tokens_used=0,
        )

    def _hash_vector(self, text: str) -> list[float]:
        """Create a deterministic pseudo-random unit vector from text hash."""
        # Use SHA-256 to get deterministic bytes, then expand to required dimension
        h = hashlib.sha256(text.encode("utf-8")).digest()
        vec: list[float] = []
        for i in range(self._dim):
            # Mix bytes cyclically with position to get varied values
            byte_val = h[i % len(h)] ^ (i & 0xFF)
            # Map to [-1, 1]
            vec.append((byte_val / 127.5) - 1.0)
        # L2-normalise
        norm = math.sqrt(sum(v * v for v in vec))
        if norm > 0:
            vec = [v / norm for v in vec]
        return vec


# ── Embedding generator (facade) ──────────────────────────────────────


@dataclass
class EmbeddingGenerator:
    """Facade that dispatches to the configured :class:`EmbeddingProvider`.

    Usage::

        gen = EmbeddingGenerator(provider=MockEmbeddingProvider())
        result = await gen.embed(["hello", "world"])
        vec = await gen.embed_single("hello")

    Args:
        provider: The underlying embedding provider instance.
    """

    provider: EmbeddingProvider

    async def embed(self, texts: list[str]) -> EmbeddingResult:
        """Embed a list of texts. Delegates to :meth:`provider.embed`."""
        return await self.provider.embed(texts)

    async def embed_single(self, text: str) -> list[float]:
        """Embed a single text string. Convenience wrapper around :meth:`embed`."""
        result = await self.provider.embed([text])
        return result.vectors[0]

    async def batch_embed(
        self, texts: list[str], batch_size: int = 100
    ) -> EmbeddingResult:
        """Embed a large list in smaller batches to manage memory.

        Args:
            texts: Texts to embed.
            batch_size: Number of texts per sub-batch.

        Returns:
            Merged :class:`EmbeddingResult` across all batches.
        """
        all_vectors: list[list[float]] = []
        total_tokens = 0

        for i in range(0, len(texts), batch_size):
            chunk = texts[i : i + batch_size]
            result = await self.provider.embed(chunk)
            all_vectors.extend(result.vectors)
            total_tokens += result.tokens_used

        return EmbeddingResult(
            vectors=all_vectors,
            model=self.provider.model_name,
            dimension=self.provider.dimension,
            tokens_used=total_tokens,
        )

    @property
    def dimension(self) -> int:
        return self.provider.dimension
