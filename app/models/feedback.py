"""Feedback and learning data models.

See SPEC §4.6.5 (FeedbackData) for the full specification.
"""

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class DiffOp:
    """A single diff operation (insert, delete, replace) on SQL text."""

    op_type: str  # "insert", "delete", "replace"
    position: int = 0
    old_text: str = ""
    new_text: str = ""
    length: int = 0


@dataclass
class FeedbackData:
    """Complete feedback record for a single query."""

    session_id: str
    query_id: str
    nl_input: str
    sql_generated: str
    sql_final: str | None = None
    rating: int | None = None  # 1-5
    feedback_text: str | None = None
    diff_operations: list[DiffOp] = field(default_factory=list)
    execution_success: bool = False
    execution_time_ms: int = 0
    user_cancelled: bool = False
    timestamp: datetime = field(default_factory=datetime.now)
