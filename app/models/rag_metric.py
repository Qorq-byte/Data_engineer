"""RAG Metric models — MetricDocument.

See SPEC §4.2.4 (MetricRAG) for the full specification.
"""

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class MetricDocument:
    """A searchable business metric (KPI) in the RAG index."""

    doc_id: str
    domain: str = ""
    name: str = ""
    name_zh: str = ""
    aliases: list[str] = field(default_factory=list)
    description: str = ""
    formula: str = ""
    metric_type: str = "derived"  # "derived", "measure", "ratio"
    aggregation: str = "sum"  # "sum", "count", "avg", "count_distinct"
    dimensions: list[str] = field(default_factory=list)
    time_grain: str = "day"
    precision: int = 2
    sql_template: str = ""
    embedding_text: str = ""
    embedding: list[float] = field(default_factory=list)
    updated_at: datetime = field(default_factory=datetime.now)
    version: int = 1
