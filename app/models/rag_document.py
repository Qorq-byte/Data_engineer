"""RAG Document Store models — Document.

See SPEC §4.2.4 (DocumentStore) for the full specification.
"""

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class Document:
    """A searchable platform document (docs, guides, best practices) in the RAG index."""

    doc_id: str
    domain: str | None = None
    title: str = ""
    content: str = ""
    content_type: str = "user_guide"  # "user_guide", "best_practice", "sql_style", "faq"
    source: str = "uploaded"  # "uploaded", "builtin", "generated"
    chunk_index: int = 0
    parent_doc_id: str | None = None
    embedding_text: str = ""
    embedding: list[float] = field(default_factory=list)
    keywords: list[str] = field(default_factory=list)
    language: str = "en"  # "zh", "en"
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)
