"""Feedback collection API endpoints."""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.storage.mysql_store import get_store

logger = logging.getLogger(__name__)
router = APIRouter()

class FeedbackRequest(BaseModel):
    query_id: str = Field(default="")
    session_id: str = Field(default="")
    nl_input: str = Field(default="")
    sql_generated: str = Field(default="")
    sql_final: str = Field(default="")
    rating: int = Field(default=0, ge=0, le=5)
    feedback_text: str = Field(default="")
    domain: str = Field(default="")

_feedback_store: list[dict[str, Any]] = []

@router.post("/feedback")
async def submit_feedback(body: FeedbackRequest) -> dict:
    record = {"id": f"fb_{len(_feedback_store)+1}_{datetime.now().strftime('%Y%m%d%H%M%S')}", "query_id": body.query_id, "session_id": body.session_id, "nl_input": body.nl_input, "sql_generated": body.sql_generated, "sql_final": body.sql_final, "rating": body.rating, "feedback_text": body.feedback_text, "domain": body.domain, "created_at": datetime.now().isoformat()}
    _feedback_store.append(record)
    try:
        store = get_store()
        await store.save_feedback(record)
    except Exception as e:
        logger.warning("MySQL store unavailable: %s", e)
    try:
        from app.learning.feedback_collector import feedback_collector

        # Auto-compute diff_operations when the frontend doesn't supply them.
        # This is the critical fix: without diff_operations, diff_count=0 in
        # SQLite, which makes PatternAnalyzer skip the record (Guard 2).
        # We compute a simple "replace whole SQL" diff so the collector
        # records diff_count > 0 and PatternAnalyzer can analyse the edit.
        diff_operations = None
        sql_gen = body.sql_generated or ""
        sql_final = body.sql_final or ""
        if sql_final and sql_gen and sql_gen != sql_final:
            try:
                from app.models.feedback import DiffOp
                diff_operations = [DiffOp(
                    op_type="replace",
                    position=0,
                    old_text=sql_gen,
                    new_text=sql_final,
                    length=len(sql_final),
                )]
            except Exception:
                diff_operations = None

        feedback_collector.collect(
            session_id=body.session_id or "unknown",
            query_id=body.query_id or "unknown",
            nl_input=body.nl_input,
            sql_generated=body.sql_generated,
            sql_final=body.sql_final or None,
            rating=body.rating,
            feedback_text=body.feedback_text or None,
            diff_operations=diff_operations,
            domain_id=body.domain or None,
        )
    except Exception as e:
        logger.warning("Feedback collector unavailable: %s", e)
    return {"status": "recorded", "feedback_id": record["id"], "message": "感谢您的反馈！反馈已记录至学习库。"}

@router.get("/feedback/stats")
async def feedback_stats() -> dict:
    try:
        store = get_store()
        return await store.get_feedback_stats()
    except Exception as e:
        logger.warning("MySQL store unavailable, falling back to in-memory: %s", e)
    if not _feedback_store:
        return {"total": 0, "avg_rating": 0.0, "ratings_distribution": {}, "recent_count_7d": 0}
    ratings = [r["rating"] for r in _feedback_store if r["rating"] > 0]
    dist: dict[int, int] = {}
    for r in ratings:
        dist[r] = dist.get(r, 0) + 1
    return {"total": len(_feedback_store), "avg_rating": round(sum(ratings)/len(ratings),2) if ratings else 0.0, "ratings_distribution": {str(k):v for k,v in sorted(dist.items())}, "recent_count_7d": len(_feedback_store)}

@router.get("/feedback/recent")
async def recent_feedback(limit: int = 20) -> dict:
    try:
        store = get_store()
        records = await store.get_recent_feedback(limit)
        return {"records": records, "total": len(records)}
    except Exception as e:
        logger.warning("MySQL store unavailable, falling back to in-memory: %s", e)
    records = _feedback_store[-limit:] if _feedback_store else []
    return {"records": list(reversed(records)), "total": len(records)}
