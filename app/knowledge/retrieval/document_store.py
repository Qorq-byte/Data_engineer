r"""Document Store — chunked document indexing with hybrid search.

See SPEC §4.2.4 and implementation-plan.md §4.8.2 for the full specification.

This module provides the third concrete RAG application: indexing platform
documents (user guides, best practices, SQL style guides, FAQs) with
Markdown-aware chunking.  Documents are split on heading/paragraph boundaries
into chunks of at most 512 tokens (≈2048 chars) with 64-token (≈256 char)
overlap, then indexed into both LanceDB and BM25 for hybrid retrieval.

Usage::

    from app.knowledge.retrieval.embedding import MockEmbeddingProvider, EmbeddingGenerator
    from app.knowledge.retrieval.lancedb_store import LanceDBStore
    from app.knowledge.retrieval.bm25_index import BM25Index

    store = DocumentStore(
        lancedb_store=LanceDBStore(dimension=256),
        bm25_index=BM25Index(":memory:"),
        embedding_generator=EmbeddingGenerator(provider=MockEmbeddingProvider()),
    )

    # Index (auto-chunks)
    count = await store.index_documents([Document(...), ...])

    # Search
    results = await store.search("how to write JOIN queries", top_k=5)
    for r in results:
        print(r.title, r.content[:100], r.rrf_score)
"""

from __future__ import annotations

import contextlib
import re
from dataclasses import dataclass, field
from typing import Any

from app.knowledge.retrieval.bm25_index import BM25Index
from app.knowledge.retrieval.embedding import EmbeddingGenerator, MockEmbeddingProvider
from app.knowledge.retrieval.lancedb_store import (
    NAMESPACE_DOCUMENTS,
    LanceDBStore,
    document_to_record,
)
from app.knowledge.retrieval.rrf_fusion import RRFFusion

# ── Chunking constants ─────────────────────────────────────────────────

# Approximate: 1 token ≈ 4 characters (works for both English and CJK)
_CHARS_PER_TOKEN = 4
_MAX_CHUNK_TOKENS = 512
_OVERLAP_TOKENS = 64
_MAX_CHUNK_CHARS = _MAX_CHUNK_TOKENS * _CHARS_PER_TOKEN  # 2048
_OVERLAP_CHARS = _OVERLAP_TOKENS * _CHARS_PER_TOKEN         # 256

# Sentinel for "no overlap" when chunk fitting is tight
_MIN_CHUNK_CHARS = 128  # below this, don't split further

# Regex: split before markdown ATX headings (line-start #)
_HEADING_BOUNDARY_RE = re.compile(r"(?=\n#{1,6}\s)")
# Regex: split on double-newline (paragraph boundary)
_PARAGRAPH_BOUNDARY_RE = re.compile(r"\n\n+")


# ── Result model ───────────────────────────────────────────────────────


@dataclass
class DocumentSearchResult:
    """A single result from document hybrid search — one chunk of a document."""

    doc_id: str  # chunk-level doc_id (e.g. "doc_guide#0")
    parent_doc_id: str = ""
    domain: str = ""
    title: str = ""
    content: str = ""
    content_type: str = "user_guide"
    source: str = "uploaded"
    chunk_index: int = 0
    language: str = "en"
    keywords: list[str] = field(default_factory=list)
    embedding_text: str = ""
    rrf_score: float = 0.0
    dense_rank: int | None = None
    sparse_rank: int | None = None

    # Full raw dict from the winning backend (for debugging / advanced use)
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    @property
    def is_chunk(self) -> bool:
        """True if this result is a chunk (not the original whole document)."""
        return bool(self.parent_doc_id)


# ── Chunking helpers ───────────────────────────────────────────────────


def _split_markdown(content: str) -> list[str]:
    """Split Markdown *content* into token-budget-aware chunks.

    Strategy (two-tier):
    1. Split on ``\\n# …`` heading boundaries — natural semantic boundaries.
    2. If a section still exceeds ``_MAX_CHUNK_CHARS``, split on paragraph
       boundaries (``\\n\\n``).
    3. As a last resort, force-split a single overlarge paragraph with overlap.

    Returns:
        List of content chunks, each ≤ ``_MAX_CHUNK_CHARS`` characters.
    """
    if not content.strip():
        return []

    # Tier 1: split on headings
    sections: list[str] = []
    raw_sections = _HEADING_BOUNDARY_RE.split(content)
    for sec in raw_sections:
        sec = sec.strip()
        if sec:
            sections.append(sec)

    # Tier 2: enforce max chunk size
    chunks: list[str] = []
    current: str = ""

    for section in sections:
        if not current:
            # Start a new accumulator
            if len(section) <= _MAX_CHUNK_CHARS:
                current = section
            else:
                # Section alone exceeds limit → paragraph-split it
                chunks.extend(_split_long_section(section))
        else:
            combined = current + "\n\n" + section
            if len(combined) <= _MAX_CHUNK_CHARS:
                current = combined
            else:
                # Current accumulator is full — flush and start new
                chunks.append(current.strip())
                if len(section) <= _MAX_CHUNK_CHARS:
                    current = section
                else:
                    chunks.extend(_split_long_section(section))
                    current = ""

    # Flush remaining
    if current.strip():
        chunks.append(current.strip())

    return chunks


def _split_long_section(text: str) -> list[str]:
    """Split a single oversized section on paragraph boundaries.

    Paragraphs shorter than ``_MAX_CHUNK_CHARS`` are accumulated; if a single
    paragraph exceeds the limit it is force-split with overlap.
    """
    paragraphs = _PARAGRAPH_BOUNDARY_RE.split(text)
    chunks: list[str] = []
    current: str = ""

    for para in paragraphs:
        para = para.strip()
        if not para:
            continue

        if not current:
            if len(para) <= _MAX_CHUNK_CHARS:
                current = para
            else:
                chunks.extend(_force_split_block(para))
        else:
            combined = current + "\n\n" + para
            if len(combined) <= _MAX_CHUNK_CHARS:
                current = combined
            else:
                chunks.append(current.strip())
                if len(para) <= _MAX_CHUNK_CHARS:
                    current = para
                else:
                    chunks.extend(_force_split_block(para))
                    current = ""

    if current.strip():
        chunks.append(current.strip())

    return chunks


def _force_split_block(text: str) -> list[str]:
    """Force-split a text block that exceeds the max chunk size.

    Uses sliding windows with ``_OVERLAP_CHARS`` overlap so context spans
    chunk boundaries.
    """
    chunks: list[str] = []
    start = 0
    text_len = len(text)

    while start < text_len:
        end = min(start + _MAX_CHUNK_CHARS, text_len)
        chunks.append(text[start:end].strip())
        if end >= text_len:
            break
        start = end - _OVERLAP_CHARS
        # Ensure forward progress on tiny texts / edge cases
        if start <= 0 or start >= text_len:
            break

    return chunks


def _make_chunk_doc_id(parent_id: str, chunk_index: int) -> str:
    """Build a chunk-level doc_id from a parent document ID and chunk index."""
    return f"{parent_id}#{chunk_index}"


# ── Metadata extraction ────────────────────────────────────────────────


_INDEXED_META_KEYS: tuple[str, ...] = (
    "parent_doc_id", "domain", "title", "content_type",
    "source", "chunk_index", "language",
)


def _extract_index_meta(doc: Any) -> dict[str, Any]:
    """Pull metadata fields from a Document (or chunk) for BM25 storage."""
    meta: dict[str, Any] = {}
    for key in _INDEXED_META_KEYS:
        val = getattr(doc, key, None)
        if val is not None:
            meta[key] = val
    return meta


def _row_to_doc_result(row: dict[str, Any]) -> DocumentSearchResult:
    """Convert a fused result dict to DocumentSearchResult."""
    keywords = _parse_json_list(row.get("keywords_json", "[]"))

    return DocumentSearchResult(
        doc_id=str(row.get("doc_id", "")),
        parent_doc_id=str(row.get("parent_doc_id", "")),
        domain=str(row.get("domain", "")),
        title=str(row.get("title", "")),
        content=str(row.get("content", "")),
        content_type=str(row.get("content_type", "user_guide")),
        source=str(row.get("source", "uploaded")),
        chunk_index=int(row.get("chunk_index", 0)),
        language=str(row.get("language", "en")),
        keywords=keywords,
        embedding_text=str(row.get("embedding_text", "")),
        rrf_score=float(row.get("rrf_score", 0.0)),
        dense_rank=row.get("dense_rank"),
        sparse_rank=row.get("sparse_rank"),
        raw=row,
    )


def _parse_json_list(raw: Any) -> list[str]:
    """Safely parse a JSON-serialised list of strings."""
    import json

    if isinstance(raw, list):
        return [str(item) for item in raw]
    try:
        parsed = json.loads(str(raw))
        if isinstance(parsed, list):
            return [str(item) for item in parsed]
    except (json.JSONDecodeError, TypeError):
        pass
    return []


# ── Main class ─────────────────────────────────────────────────────────


class DocumentStore:
    r"""Hybrid (dense + sparse) search over chunked platform documents.

    Indexes :class:`~app.models.rag_document.Document` objects by:
    1. Splitting content into chunks (≤ 512 tokens / 2048 chars, 64-token overlap)
       at Markdown heading and paragraph boundaries.
    2. Generating embeddings for each chunk.
    3. Writing each chunk into both LanceDB (vector) and BM25 (keyword).

    Args:
        lancedb_store: Pre-configured :class:`LanceDBStore` (any dimension).
        bm25_index: Pre-configured :class:`BM25Index` (``:memory:`` or file).
        embedding_generator: :class:`EmbeddingGenerator` wrapping any provider.
            Defaults to ``MockEmbeddingProvider`` for testing.
        rrf_fusion: :class:`RRFFusion` with custom k/α.  Defaults to k=60, α=0.5.
        max_chunk_tokens: Maximum tokens per chunk (default 512).
        overlap_tokens: Token overlap between consecutive chunks (default 64).
    """

    NAMESPACE = NAMESPACE_DOCUMENTS

    def __init__(
        self,
        lancedb_store: LanceDBStore,
        bm25_index: BM25Index,
        embedding_generator: EmbeddingGenerator | None = None,
        rrf_fusion: RRFFusion | None = None,
        max_chunk_tokens: int = _MAX_CHUNK_TOKENS,
        overlap_tokens: int = _OVERLAP_TOKENS,
    ):
        self._lancedb = lancedb_store
        self._bm25 = bm25_index
        self._embedding = embedding_generator or EmbeddingGenerator(
            provider=MockEmbeddingProvider()
        )
        self._rrf = rrf_fusion or RRFFusion()

        # Derived chunking limits
        self._max_chunk_chars = max_chunk_tokens * _CHARS_PER_TOKEN
        self._overlap_chars = overlap_tokens * _CHARS_PER_TOKEN

        # Ensure both backends are connected and the namespace exists
        self._lancedb.connect()
        self._bm25.connect()
        self._lancedb.ensure_namespace(self.NAMESPACE)
        self._bm25.ensure_namespace(self.NAMESPACE)

    # ── Properties ─────────────────────────────────────────────────────

    @property
    def embedding_dimension(self) -> int:
        return self._embedding.dimension

    @property
    def rrf_alpha(self) -> float:
        return self._rrf.alpha

    @property
    def rrf_k(self) -> int:
        return self._rrf.k

    @property
    def max_chunk_tokens(self) -> int:
        return self._max_chunk_chars // _CHARS_PER_TOKEN

    @property
    def overlap_tokens(self) -> int:
        return self._overlap_chars // _CHARS_PER_TOKEN

    # ── Indexing ───────────────────────────────────────────────────────

    async def index_documents(
        self,
        docs: list[Any],
        language: str = "auto",
    ) -> int:
        """Index a batch of :class:`~app.models.rag_document.Document` objects.

        Each document is chunked at Markdown heading/paragraph boundaries
        (≤ 512 tokens, 64-token overlap).  Chunks are indexed into both
        LanceDB and BM25.  Duplicate ``doc_id`` values overwrite previous
        chunks from the same parent document.

        Args:
            docs: List of ``Document`` instances.
            language: Language hint for BM25 tokenisation.

        Returns:
            Total number of chunks indexed.
        """
        if not docs:
            return 0

        # 1. Chunk all documents → produce chunk-level Document objects
        all_chunks: list[Any] = []
        for doc in docs:
            chunks = self._chunk_document(doc)
            all_chunks.extend(chunks)

        if not all_chunks:
            return 0

        # 2. Generate embeddings for all chunks
        texts = [c.embedding_text for c in all_chunks]
        emb_result = await self._embedding.embed(texts)

        # 3. Delete existing chunks from LanceDB for these parent doc_ids
        parent_ids = {d.doc_id for d in docs}
        for pid in parent_ids:
            with contextlib.suppress(Exception):
                self._lancedb.delete(
                    self.NAMESPACE,
                    f"parent_doc_id = '{pid}' OR doc_id = '{pid}'",
                )
        # Also delete by chunk-level doc_ids (re-index of same doc)
        chunk_ids_str = ", ".join(f"'{c.doc_id}'" for c in all_chunks)
        with contextlib.suppress(Exception):
            self._lancedb.delete(self.NAMESPACE, f"doc_id IN ({chunk_ids_str})")

        # 4. Insert into LanceDB
        lancedb_records = [
            document_to_record(chunk, vec)
            for chunk, vec in zip(all_chunks, emb_result.vectors, strict=False)
        ]
        self._lancedb.insert(self.NAMESPACE, lancedb_records)

        # 5. Insert into BM25
        bm25_docs: list[dict[str, Any]] = []
        for chunk in all_chunks:
            bm25_text = chunk.embedding_text
            # Augment with keywords for better BM25 recall
            if chunk.keywords:
                bm25_text = bm25_text + " " + " ".join(chunk.keywords)
            bm25_docs.append({
                "doc_id": chunk.doc_id,
                "text": bm25_text,
                "metadata": _extract_index_meta(chunk),
            })
        self._bm25.index(self.NAMESPACE, bm25_docs, language=language)

        return len(all_chunks)

    async def index_document(self, doc: Any, language: str = "auto") -> int:
        """Index a single :class:`~app.models.rag_document.Document`.

        Convenience wrapper around :meth:`index_documents`.
        """
        return await self.index_documents([doc], language=language)

    # ── Search ─────────────────────────────────────────────────────────

    async def search(
        self,
        query: str,
        top_k: int = 10,
        *,
        content_type: str | None = None,
        domain: str | None = None,
        language: str = "auto",
    ) -> list[DocumentSearchResult]:
        """Hybrid search for document chunks relevant to a natural-language *query*.

        Args:
            query: Natural-language search query.
            top_k: Maximum number of chunks to return.
            content_type: Optional filter (``"user_guide"``, ``"best_practice"``,
                ``"sql_style"``, ``"faq"``).
            domain: Optional domain filter.
            language: Language hint for BM25 query tokenisation.

        Returns:
            List of :class:`DocumentSearchResult`, sorted by RRF score (best first).
        """
        # Build LanceDB filter expression
        filters: list[str] = []
        if content_type:
            filters.append(f"content_type = '{content_type}'")
        if domain:
            filters.append(f"domain = '{domain}'")
        filter_expr = " AND ".join(filters) if filters else None

        # 1. Dense search (LanceDB)
        query_vec = await self._embedding.embed_single(query)
        dense_raw = self._lancedb.search(
            self.NAMESPACE,
            query_vec,
            top_k=max(top_k * 2, 20),
            filter_expr=filter_expr,
        )
        dense_results: list[dict[str, Any]] = []
        for row in dense_raw:
            dist = row.get("_distance", 1.0)
            dense_results.append({
                **row,
                "score": 1.0 / (1.0 + dist),
            })

        # 2. Sparse search (BM25)
        sparse_raw = self._bm25.search(
            self.NAMESPACE,
            query,
            top_k=max(top_k * 2, 20),
            language=language,
        )
        sparse_results: list[dict[str, Any]] = []
        for row in sparse_raw:
            unpacked: dict[str, Any] = dict(row)
            meta = unpacked.pop("metadata", {})
            if isinstance(meta, dict):
                unpacked.update(meta)
            sparse_results.append(unpacked)

        # 3. RRF fusion
        fused = self._rrf.fuse(dense_results, sparse_results, top_k=top_k)

        # 4. Convert to DocumentSearchResult
        results = [_row_to_doc_result(r) for r in fused]

        # 5. Post-filter (BM25 has no server-side filter)
        if content_type:
            results = [r for r in results if r.content_type == content_type]
        if domain:
            results = [r for r in results if r.domain == domain]

        return results

    # ── Chunking ───────────────────────────────────────────────────────

    def _chunk_document(self, doc: Any) -> list[Any]:
        """Split a single Document into chunk Documents.

        Uses :func:`_split_markdown` for heading/paragraph-aware chunking.
        Each chunk inherits metadata from the parent document and gets a
        unique ``doc_id`` of the form ``{parent_id}#{n}``.
        """
        from copy import copy

        content = doc.content
        if not content or not content.strip():
            # Empty document → single chunk with empty content
            chunk = copy(doc)
            chunk.parent_doc_id = None
            chunk.chunk_index = 0
            return [chunk]

        chunks_text = _split_markdown(content)

        if not chunks_text:
            chunk = copy(doc)
            chunk.parent_doc_id = None
            chunk.chunk_index = 0
            return [chunk]

        result: list[Any] = []
        for i, chunk_text in enumerate(chunks_text):
            chunk = copy(doc)
            chunk.doc_id = _make_chunk_doc_id(doc.doc_id, i)
            chunk.parent_doc_id = doc.doc_id
            chunk.content = chunk_text
            chunk.chunk_index = i
            # Build embedding_text from title + first line of content for
            # better dense retrieval signal
            first_line = chunk_text.split("\n", 1)[0][:120].strip()
            chunk.embedding_text = (
                f"{doc.title} — {first_line}" if doc.title else first_line
            )
            result.append(chunk)

        return result

    # ── Management ─────────────────────────────────────────────────────

    def clear(self) -> None:
        """Remove all indexed document chunks from both backends."""
        self._lancedb.drop_namespace(self.NAMESPACE)
        self._lancedb.ensure_namespace(self.NAMESPACE)
        self._bm25.clear(self.NAMESPACE)

    def stats(self) -> dict[str, int]:
        """Return chunk counts for both backends."""
        return {
            "lancedb_count": self._lancedb.count(self.NAMESPACE),
            "bm25_count": self._bm25.count(self.NAMESPACE),
        }

    # ── Convenience ────────────────────────────────────────────────────

    @staticmethod
    def get_parent_documents(
        results: list[DocumentSearchResult],
    ) -> list[str]:
        """Extract deduplicated parent document IDs from search results.

        Args:
            results: Output of :meth:`search`.

        Returns:
            Parent doc IDs in the order they first appear.
        """
        seen: set[str] = set()
        parents: list[str] = []
        for r in results:
            pid = r.parent_doc_id or r.doc_id
            if pid not in seen:
                seen.add(pid)
                parents.append(pid)
        return parents

    @staticmethod
    def count_chunks(results: list[DocumentSearchResult]) -> int:
        """Count the number of chunk-level results (excludes whole-doc results)."""
        return sum(1 for r in results if r.is_chunk)
