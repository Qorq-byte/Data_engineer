"""RAG Schema Metadata models — SchemaDocument.

See SPEC §4.2.4 (SchemaMetadataRAG) for the full specification.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class SchemaDocument:
    """A searchable document representing a table or column in the RAG index."""

    doc_id: str  # {db_id}.{schema}.{table}.{column}
    db_id: str
    schema_name: str = "public"
    table_name: str = ""
    column_name: str | None = None
    data_type: str | None = None
    comment: str | None = None
    is_primary_key: bool = False
    is_foreign_key: bool = False
    fk_references: tuple | None = None  # (table, column)
    enum_values: list[str] | None = None
    sample_values: list[Any] = field(default_factory=list)
    table_row_count: int = 0
    embedding_text: str = ""
    embedding: list[float] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    updated_at: datetime = field(default_factory=datetime.now)

    @property
    def is_table_level(self) -> bool:
        return self.column_name is None
