"""Request audit state and best-effort finalization, independent of workflow routing."""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime

from app.audit.store import UsageAuditRecord, UsageAuditStore, audit_io
from app.llm.usage import UsageCollector

logger = logging.getLogger(__name__)


@dataclass
class QueryAuditState:
    query_id: str = field(default_factory=lambda: f"q_{uuid.uuid4().hex[:12]}")
    collector: UsageCollector = field(default_factory=UsageCollector)
    llm_calls: int = 0
    outcome: str = "error"
    started_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    store: UsageAuditStore | None = None


async def finish_audit(state: QueryAuditState, session_id: str) -> str:
    if state.store is None:
        return "disabled"
    try:
        record = UsageAuditRecord(
            query_id=state.query_id,
            session_id=session_id,
            outcome=state.outcome,
            llm_calls=state.llm_calls,
            usage=state.collector.summary(),
            started_at=state.started_at,
        )
        store = state.store
        await audit_io(lambda: store.write(record))
    except Exception:
        # Never log storage paths, raw exceptions, prompts or credentials.
        logger.warning("LLM usage audit was not persisted")
        return "failed"
    return "persisted"
