"""Continuous learning system — feedback collection, query pair persistence,
pattern analysis, and rule extraction.

See SPEC §4.6 for the continuous learning architecture specification.

Modules:
    feedback_collector — collect, validate, and persist user feedback (5.1)
    query_pair_store   — persistent NL→SQL pairs with embedding generation (5.2)
    pattern_analyzer   — cluster edits, extract candidate business rules (5.3)
"""

from __future__ import annotations

from app.learning.feedback_collector import FeedbackCollector, feedback_collector
from app.learning.pattern_analyzer import CandidateRule, PatternAnalyzer
from app.learning.query_pair_store import QueryPairStore

__all__ = [
    "CandidateRule",
    "FeedbackCollector",
    "PatternAnalyzer",
    "QueryPairStore",
    "feedback_collector",
]
