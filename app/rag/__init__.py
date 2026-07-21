"""RAG index module — converters, engine singleton, and index auto-refresh.

Public API::

    from app.rag import (
        initialize_rag_engine,
        get_rag_engine,
        get_schema_rag,
        get_metric_rag,
        get_document_store,
        get_index_refresher,
    )
"""

from __future__ import annotations

from app.rag.engine import (
    get_document_store,
    get_index_refresher,
    get_metric_rag,
    get_rag_engine,
    get_schema_rag,
    initialize_rag_engine,
)

__all__ = [
    "get_document_store",
    "get_index_refresher",
    "get_metric_rag",
    "get_rag_engine",
    "get_schema_rag",
    "initialize_rag_engine",
]
