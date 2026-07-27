"""Learning & continuous improvement API endpoints.

See SPEC 6.6 (Learning & Feedback API) for the full specification.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

from fastapi import APIRouter

from app.storage.mysql_store import get_store

logger = logging.getLogger(__name__)

router = APIRouter()

# ── In-memory learning store ────────────────────────────────────────────

_learning_store: dict[str, Any] = {
    "total_queries": 0,
    "total_feedback": 0,
    "avg_rating": 0.0,
    "query_pairs": [],
    "rule_candidates": [],
    "quality_trends": [],
}


def record_query(nl_text: str = "", sql_text: str = "", domain: str = "") -> None:
    """Record a new query for learning stats. Called from query.py."""
    _learning_store["total_queries"] += 1
    # Record as a query pair for learning
    if nl_text and sql_text:
        _learning_store["query_pairs"].append({
            "id": f"qp_{datetime.now().strftime('%Y%m%d%H%M%S')}_{_learning_store['total_queries']}",
            "nl_text": nl_text,
            "sql_text": sql_text,
            "domain": domain,
            "rating": 0,
            "created_at": datetime.now().isoformat(),
        })
        # Keep only last 200 pairs
        if len(_learning_store["query_pairs"]) > 200:
            _learning_store["query_pairs"] = _learning_store["query_pairs"][-200:]

        # Also record to feedback_collector for PatternAnalyzer to mine rules
        try:
            from app.learning.feedback_collector import feedback_collector
            feedback_collector.collect(
                session_id="auto",
                query_id=f"q_{_learning_store['total_queries']}",
                nl_input=nl_text,
                sql_generated=sql_text,
                sql_final=sql_text,
                rating=0,
                domain_id=domain,
            )
        except Exception:
            pass

    # Update quality trend for today
    today_str = datetime.now().strftime("%Y-%m-%d")
    existing = next((t for t in _learning_store["quality_trends"] if t["date"] == today_str), None)
    if existing:
        existing["query_count"] += 1
    else:
        _learning_store["quality_trends"].append({
            "date": today_str,
            "accuracy": 0.0,
            "query_count": 1,
            "avg_response_ms": 0,
            "feedback_count": 0,
        })
        # Keep last 90 days
        if len(_learning_store["quality_trends"]) > 90:
            _learning_store["quality_trends"] = _learning_store["quality_trends"][-90:]


def _sync_with_feedback_store():
    """Sync learning stats from the feedback API store."""
    try:
        from app.api.feedback import _feedback_store
        total = len(_feedback_store)
        ratings = [r["rating"] for r in _feedback_store if r.get("rating", 0) > 0]
        _learning_store["total_feedback"] = total
        _learning_store["avg_rating"] = (
            round(sum(ratings) / len(ratings), 2) if ratings else 0.0
        )
        # Update accuracy in quality trends based on feedback ratings
        if ratings and _learning_store["quality_trends"]:
            today = _learning_store["quality_trends"][-1]
            if today:
                today["feedback_count"] = total
                today["accuracy"] = round(sum(1 for r in ratings if r >= 4) / len(ratings), 3) if ratings else 0.0
    except Exception:
        pass


# ── Endpoints ───────────────────────────────────────────────────────────


def _get_fresh_candidates() -> list[dict[str, Any]]:
    """Run PatternAnalyzer and return fresh candidate dicts with review status applied.

    Shared by both ``learning_stats`` and ``rule_candidates`` so the stats
    panel always reflects the real PatternAnalyzer output.

    Review-status rules:
      - Both ``approved`` and ``rejected`` statuses are preserved — reviewed
        candidates stay reviewed and don't reappear in the pending queue.
      - Only genuinely new patterns (unseen rule_ids) appear as
        ``pending_review``.
    """
    from app.learning.pattern_analyzer import PatternAnalyzer
    from app.learning.feedback_collector import feedback_collector

    analyzer = PatternAnalyzer(feedback_collector)
    candidates_raw = analyzer.analyze(domain="", window_days=90, min_occurrence=1)
    if not candidates_raw:
        return []

    candidate_dicts = analyzer.to_dicts(candidates_raw)
    for d in candidate_dicts:
        if "rule_id" in d and "id" not in d:
            d["id"] = d.pop("rule_id")
        if "occurrence_count" in d:
            d["source_count"] = d.pop("occurrence_count")
        if "pattern_description" in d:
            d["description"] = d.pop("pattern_description")
        if "suggested_pattern" in d:
            d["pattern"] = d.pop("suggested_pattern")
        if "domain_id" in d:
            d["domain"] = d.pop("domain_id")

    # Preserve EXPLICITLY reviewed statuses (both approved AND rejected).
    # Rejected candidates stay rejected UNLESS new feedback supports the
    # same pattern (source_count increased since rejection) — in that case
    # they reappear as pending_review, giving the user a chance to
    # reconsider with the new evidence.
    reviewed_status: dict[str, str] = {}
    rejected_source_counts: dict[str, int] = {}
    for r in _learning_store["rule_candidates"]:
        rid = r.get("id") or r.get("rule_id", "")
        r_status = r.get("status", "pending_review")
        if rid and r_status in ("approved", "rejected"):
            reviewed_status[rid] = r_status
            if r_status == "rejected":
                rejected_source_counts[rid] = r.get("rejected_source_count", 0)

    for d in candidate_dicts:
        rid = d.get("id", "")
        if rid in reviewed_status:
            stored = reviewed_status[rid]
            if stored == "rejected":
                # New evidence? source_count increased since rejection
                if d.get("source_count", 0) > rejected_source_counts.get(rid, 0):
                    d["status"] = "pending_review"
                else:
                    d["status"] = "rejected"
            else:
                d["status"] = stored  # approved
        else:
            d["status"] = "pending_review"

    return candidate_dicts


@router.get("/learning/stats")
async def learning_stats() -> dict:
    """Get learning statistics.

    Uses in-memory store (populated by real-time record_query calls) as the
    primary source, with MySQL as fallback for persistence and rule candidates.
    """
    _sync_with_feedback_store()

    # Run PatternAnalyzer for real, live rule-candidate counts — the
    # in-memory store is only updated when /rule-candidates is called, so
    # reading from it here produces stale/fixed numbers in the stats panel.
    try:
        fresh = _get_fresh_candidates()
        rule_candidates_count = len(fresh)
        pending_review_count = len(
            [c for c in fresh if c.get("status") == "pending_review"]
        )
        approved_rules_count = len(
            [c for c in fresh if c.get("status") == "approved"]
        )
    except Exception as e:
        logger.warning("PatternAnalyzer failed in stats, using store: %s", e)
        rule_candidates_count = len(_learning_store["rule_candidates"])
        pending_review_count = len(
            [r for r in _learning_store["rule_candidates"] if r["status"] == "pending_review"]
        )
        approved_rules_count = len(
            [r for r in _learning_store["rule_candidates"] if r["status"] == "approved"]
        )

    result = {
        "status": "ok",
        "total_queries": _learning_store["total_queries"],
        "total_feedback": _learning_store["total_feedback"],
        "avg_rating": _learning_store["avg_rating"],
        "rule_candidates_count": rule_candidates_count,
        "pending_review_count": pending_review_count,
        "approved_rules_count": approved_rules_count,
    }

    # If in-memory has no data yet, try MySQL as fallback
    if _learning_store["total_queries"] == 0:
        try:
            from app.storage.mysql_store import get_store
            store = get_store()
            mysql_stats = await store.get_learning_stats()
            if mysql_stats and mysql_stats.get("total_queries", 0) > 0:
                result.update(mysql_stats)
        except Exception:
            pass

    return result


@router.get("/learning/query-pairs")
async def query_pairs(limit: int = 20, domain: str = "") -> dict:
    """Get historical NL-SQL query pairs.

    Uses in-memory store (populated by real-time record_query calls) as the
    primary source, with MySQL as fallback for persistence.
    """
    # Primary: use in-memory store (real-time data)
    pairs = _learning_store["query_pairs"]
    if domain:
        pairs = [p for p in pairs if p.get("domain") == domain]
    if pairs:
        return {
            "status": "ok",
            "pairs": list(reversed(pairs[-limit:])),
            "total": len(pairs),
        }

    # Fallback: try MySQL
    try:
        from app.storage.mysql_store import get_store
        store = get_store()
        if store is not None:
            pairs = await store.get_query_pairs(limit=limit, domain=domain)
            if pairs:
                return {
                    "status": "ok",
                    "pairs": pairs,
                    "total": len(pairs),
                }
    except Exception:
        pass

    return {
        "status": "ok",
        "pairs": [],
        "total": 0,
    }


@router.get("/learning/rule-candidates")
async def rule_candidates(status: str = "pending_review") -> dict:
    """Get rule candidates from feedback patterns."""
    try:
        candidate_dicts = _get_fresh_candidates()

        if not candidate_dicts:
            return {"status": "ok", "candidates": [], "total": 0}

        # Update in-memory store: replace stale entries with fresh ones,
        # keeping reviewed (approved/rejected) entries that PatternAnalyzer
        # no longer finds, so their review status persists.
        fresh_ids = {d.get("id", "") for d in candidate_dicts}
        kept_reviewed = [
            r for r in _learning_store["rule_candidates"]
            if r.get("id", "") not in fresh_ids
            and r.get("status") in ("approved", "rejected")
        ]
        # Preserve rejected_source_count from old entries so the
        # "new evidence" check survives store refreshes.
        old_by_id = {
            (r.get("id") or r.get("rule_id", "")): r
            for r in _learning_store["rule_candidates"]
        }
        fresh_entries = []
        for d in candidate_dicts:
            rid = d.get("id", "")
            entry = {
                "id": rid,
                "description": d.get("description", ""),
                "pattern": d.get("pattern", ""),
                "sql_template": d.get("sql_template", ""),
                "confidence": d.get("confidence", 0.0),
                "source_count": d.get("source_count", 0),
                "status": d.get("status", "pending_review"),
                "domain_id": d.get("domain", d.get("domain_id", "")),
            }
            old = old_by_id.get(rid, {})
            if "rejected_source_count" in old:
                entry["rejected_source_count"] = old["rejected_source_count"]
            fresh_entries.append(entry)
        _learning_store["rule_candidates"] = kept_reviewed + fresh_entries

        # Filter by requested status
        filtered = [d for d in candidate_dicts if d.get("status") == status]
        return {
            "status": "ok",
            "candidates": filtered,
            "total": len(filtered),
        }
    except Exception as e:
        logger.warning("Pattern analyzer failed, falling back: %s", e)

    # Fallback: try MySQL (only when PatternAnalyzer itself crashed)
    try:
        store = get_store()
        if store is not None:
            candidates = await store.get_rule_candidates(status=status)
            if candidates:
                # Remap MySQL columns to frontend-friendly keys
                for c in candidates:
                    if "rule_id" in c and "id" not in c:
                        c["id"] = c.pop("rule_id")
                return {
                    "status": "ok",
                    "candidates": candidates,
                    "total": len(candidates),
                }
    except Exception:
        pass

    # Fallback: in-memory store (only when PatternAnalyzer crashed)
    candidates = [
        r for r in _learning_store["rule_candidates"] if r["status"] == status
    ]
    return {
        "status": "ok",
        "candidates": candidates,
        "total": len(candidates),
    }


@router.post("/learning/rule-candidates/review")
async def review_rule_candidate(rule_id: str, action: str = "approve") -> dict:
    """Approve or reject a rule candidate."""
    if action not in ("approve", "reject"):
        return {"status": "error", "message": f"Invalid action: {action}"}

    new_status = "approved" if action == "approve" else "rejected"

    # Helper: persist review decision to MySQL (save-then-update)
    async def _persist_review(rid: str, st: str, info: dict[str, Any]) -> None:
        try:
            store = get_store()
            if store is not None:
                # Save first (creates row if missing), then update status
                await store.save_rule_candidate({
                    "id": rid,
                    "description": info.get("description", ""),
                    "pattern": info.get("pattern", ""),
                    "sql_template": info.get("sql_template", ""),
                    "confidence": info.get("confidence", 0.0),
                    "source_count": info.get("source_count", 0),
                    "status": st,
                    "domain": info.get("domain", info.get("domain_id", "")),
                })
                updated = await store.update_rule_candidate_status(rid, st)
                if not updated:
                    logger.warning("MySQL update_rule_candidate_status returned False for %s", rid)
        except Exception as e:
            logger.warning("Failed to persist review to MySQL for %s: %s", rid, e)

    # Tier 1: Check in-memory store
    for r in _learning_store["rule_candidates"]:
        rid = r.get("id") or r.get("rule_id", "")
        if rid == rule_id:
            r["status"] = new_status
            # Record source_count at rejection time so we can detect new
            # evidence later (source_count increases when new feedback
            # supports the same pattern).
            if new_status == "rejected":
                r["rejected_source_count"] = r.get("source_count", 0)
            await _persist_review(rule_id, new_status, r)
            return {
                "status": "ok",
                "message": "Rule approved" if action == "approve" else "Rule rejected",
                "rule": r,
            }

    # Tier 2: Try pattern analyzer (ephemeral objects — persist to MySQL)
    try:
        from app.learning.pattern_analyzer import PatternAnalyzer
        from app.learning.feedback_collector import feedback_collector

        analyzer = PatternAnalyzer(feedback_collector)
        candidates = analyzer.get_review_queue()
        for c in candidates:
            if c.rule_id == rule_id:
                c.status = new_status
                rule_info = {
                    "id": c.rule_id,
                    "description": c.pattern_description,
                    "pattern": c.suggested_pattern,
                    "sql_template": c.suggested_pattern or "",
                    "confidence": c.confidence,
                    "source_count": c.occurrence_count,
                    "status": c.status,
                    "domain": c.domain_id or "",
                }
                if new_status == "rejected":
                    rule_info["rejected_source_count"] = c.occurrence_count
                await _persist_review(rule_id, new_status, rule_info)
                # Also add to in-memory store so it survives this session
                _learning_store["rule_candidates"].append(rule_info)
                return {
                    "status": "ok",
                    "message": "Rule approved" if action == "approve" else "Rule rejected",
                    "rule": rule_info,
                }
    except Exception as e:
        logger.warning("Pattern analyzer review failed for %s: %s", rule_id, e)

    # Tier 3: Try MySQL directly (candidate may exist there but not in memory)
    try:
        store = get_store()
        if store is not None:
            updated = await store.update_rule_candidate_status(rule_id, new_status)
            if updated:
                return {
                    "status": "ok",
                    "message": "Rule approved" if action == "approve" else "Rule rejected",
                    "rule": {"id": rule_id, "status": new_status},
                }
    except Exception as e:
        logger.warning("MySQL direct review failed for %s: %s", rule_id, e)

    return {"status": "error", "message": f"Rule candidate {rule_id} not found"}


@router.get("/learning/quality")
async def quality_trends(days: int = 30) -> dict:
    """Get quality trend data.

    Uses in-memory store (populated by real-time record_query calls) as the
    primary source, with feedback collector as enrichment for accuracy data.
    """
    # Try to enrich trends with accuracy data from feedback collector
    try:
        from app.learning.feedback_collector import feedback_collector
        from datetime import UTC, datetime, timedelta

        # Get feedback stats from the collector
        recent_records = feedback_collector.get_recent(limit=500)
        if recent_records:
            # Group ratings by date
            from collections import defaultdict
            daily_ratings: dict[str, list[int]] = defaultdict(list)
            daily_feedback_count: dict[str, int] = defaultdict(int)
            cutoff = datetime.now(UTC) - timedelta(days=days)

            for rec in recent_records:
                try:
                    ts = datetime.fromisoformat(rec.get("created_at", ""))
                    if ts < cutoff:
                        continue
                except (ValueError, TypeError):
                    continue
                date_str = ts.strftime("%Y-%m-%d")
                rating = rec.get("rating", 0)
                if rating > 0:
                    daily_ratings[date_str].append(rating)
                daily_feedback_count[date_str] += 1

            # Merge with in-memory trends
            for trend in _learning_store["quality_trends"]:
                date = trend["date"]
                if date in daily_ratings and daily_ratings[date]:
                    avg_rating = sum(daily_ratings[date]) / len(daily_ratings[date])
                    trend["accuracy"] = round(avg_rating / 5.0, 3)  # normalize to 0-1
                    trend["feedback_count"] = daily_feedback_count.get(date, 0)
    except Exception:
        pass

    # Primary: use in-memory learning store trends (populated by real queries)
    if _learning_store["quality_trends"]:
        trends = _learning_store["quality_trends"]
        return {
            "status": "ok",
            "trends": trends,
            "days": days,
            "summary": {
                "current_accuracy": trends[-1].get("accuracy", 0) if trends else 0.0,
                "total_queries": sum(t.get("query_count", 0) for t in trends),
                "total_feedback": sum(t.get("feedback_count", 0) for t in trends),
            },
        }

    # Fallback: try MySQL for historical data
    try:
        from app.storage.mysql_store import get_store
        store = get_store()
        if store is not None:
            trends = await store.get_quality_trends(days=days)
            if trends:
                return {
                    "status": "ok",
                    "trends": trends,
                    "days": days,
                    "summary": {
                        "current_accuracy": trends[-1].get("accuracy", 0) if trends else 0.0,
                        "total_queries": sum(t.get("query_count", 0) for t in trends),
                        "total_feedback": sum(t.get("feedback_count", 0) for t in trends),
                    },
                }
    except Exception:
        pass

    return {
        "status": "ok",
        "trends": [],
        "days": days,
        "summary": {
            "current_accuracy": 0.0,
            "total_queries": 0,
            "total_feedback": 0,
        },
    }


@router.post("/learning/rag/refresh")
async def refresh_rag_index(db_id: str | None = None) -> dict:
    """Manually trigger RAG index refresh for all or a specific database.

    Args:
        db_id: Optional database ID to refresh. If omitted, refreshes all.
    """
    try:
        from app.rag import get_index_refresher

        refresher = get_index_refresher()
        if db_id:
            success = await refresher.refresh(db_id)
            return {
                "status": "ok" if success else "error",
                "db_id": db_id,
                "refreshed": success,
                "refreshed_at": datetime.now().isoformat(),
            }
        else:
            results = await refresher.refresh_all()
            return {
                "status": "ok",
                "databases_refreshed": results,
                "count": sum(1 for v in results.values() if v),
                "refreshed_at": datetime.now().isoformat(),
            }
    except Exception as e:
        return {
            "status": "error",
            "message": f"RAG refresh failed: {e}",
            "refreshed_at": datetime.now().isoformat(),
        }


@router.get("/learning/rag/stats")
async def rag_stats() -> dict:
    """Get RAG index statistics across all namespaces."""
    try:
        from app.rag import get_schema_rag, get_metric_rag, get_document_store

        schema_stats = get_schema_rag().stats()
        metric_stats = get_metric_rag().stats()
        doc_stats = get_document_store().stats()
        return {
            "status": "ok",
            "schema": schema_stats,
            "metrics": metric_stats,
            "documents": doc_stats,
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}


@router.get("/learning/rag/status")
async def rag_status() -> dict:
    """Get RAG index refresh status."""
    try:
        from app.rag import get_index_refresher

        return {
            "status": "ok",
            **get_index_refresher().get_refresh_status(),
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}


@router.post("/learning/rag/clear")
async def clear_rag_index(body: dict | None = None) -> dict:
    """Clear RAG indices to remove stale data from previously-disconnected databases.

    Request body (all optional):
        {
            "scope": "all" | "schema" | "metrics" | "documents"  # default: "all"
        }

    This is destructive: it drops all indexed schema/metric/document records
    from both LanceDB and BM25 backends. Use when stale entries from old
    database connections pollute search results.
    """
    scope = "all"
    if isinstance(body, dict):
        scope = str(body.get("scope", "all")).lower().strip()

    cleared: dict[str, dict[str, int]] = {}
    try:
        from app.rag import get_schema_rag, get_metric_rag, get_document_store

        if scope in ("all", "schema"):
            before = get_schema_rag().stats()
            get_schema_rag().clear()
            after = get_schema_rag().stats()
            cleared["schema"] = {
                "before_lancedb": before.get("lancedb_count", 0),
                "before_bm25": before.get("bm25_count", 0),
                "after_lancedb": after.get("lancedb_count", 0),
                "after_bm25": after.get("bm25_count", 0),
            }

        if scope in ("all", "metrics"):
            before = get_metric_rag().stats()
            get_metric_rag().clear()
            after = get_metric_rag().stats()
            cleared["metrics"] = {
                "before_lancedb": before.get("lancedb_count", 0),
                "before_bm25": before.get("bm25_count", 0),
                "after_lancedb": after.get("lancedb_count", 0),
                "after_bm25": after.get("bm25_count", 0),
            }

        if scope in ("all", "documents"):
            before = get_document_store().stats()
            get_document_store().clear()
            after = get_document_store().stats()
            cleared["documents"] = {
                "before_lancedb": before.get("lancedb_count", 0),
                "before_bm25": before.get("bm25_count", 0),
                "after_lancedb": after.get("lancedb_count", 0),
                "after_bm25": after.get("bm25_count", 0),
            }

        # Reset IndexRefresher's version cache so subsequent refreshes
        # re-extract schema instead of skipping due to stale hash match.
        try:
            from app.rag import get_index_refresher
            refresher = get_index_refresher()
            refresher._version_cache.clear()
            refresher._last_refresh.clear()
        except Exception:
            pass

        return {
            "status": "ok",
            "scope": scope,
            "cleared": cleared,
            "cleared_at": datetime.now().isoformat(),
        }
    except Exception as e:
        return {
            "status": "error",
            "message": f"RAG clear failed: {e}",
            "cleared_at": datetime.now().isoformat(),
        }
