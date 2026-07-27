"""Document upload and management API endpoints.

POST /api/v1/documents/upload  — Upload markdown document for RAG indexing
GET  /api/v1/documents          — List indexed document stats
DELETE /api/v1/documents/{id}   — Delete a document and its chunks
"""

from __future__ import annotations

import contextlib
from datetime import datetime

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

router = APIRouter()

# ── Request models ─────────────────────────────────────────────────────────

_VALID_CONTENT_TYPES = {"user_guide", "best_practice", "sql_style", "faq"}


class DocumentUploadRequest(BaseModel):
    """Request body for POST /api/v1/documents/upload."""

    title: str = Field(..., min_length=1, max_length=500, description="Document title")
    content: str = Field(..., min_length=1, description="Markdown content")
    content_type: str = Field(
        default="user_guide",
        description="One of: user_guide, best_practice, sql_style, faq",
    )
    domain: str | None = Field(default=None, description="Domain identifier")
    language: str = Field(default="zh", description="Language hint: zh, en")
    keywords: list[str] = Field(default_factory=list, description="Search keywords")


# ── Endpoints ──────────────────────────────────────────────────────────────


@router.post("/documents/upload", status_code=201)
async def upload_document(body: DocumentUploadRequest) -> dict:
    """Upload a Markdown document for RAG indexing.

    Content is automatically chunked (Markdown-aware, ~512 tokens per chunk
    with ~64 token overlap) and indexed into both LanceDB and BM25.
    """
    if body.content_type not in _VALID_CONTENT_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid content_type. Must be one of: {_VALID_CONTENT_TYPES}",
        )

    try:
        from app.models.rag_document import Document
        from app.rag import get_document_store
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"RAG engine not available: {e}"
        ) from e

    # Build a safe doc_id
    doc_id = f"doc_{body.title.replace(' ', '_')[:80]}"

    doc = Document(
        doc_id=doc_id,
        domain=body.domain,
        title=body.title,
        content=body.content,
        content_type=body.content_type,
        source="uploaded",
        language=body.language,
        keywords=body.keywords,
    )

    try:
        store = get_document_store()
        chunks_indexed = await store.index_document(doc)

        return {
            "status": "indexed",
            "doc_id": doc_id,
            "title": body.title,
            "content_type": body.content_type,
            "chunks_indexed": chunks_indexed,
            "uploaded_at": datetime.now().isoformat(),
        }
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Document upload failed: {e}"
        ) from e


@router.get("/documents")
async def list_documents() -> dict:
    """Get document index statistics."""
    try:
        from app.rag import get_document_store

        store = get_document_store()
        stats = store.stats()
        return {
            "status": "ok",
            "stats": stats,
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}


@router.delete("/documents/{doc_id}")
async def delete_document(doc_id: str) -> dict:
    """Delete an indexed document and all its chunks from both backends."""
    try:
        from app.rag import get_document_store

        store = get_document_store()

        # Delete from LanceDB (chunks have parent_doc_id or doc_id)
        with contextlib.suppress(Exception):
            store._lancedb.delete(
                store.NAMESPACE,
                f"doc_id = '{doc_id}' OR parent_doc_id = '{doc_id}'",
            )

        # Delete from BM25
        with contextlib.suppress(Exception):
            store._bm25.delete_by_field(store.NAMESPACE, "parent_doc_id", doc_id)
            store._bm25.delete_by_field(store.NAMESPACE, "doc_id", doc_id)

        return {"status": "deleted", "doc_id": doc_id}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Delete failed: {e}") from e
