"""Feedback Agent — collects user feedback and triggers learning pipeline.

See SPEC §4.11.2 (agent role: Feedback) and implementation plan §4.13.1.

Phase 4: stub — validates and acknowledges feedback payloads.
Full persistence and learning pipeline trigger deferred to Phase 5.
"""

from __future__ import annotations

import difflib
import time
from dataclasses import dataclass, field
from typing import Any

from app.agents.base import AgentResult, BaseAgent


@dataclass
class FeedbackRecord:
    """A single user feedback entry (Phase 4: in-memory only)."""

    turn_id: str = ""
    rating: int = 0  # 1–5
    comment: str = ""
    original_sql: str = ""
    final_sql: str = ""
    diff_ops: list[dict[str, str]] = field(default_factory=list)
    domain_id: str = ""
    timestamp: str = ""


class FeedbackAgent(BaseAgent):
    """Agent that collects, validates, and stores user feedback.

    Phase 4: validates payload format and returns acknowledgment.
    Phase 5: persists to ``feedback_log`` table + triggers learning pipeline.

    Usage::

        agent = FeedbackAgent()
        result = await agent.record_feedback(
            turn_id="turn_123",
            rating=4,
            comment="Good query but wrong date range",
        )
    """

    name = "feedback_processor"
    description = (
        "Collect user feedback (rating, comments, SQL diffs) and "
        "trigger the continuous learning pipeline."
    )

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        super().__init__(config)
        self._records: list[FeedbackRecord] = []  # Phase 4: in-memory only

    async def execute(self, input: Any, context: dict[str, Any]) -> AgentResult:
        """Validate and record feedback.

        Args:
            input: dict with keys:
                - ``rating`` (int) — 1–5 star rating (required)
                - ``comment`` (str) — optional free-text feedback
                - ``turn_id`` (str) — optional conversation turn ID
                - ``original_sql`` (str) — SQL before user edits
                - ``final_sql`` (str) — SQL after user edits
            context: Shared dict (session_id, domain_id, etc.).

        Returns:
            AgentResult.data = {"acknowledged": True, "record_count": N}
        """
        t0 = time.perf_counter()
        try:
            if not isinstance(input, dict):
                return self._error(
                    f"FeedbackAgent expects a dict input, got {type(input).__name__}",
                    latency_ms=0.0,
                )

            rating = int(input.get("rating", 0))
            if not (1 <= rating <= 5):
                return self._error(
                    f"Rating must be 1–5, got {rating}",
                    latency_ms=(time.perf_counter() - t0) * 1000,
                )

            comment = str(input.get("comment", ""))
            turn_id = str(input.get("turn_id", ""))
            original_sql = str(input.get("original_sql", ""))
            final_sql = str(input.get("final_sql", ""))

            # Compute diff if both SQLs provided
            diff_ops: list[dict[str, str]] = []
            if original_sql and final_sql and original_sql != final_sql:
                diff_ops = self._compute_diff(original_sql, final_sql)

            record = FeedbackRecord(
                turn_id=turn_id,
                rating=rating,
                comment=comment,
                original_sql=original_sql,
                final_sql=final_sql,
                diff_ops=diff_ops,
                domain_id=str(context.get("domain_id", "")),
                timestamp=str(time.time()),
            )
            self._records.append(record)

            elapsed = (time.perf_counter() - t0) * 1000
            return self._ok(
                {
                    "acknowledged": True,
                    "rating": rating,
                    "record_count": len(self._records),
                },
                latency_ms=round(elapsed, 2),
                has_diff=len(diff_ops) > 0,
            )
        except Exception as exc:
            elapsed = (time.perf_counter() - t0) * 1000
            return self._error(str(exc), latency_ms=round(elapsed, 2))

    # ── Convenience methods ────────────────────────────────────────────

    async def record_feedback(
        self,
        turn_id: str = "",
        rating: int = 0,
        comment: str = "",
        original_sql: str = "",
        final_sql: str = "",
    ) -> AgentResult:
        """Record feedback with keyword arguments (convenience wrapper)."""
        return await self.execute(
            {
                "turn_id": turn_id,
                "rating": rating,
                "comment": comment,
                "original_sql": original_sql,
                "final_sql": final_sql,
            },
            {},
        )

    async def extract_diff(self, original_sql: str, final_sql: str) -> list[dict[str, str]]:
        """Compute and return a diff between two SQL strings."""
        return self._compute_diff(original_sql, final_sql)

    # ── helpers ────────────────────────────────────────────────────────

    @staticmethod
    def _compute_diff(original: str, revised: str) -> list[dict[str, str]]:
        """Compute a line-by-line unified diff between two SQL strings."""
        diff_lines = list(
            difflib.unified_diff(
                original.splitlines(keepends=True),
                revised.splitlines(keepends=True),
                fromfile="original",
                tofile="revised",
            )
        )
        ops: list[dict[str, str]] = []
        for line in diff_lines:
            if line.startswith("---") or line.startswith("+++") or line.startswith("@@"):
                continue
            if line.startswith("-"):
                ops.append({"op": "remove", "line": line[1:].rstrip("\n")})
            elif line.startswith("+"):
                ops.append({"op": "add", "line": line[1:].rstrip("\n")})
            elif line.startswith(" "):
                ops.append({"op": "keep", "line": line[1:].rstrip("\n")})
        return ops

    # ── Inspection ─────────────────────────────────────────────────────

    @property
    def record_count(self) -> int:
        return len(self._records)

    def reset(self) -> None:
        """Clear all stored feedback records."""
        super().reset()
        self._records.clear()
