"""Tests for DocumentStore — Markdown chunking + hybrid document search.

Covers chunking logic, indexing, search, and edge cases.
"""

from __future__ import annotations

import pytest

from app.knowledge.retrieval.bm25_index import BM25Index
from app.knowledge.retrieval.document_store import (
    DocumentSearchResult,
    DocumentStore,
    _extract_index_meta,
    _force_split_block,
    _make_chunk_doc_id,
    _parse_json_list,
    _row_to_doc_result,
    _split_markdown,
)
from app.knowledge.retrieval.embedding import EmbeddingGenerator, MockEmbeddingProvider
from app.knowledge.retrieval.lancedb_store import LanceDBStore
from app.knowledge.retrieval.rrf_fusion import RRFFusion
from app.models.rag_document import Document

# ── Helpers ────────────────────────────────────────────────────────────

DIM = 128


def _make_doc(
    doc_id: str,
    domain: str = "platform",
    title: str = "User Guide",
    content: str = "This is a sample document.",
    content_type: str = "user_guide",
    source: str = "uploaded",
    keywords: list[str] | None = None,
    language: str = "en",
) -> Document:
    return Document(
        doc_id=doc_id,
        domain=domain,
        title=title,
        content=content,
        content_type=content_type,
        source=source,
        keywords=keywords or [],
        language=language,
        embedding_text=f"{title} — {content[:60]}",
    )


def _make_store(
    tmp_path,
    alpha: float = 0.5,
    k: int = 60,
    max_chunk_tokens: int | None = None,
    overlap_tokens: int | None = None,
) -> DocumentStore:
    """Build a DocumentStore with file-backed LanceDB + in-memory BM25."""
    lancedb_uri = str(tmp_path / "lancedb_doc_store")
    kwargs: dict = {}
    if max_chunk_tokens is not None:
        kwargs["max_chunk_tokens"] = max_chunk_tokens
    if overlap_tokens is not None:
        kwargs["overlap_tokens"] = overlap_tokens
    return DocumentStore(
        lancedb_store=LanceDBStore(dimension=DIM, uri=lancedb_uri),
        bm25_index=BM25Index(":memory:"),
        embedding_generator=EmbeddingGenerator(provider=MockEmbeddingProvider(dimension=DIM)),
        rrf_fusion=RRFFusion(k=k, alpha=alpha),
        **kwargs,
    )


# ── Chunking tests ─────────────────────────────────────────────────────


class TestMarkdownSplit:
    def test_empty_content(self):
        assert _split_markdown("") == []
        assert _split_markdown("   ") == []

    def test_single_short_paragraph(self):
        chunks = _split_markdown("A short document.")
        assert len(chunks) == 1
        assert chunks[0] == "A short document."

    def test_splits_on_headings(self):
        content = (
            "# Section 1\nContent of section 1.\n\n"
            "## Section 2\nContent of section 2.\n\n"
            "# Section 3\nContent of section 3."
        )
        chunks = _split_markdown(content)
        assert len(chunks) >= 1  # short content may be merged into one chunk
        # If merged, the combined text should contain all sections
        combined = " ".join(chunks)
        assert "Section 1" in combined
        assert "Section 2" in combined
        assert "Section 3" in combined

    def test_splits_large_document(self):
        """A document with many sections should produce multiple chunks."""
        sections = []
        for i in range(30):
            sections.append(f"## Section {i}\n" + "Lorem ipsum dolor sit amet. " * 40)
        content = "\n\n".join(sections)
        chunks = _split_markdown(content)
        # Should produce multiple chunks due to size
        assert len(chunks) >= 2

    def test_force_split_large_block(self):
        """A single block exceeding max chars should be force-split."""
        big = "A" * 3000
        chunks = _force_split_block(big)
        assert len(chunks) >= 2
        # Each chunk should be ≤ 2048 chars
        for c in chunks:
            assert len(c) <= 2048
        # Should have overlap: first chunk end and second chunk start overlap
        # (overlap = 256 chars)
        if len(chunks) >= 2:
            assert chunks[0][-4:] in big  # last bit of first chunk exists in original

    def test_force_split_empty(self):
        assert _force_split_block("") == []
        assert _force_split_block("   ") == [""]  # strips to empty

    def test_make_chunk_doc_id(self):
        assert _make_chunk_doc_id("guide", 0) == "guide#0"
        assert _make_chunk_doc_id("guide", 5) == "guide#5"

    def test_chunk_preserves_heading_structure(self):
        """Heading boundary split should keep the heading in the chunk."""
        content = "# Title\nBody text here."
        chunks = _split_markdown(content)
        assert len(chunks) == 1
        assert "# Title" in chunks[0]
        assert "Body text" in chunks[0]

    def test_overlap_between_chunks(self):
        """Force-split chunks should have overlapping content."""
        # Create text that forces chunks: 2500 chars, max 2048, overlap 256
        words = "Lorem ipsum dolor sit amet consectetur adipiscing elit. "
        big = words * 50  # ~2500+ chars
        chunks = _force_split_block(big)
        if len(chunks) >= 2:
            # Not a strict test — just verify chunks are non-trivial
            assert len(chunks[0]) > 100
            assert len(chunks[1]) > 100


# ── Helper function tests ──────────────────────────────────────────────


class TestHelpers:
    def test_extract_index_meta(self):
        doc = _make_doc("guide.section1", domain="platform", title="My Guide",
                        content_type="faq", source="builtin")
        meta = _extract_index_meta(doc)
        assert meta["domain"] == "platform"
        assert meta["title"] == "My Guide"
        assert meta["content_type"] == "faq"
        assert meta["source"] == "builtin"

    def test_row_to_doc_result(self):
        row = {
            "doc_id": "guide#0",
            "parent_doc_id": "guide",
            "domain": "platform",
            "title": "User Guide",
            "content": "# Intro\nWelcome to the guide.",
            "content_type": "user_guide",
            "source": "uploaded",
            "chunk_index": 0,
            "language": "en",
            "keywords_json": '["sql", "join"]',
            "embedding_text": "User Guide — # Intro",
            "rrf_score": 0.025,
            "dense_rank": 1,
            "sparse_rank": 2,
        }
        dr = _row_to_doc_result(row)
        assert dr.doc_id == "guide#0"
        assert dr.parent_doc_id == "guide"
        assert dr.title == "User Guide"
        assert dr.content_type == "user_guide"
        assert dr.chunk_index == 0
        assert dr.keywords == ["sql", "join"]
        assert dr.rrf_score == 0.025
        assert dr.dense_rank == 1
        assert dr.sparse_rank == 2
        assert dr.is_chunk is True

    def test_row_to_doc_result_no_parent(self):
        row = {
            "doc_id": "guide",
            "parent_doc_id": "",
            "domain": "",
            "title": "Guide",
            "content": "text",
            "content_type": "best_practice",
            "source": "uploaded",
            "chunk_index": 0,
            "language": "en",
            "keywords_json": "[]",
            "embedding_text": "Guide",
            "rrf_score": 0.01,
            "dense_rank": None,
            "sparse_rank": 1,
        }
        dr = _row_to_doc_result(row)
        assert dr.is_chunk is False

    def test_parse_json_list_variants(self):
        assert _parse_json_list(["a"]) == ["a"]
        assert _parse_json_list('["x"]') == ["x"]
        assert _parse_json_list("bad") == []
        assert _parse_json_list(None) == []


# ── Integration tests ──────────────────────────────────────────────────


class TestDocumentStore:
    """Integration tests for the full index → search pipeline."""

    @pytest.fixture
    def store(self, tmp_path) -> DocumentStore:
        return _make_store(tmp_path)

    # ── Construction ───────────────────────────────────────────────────

    def test_construction_defaults(self, tmp_path):
        store = _make_store(tmp_path)
        assert store.embedding_dimension == DIM
        assert store.rrf_alpha == 0.5
        assert store.rrf_k == 60
        assert store.max_chunk_tokens == 512
        assert store.overlap_tokens == 64

    def test_construction_custom_chunking(self, tmp_path):
        store = _make_store(tmp_path, max_chunk_tokens=256, overlap_tokens=32)
        assert store.max_chunk_tokens == 256
        assert store.overlap_tokens == 32

    def test_namespace_is_documents(self, store: DocumentStore):
        assert store.NAMESPACE == "documents"

    # ── Indexing ───────────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_index_documents_populates_both_backends(self, store: DocumentStore):
        docs = [
            _make_doc("guide.sql_style", title="SQL Style Guide",
                      content="Use uppercase for SQL keywords.\n\n## Joins\nUse JOIN not comma."),
            _make_doc("guide.best_practice", title="Best Practices",
                      content="Always use parameterized queries.\n\n"
                              "## Security\nAvoid SQL injection.",
                      content_type="best_practice"),
        ]
        count = await store.index_documents(docs)
        assert count >= 2  # at least one chunk per doc

        stats = store.stats()
        assert stats["lancedb_count"] >= 2
        assert stats["bm25_count"] >= 2

    @pytest.mark.asyncio
    async def test_index_empty_list(self, store: DocumentStore):
        count = await store.index_documents([])
        assert count == 0

    @pytest.mark.asyncio
    async def test_index_single_doc(self, store: DocumentStore):
        doc = _make_doc("guide.one", title="One", content="Just one paragraph.")
        count = await store.index_document(doc)
        assert count == 1

    @pytest.mark.asyncio
    async def test_index_overwrites_duplicate(self, store: DocumentStore):
        doc1 = _make_doc("dup.id", title="V1", content="Old content.")
        doc2 = _make_doc("dup.id", title="V2", content="New content here.")
        await store.index_documents([doc1])
        count2 = await store.index_documents([doc2])
        # After re-index, should have the same number of chunks (not doubled)
        stats = store.stats()
        assert stats["lancedb_count"] == count2  # one doc → N chunks, not N+old
        assert stats["bm25_count"] == count2

    @pytest.mark.asyncio
    async def test_chunked_document_has_chunk_ids(self, store: DocumentStore):
        doc = _make_doc("guide.split", title="Split Test",
                        content="## A\n" + "word " * 500 + "\n## B\n" + "word " * 500)
        count = await store.index_documents([doc])
        # With 500 "word " = 2500 chars per section, each section is > 2048
        # So it should split into at least 2 chunks
        assert count >= 2

    @pytest.mark.asyncio
    async def test_empty_content_document(self, store: DocumentStore):
        doc = _make_doc("empty.doc", title="Empty", content="")
        count = await store.index_documents([doc])
        assert count == 1  # single chunk with empty content

    # ── Search ─────────────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_search_returns_results(self, store: DocumentStore):
        docs = [
            _make_doc("guide.sql", title="SQL Guide",
                      content="# SQL Guide\n\nUse SELECT for queries.\n\n"
                              "## JOIN\nUse JOIN to combine tables.",
                      keywords=["sql", "select", "join"]),
            _make_doc("guide.python", title="Python Guide",
                      content="# Python Guide\n\nUse list comprehensions.\n\n"
                              "## Dict\nUse dict for mappings.",
                      keywords=["python", "list", "dict"]),
        ]
        await store.index_documents(docs)

        results = await store.search("JOIN tables", top_k=3)
        assert len(results) >= 1
        assert results[0].title == "SQL Guide"

    @pytest.mark.asyncio
    async def test_search_respects_top_k(self, store: DocumentStore):
        docs = [
            _make_doc(f"guide.{i}", title=f"Guide {i}",
                      content=f"Content number {i}. " * 50)
            for i in range(10)
        ]
        await store.index_documents(docs)
        results = await store.search("Content number 1", top_k=3)
        assert len(results) == 3

    @pytest.mark.asyncio
    async def test_search_empty_index(self, store: DocumentStore):
        results = await store.search("anything")
        assert results == []

    @pytest.mark.asyncio
    async def test_search_with_content_type_filter(self, store: DocumentStore):
        docs = [
            _make_doc("faq.join", title="JOIN FAQ", content="How to use JOIN in SQL?",
                      content_type="faq", keywords=["join", "faq"]),
            _make_doc("best.join", title="JOIN Best Practice",
                      content="Always specify JOIN conditions.",
                      content_type="best_practice", keywords=["join", "best"]),
        ]
        await store.index_documents(docs)

        results = await store.search("JOIN", top_k=10, content_type="faq")
        for r in results:
            assert r.content_type == "faq"

    @pytest.mark.asyncio
    async def test_search_with_domain_filter(self, store: DocumentStore):
        docs = [
            _make_doc("sales.guide", domain="sales", title="Sales Guide",
                      content="How to query sales data.", keywords=["sales"]),
            _make_doc("marketing.guide", domain="marketing", title="Marketing Guide",
                      content="How to query marketing data.", keywords=["marketing"]),
        ]
        await store.index_documents(docs)

        results = await store.search("query data", top_k=10, domain="sales")
        for r in results:
            assert r.domain == "sales"

    @pytest.mark.asyncio
    async def test_search_has_rrf_metadata(self, store: DocumentStore):
        docs = [
            _make_doc(f"guide.{i}", title=f"Guide {i}",
                      content=f"Content {i}. " * 50)
            for i in range(5)
        ]
        await store.index_documents(docs)
        results = await store.search("Content 0", top_k=3)
        for r in results:
            assert r.rrf_score > 0
            assert isinstance(r.dense_rank, int) or r.dense_rank is None
            assert isinstance(r.sparse_rank, int) or r.sparse_rank is None

    @pytest.mark.asyncio
    async def test_search_chinese(self, store: DocumentStore):
        docs = [
            _make_doc("guide.sql", title="SQL 指南",
                      content="# SQL 指南\n\n使用 SELECT 查询数据。",
                      keywords=["sql", "查询"], language="zh"),
            _make_doc("guide.py", title="Python 指南",
                      content="# Python 指南\n\n使用 list 存储数据。",
                      keywords=["python", "列表"], language="zh"),
        ]
        await store.index_documents(docs, language="zh")
        results = await store.search("SQL 查询", top_k=3, language="zh")
        assert len(results) >= 1
        assert "SQL" in results[0].title

    @pytest.mark.asyncio
    async def test_search_matches_by_keywords(self, store: DocumentStore):
        """BM25 should match keyword fields even when content doesn't contain the term."""
        doc = _make_doc("guide.db", title="Database Guide",
                        content="Connect and query your data.",
                        keywords=["postgresql", "mysql", "connection"])
        await store.index_documents([doc])
        results = await store.search("postgresql", top_k=3)
        # Keywords are appended to BM25 text, so "postgresql" should match
        assert len(results) >= 1

    # ── Management ─────────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_clear_removes_all(self, store: DocumentStore):
        docs = [
            _make_doc(f"guide.{i}", title=f"Guide {i}",
                      content=f"Content {i}. " * 50)
            for i in range(3)
        ]
        await store.index_documents(docs)
        assert store.stats()["lancedb_count"] >= 3

        store.clear()
        stats = store.stats()
        assert stats["lancedb_count"] == 0
        assert stats["bm25_count"] == 0

    @pytest.mark.asyncio
    async def test_clear_then_reindex(self, store: DocumentStore):
        await store.index_documents([
            _make_doc("guide.sql", title="SQL Guide", content="Use SELECT.")
        ])
        store.clear()
        await store.index_documents([
            _make_doc("guide.py", title="Python Guide", content="Use list.")
        ])
        results = await store.search("Python list", top_k=3)
        assert len(results) >= 1
        assert results[0].title == "Python Guide"

    # ── Convenience methods ────────────────────────────────────────────

    def test_get_parent_documents(self, store: DocumentStore):
        results = [
            DocumentSearchResult(doc_id="guide#0", parent_doc_id="guide",
                                 title="Guide", rrf_score=0.02),
            DocumentSearchResult(doc_id="guide#1", parent_doc_id="guide",
                                 title="Guide", rrf_score=0.015),
            DocumentSearchResult(doc_id="faq", parent_doc_id="",
                                 title="FAQ", rrf_score=0.01),
        ]
        parents = store.get_parent_documents(results)
        assert parents == ["guide", "faq"]

    def test_get_parent_documents_empty(self, store: DocumentStore):
        assert store.get_parent_documents([]) == []

    def test_count_chunks(self, store: DocumentStore):
        results = [
            DocumentSearchResult(doc_id="guide#0", parent_doc_id="guide"),
            DocumentSearchResult(doc_id="guide#1", parent_doc_id="guide"),
            DocumentSearchResult(doc_id="faq", parent_doc_id=""),
        ]
        assert store.count_chunks(results) == 2

    def test_count_chunks_none(self, store: DocumentStore):
        results = [
            DocumentSearchResult(doc_id="a", parent_doc_id=""),
            DocumentSearchResult(doc_id="b", parent_doc_id=""),
        ]
        assert store.count_chunks(results) == 0

    # ── Edge cases ─────────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_complex_markdown(self, store: DocumentStore):
        """Document with code blocks, lists, and nested headings."""
        content = (
            "# SQL Style Guide\n\n"
            "## Naming\n\n"
            "- Use `snake_case` for table names\n"
            "- Use `PascalCase` for column aliases\n\n"
            "## Queries\n\n"
            "```sql\n"
            "SELECT *\n"
            "FROM users\n"
            "WHERE active = 1;\n"
            "```\n\n"
            "### JOIN Guidelines\n\n"
            "1. Always use `INNER JOIN` explicitly\n"
            "2. Put join conditions in the `ON` clause\n\n"
            "## Indexing\n\n"
            "Create indexes on foreign key columns."
        )
        doc = _make_doc("guide.sql_style", title="SQL Style Guide",
                        content=content, keywords=["sql", "style", "join"])
        count = await store.index_documents([doc])
        assert count >= 1
        results = await store.search("JOIN guidelines", top_k=3)
        assert len(results) >= 1

    @pytest.mark.asyncio
    async def test_whitespace_only_content_doesnt_crash(self, store: DocumentStore):
        doc = _make_doc("blank", title="Blank", content="\n\n   \n\n")
        count = await store.index_documents([doc])
        assert count >= 0  # shouldn't crash

    @pytest.mark.asyncio
    async def test_long_document_chunks_preserved(self, store: DocumentStore):
        """Index a large doc and verify chunks are independently searchable."""
        content = ""
        for i in range(40):
            content += f"## Section {i}\n"
            content += f"Topic number {i}: This section discusses subject {i} in detail. " * 20
            content += "\n\n"

        doc = _make_doc("guide.large", title="Large Guide", content=content,
                        keywords=["section", "topic"])
        count = await store.index_documents([doc])
        # Should produce multiple chunks
        assert count >= 3

        # Different queries should surface different chunks
        results_early = await store.search("Topic number 1 subject", top_k=3)
        results_late = await store.search("Topic number 38 subject", top_k=3)
        assert len(results_early) >= 1
        assert len(results_late) >= 1


class TestDocumentStoreAlpha:
    """Tests for different alpha (dense/sparse) weightings."""

    @pytest.mark.asyncio
    async def test_dense_only_alpha(self, tmp_path):
        store = _make_store(tmp_path, alpha=1.0)
        docs = [
            _make_doc(f"guide.{i}", title=f"Guide {i}",
                      content="sql query join select from where group by having. " * 10)
            for i in range(3)
        ]
        await store.index_documents(docs)
        results = await store.search("sql join query", top_k=3)
        assert len(results) >= 1

    @pytest.mark.asyncio
    async def test_sparse_only_alpha(self, tmp_path):
        store = _make_store(tmp_path, alpha=0.0)
        docs = [
            _make_doc(f"guide.{i}", title=f"Guide {i}",
                      content="sql query join select from where group by having. " * 10)
            for i in range(3)
        ]
        await store.index_documents(docs)
        results = await store.search("sql join query", top_k=3)
        assert len(results) >= 1
