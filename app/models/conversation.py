"""Conversation session models — ConversationTurn, ConversationSession.

See SPEC §4.7.1 for the full specification.
"""

from dataclasses import dataclass, field
from datetime import datetime

from app.models.feedback import FeedbackData
from app.models.query import SQR, QueryResult, SQLCandidate


@dataclass
class ConversationTurn:
    """A single turn in a multi-turn conversation."""

    turn_id: str
    nl_input: str
    sqr: SQR | None = None
    candidates: list[SQLCandidate] = field(default_factory=list)
    selected_candidate_id: str = ""
    edited_sql: str | None = None
    executed_result: QueryResult | None = None
    feedback: FeedbackData | None = None
    created_at: datetime = field(default_factory=datetime.now)


@dataclass
class ConversationSession:
    """A full conversation session with multiple turns."""

    session_id: str
    domain: str = "default"
    database: str = ""
    turns: list[ConversationTurn] = field(default_factory=list)
    context_summary: str = ""
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)

    @property
    def turn_count(self) -> int:
        return len(self.turns)
