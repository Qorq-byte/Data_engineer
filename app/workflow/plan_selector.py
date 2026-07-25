r"""Plan Selector — 3-layer routing for workflow plan selection.

See implementation-plan §4.9.2 and progress-log §3.17 for the full specification.

The plan selector determines which workflow plan template to use for a given
natural-language query.  It applies a three-layer decision strategy:

======  ======  =============================================================
Layer   Type    Logic
======  ======  =============================================================
L1      Rules   Keyword/pattern matching against query text.
                Fast, deterministic, first line of defence.
L2      History Look up similar past queries in a SQLite-backed
                ``PlanHistoryStore`` and reuse the plan they resolved to.
L3      LLM     Lightweight LLM classification via ``LiteLLMRouter``.
                Fallback when rules and history are inconclusive.
======  ======  =============================================================

Usage::

    from app.harness.plan_loader import PlanLoader
    from app.workflow.plan_selector import PlanSelector

    loader = PlanLoader("app/workflow/plans")
    selector = PlanSelector(plan_loader=loader)

    result = await selector.select("monthly sales by region")
    # SelectionResult(plan_id="ez_query", layer="L1", reason="simple single-table query")
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

# ── Selection result ──────────────────────────────────────────────────


@dataclass
class SelectionResult:
    """Structured output from plan selection.

    Attributes:
        plan_id: The selected workflow plan ID.
        layer: Which layer made the decision (``"L1"``, ``"L2"``, ``"L3"``, ``"default"``).
        reason: Human-readable explanation of the decision.
        confidence: Confidence score (0.0–1.0).
        alternatives: Other candidate plan IDs that were considered.
    """

    plan_id: str
    layer: str = "default"
    reason: str = ""
    confidence: float = 1.0
    alternatives: list[str] = field(default_factory=list)


# ── Constants ─────────────────────────────────────────────────────────

# Default plan when no rules match and no history/LLM available
DEFAULT_PLAN = "gensql_agentic"

# L1 patterns: (plan_id, regex_pattern, reason)
# Ordered by priority — first match wins.
# **Complex patterns (gensql_agentic) MUST come before simple ones (ez_query).**
L1_RULES: list[tuple[str, str, str]] = [
    # ── gensql_agentic: complex queries (checked FIRST) ────────────
    (
        "gensql_agentic",
        r"(?i)\bJOIN\b",
        "query contains JOIN — needs schema linking",
    ),
    (
        "gensql_agentic",
        r"(?i)\bGROUP\s+BY\b",
        "aggregation with GROUP BY — needs validation",
    ),
    (
        "gensql_agentic",
        r"(?i)\bHAVING\b",
        "HAVING clause — needs full pipeline",
    ),
    (
        "gensql_agentic",
        r"(?i)\bUNION\b|\bINTERSECT\b|\bEXCEPT\b",
        "set operation — needs full validation",
    ),
    (
        "gensql_agentic",
        r"(?i)\bSUBQUERY\b|\b子查询\b|\(\s*SELECT\b",
        "subquery — needs full pipeline",
    ),
    (
        "gensql_agentic",
        r"(?i)\bCASE\s+WHEN\b|\bWINDOW\b|\bOVER\s*\(|ROW_NUMBER|RANK\s*\(|DENSE_RANK",
        "window function or CASE expression — needs full pipeline",
    ),
    (
        "gensql_agentic",
        r"[一-鿿]*?(?:关联|多表|跨表|联合|连接)[一-鿿]*",
        "Chinese keyword indicating join/multi-table — needs schema linking",
    ),
    (
        "gensql_agentic",
        r"[一-鿿]*?(?:复杂|分析|对比|比较|同比|环比|占比)[一-鿿]*",
        "Chinese keyword indicating complex analysis — needs full pipeline",
    ),

    # ── ez_query: simple single-table queries ──────────────────────
    (
        "ez_query",
        r"(?i)^(?:SELECT\s+)?\*\s+FROM\s+[\w一-鿿]+\s*$",
        "simple SELECT * (no aggregation, no join)",
    ),
    (
        "ez_query",
        r"(?i)^(?:SELECT\s+)?[\w一-鿿]+(,\s*[\w一-鿿]+)*\s+FROM\s+[\w一-鿿]+\s*$",
        "simple column list, single table, no filter",
    ),
    (
        "ez_query",
        r"(?i)^(?:查看|显示|列出|show|list|display|get)\s*[\w一-鿿]+",
        "simple list/display request",
    ),
    (
        "ez_query",
        r"(?i)^(?:SELECT\s+)?COUNT\(\*\)",
        "simple COUNT(*) query",
    ),
    (
        "ez_query",
        r"(?i)^\s*(?:什么|哪个|多少|怎么|how|what|which|when|who)",
        "simple question — likely ez_query",
    ),
]


# ── LLM response helpers (L3) ─────────────────────────────────────────


def _extract_response_text(response: Any) -> str:
    """Extract the first choice's text from an LLM router response.

    Handles both dict-shaped responses (mock mode / OpenAI wire format)
    and object-shaped responses (litellm ``ModelResponse``).
    """
    if response is None:
        return ""
    if isinstance(response, dict):
        choices = response.get("choices") or []
    else:
        choices = getattr(response, "choices", None) or []
    if not choices:
        return ""
    first = choices[0]
    message = first.get("message") if isinstance(first, dict) else getattr(first, "message", None)
    if message is None:
        return ""
    content = (
        message.get("content") if isinstance(message, dict) else getattr(message, "content", "")
    )
    return str(content) if content else ""


def _parse_plan_id(text: str, available: set[str]) -> str | None:
    """Find an available plan ID mentioned in *text*.

    Case-insensitive containment match — tolerates surrounding prose.
    When several plan IDs appear, the earliest occurrence wins (longer
    IDs win ties, so substring-shaped IDs don't shadow longer ones).

    Returns ``None`` if no available plan ID is found.
    """
    if not text:
        return None
    lowered = text.lower()
    hits = [
        (lowered.find(pid.lower()), -len(pid), pid)
        for pid in sorted(available)
        if pid.lower() in lowered
    ]
    if not hits:
        return None
    hits.sort()
    return hits[0][2]


# ── PlanSelector ──────────────────────────────────────────────────────


class PlanSelector:
    """3-layer plan router: rules → history → LLM.

    Args:
        plan_loader: ``PlanLoader`` instance for listing available plans.
        rule_engine: Optional ``RuleEngine`` (unused in L1; reserved for
                     rule-based enforcement during plan selection).
        history_store: Optional ``PlanHistoryStore`` (see
                       ``app.workflow.history_store``) for L2 similarity
                       matching against past queries.  If ``None``, L2 is
                       skipped.
        llm_router: Optional ``LiteLLMRouter`` for L3 classification.  The
                    router is asked to pick one plan ID from the available
                    set.  If ``None``, L3 is skipped and selection falls
                    through to ``DEFAULT_PLAN``.
    """

    def __init__(
        self,
        plan_loader: Any = None,
        rule_engine: Any = None,
        history_store: Any = None,
        llm_router: Any = None,
    ) -> None:
        self._plan_loader = plan_loader
        self._rule_engine = rule_engine
        self._history_store = history_store
        self._llm_router = llm_router
        # Phase 5.9: adaptive optimization
        self._workflow_stats: dict[str, Any] = {}

    # ── Public API ───────────────────────────────────────────────────

    async def select(
        self,
        query_text: str,
        domain: str | None = None,
    ) -> SelectionResult:
        """Select the best workflow plan for *query_text*.

        Applies the three-layer routing strategy and returns a
        ``SelectionResult`` with the chosen ``plan_id`` and metadata.

        Args:
            query_text: The user's natural-language query.
            domain: Optional domain hint for context-aware selection.

        Returns:
            ``SelectionResult`` with the selected plan ID and decision metadata.
        """
        q = query_text.strip()

        # ── L1: Rule-based ─────────────────────────────────────────
        result = self._select_by_rules(q)
        if result is not None:
            return result

        # ── L2: History-based ──────────────────────────────────────
        result = await self._select_by_history(q, domain)
        if result is not None:
            return result

        # ── L3: LLM classification ─────────────────────────────────
        result = await self._select_by_llm(q, domain)
        if result is not None:
            return result

        # ── Fallback ───────────────────────────────────────────────
        return SelectionResult(
            plan_id=DEFAULT_PLAN,
            layer="default",
            reason="No rule/history/LLM match — using default plan",
            confidence=0.3,
        )

    def select_sync(
        self,
        query_text: str,
        domain: str | None = None,
    ) -> SelectionResult:
        """Synchronous wrapper for :meth:`select`.

        Only L1 (rules) is used; L2/L3 are skipped.  Useful when no async
        context is available (e.g., simple CLI tools).
        """
        q = query_text.strip()
        result = self._select_by_rules(q)
        if result is not None:
            return result
        return SelectionResult(
            plan_id=DEFAULT_PLAN,
            layer="default",
            reason="Sync mode — rules only, no history/LLM",
            confidence=0.3,
        )

    # ── L1: Rule-based ──────────────────────────────────────────────

    def _select_by_rules(self, query_text: str) -> SelectionResult | None:
        """Match *query_text* against L1 keyword/pattern rules.

        Returns a ``SelectionResult`` for the first matching rule whose
        ``plan_id`` is available in the plan loader, or ``None`` if no
        rule matches.
        """
        available = self._available_plans()

        for plan_id, pattern, reason in L1_RULES:
            if plan_id not in available:
                continue
            if re.search(pattern, query_text):
                return SelectionResult(
                    plan_id=plan_id,
                    layer="L1",
                    reason=reason,
                    confidence=0.9,
                    alternatives=self._other_plans(plan_id, available),
                )

        return None

    # ── L2: History-based ───────────────────────────────────────────

    async def _select_by_history(
        self,
        query_text: str,
        domain: str | None = None,
    ) -> SelectionResult | None:
        """Look up similar past queries in the history store.

        Queries the ``PlanHistoryStore`` for exact or near-match (Jaccard
        token overlap) historical queries and returns the plan with the
        most successful runs among them.  Plans not currently available
        in the plan loader are ignored.

        Returns:
            ``SelectionResult`` (layer ``"L2"``) if similar history exists,
            or ``None`` if no store is configured, no similar queries are
            found, or the store raises.
        """
        if self._history_store is None:
            return None

        try:
            matches = self._history_store.find_similar(query_text, domain=domain, limit=5)
        except Exception:
            return None
        if not matches:
            return None

        available = self._available_plans()
        success_counts: dict[str, int] = {}
        for m in matches:
            plan_id = getattr(m, "plan_id", None)
            if plan_id in available and getattr(m, "success", False):
                success_counts[plan_id] = success_counts.get(plan_id, 0) + 1
        if not success_counts:
            return None

        # Most successful plan wins; ties break alphabetically (deterministic)
        best = max(sorted(success_counts), key=lambda pid: success_counts[pid])
        n = success_counts[best]
        return SelectionResult(
            plan_id=best,
            layer="L2",
            reason=(
                f"Based on {n} similar historical "
                f"quer{'y' if n == 1 else 'ies'} that used plan '{best}'"
            ),
            confidence=0.75,
            alternatives=self._other_plans(best, available),
        )

    # ── L3: LLM classification ──────────────────────────────────────

    async def _select_by_llm(
        self,
        query_text: str,
        domain: str | None = None,
    ) -> SelectionResult | None:
        """Use a lightweight LLM to classify the query into a plan.

        Sends the query plus the list of available plans (with their
        names/descriptions from the plan loader when loadable) to the
        LLM router and parses the response for a known plan ID.  Matching
        is case-insensitive containment, so extra prose around the plan
        ID is tolerated.

        Returns:
            ``SelectionResult`` (layer ``"L3"``) if the LLM names a known
            plan, or ``None`` if no router is configured, the response
            cannot be parsed, or any error occurs (falls through to the
            default plan).
        """
        if self._llm_router is None:
            return None

        try:
            available = self._available_plans()
            prompt = self._build_llm_prompt(query_text, available, domain)
            response = await self._llm_router.complete(
                messages=[{"role": "user", "content": prompt}],
                temperature=0.0,
            )
            text = _extract_response_text(response)
            plan_id = _parse_plan_id(text, available)
            if plan_id is None:
                return None
            return SelectionResult(
                plan_id=plan_id,
                layer="L3",
                reason=f"LLM classified the query as plan '{plan_id}'",
                confidence=0.6,
                alternatives=self._other_plans(plan_id, available),
            )
        except Exception:
            return None

    def _build_llm_prompt(
        self,
        query_text: str,
        available: set[str],
        domain: str | None,
    ) -> str:
        """Build the L3 classification prompt listing available plans."""
        lines: list[str] = []
        for pid in sorted(available):
            desc = pid
            if self._plan_loader is not None:
                try:
                    plan = self._plan_loader.load(pid)
                    desc = (
                        getattr(plan, "description", "") or getattr(plan, "name", "") or pid
                    )
                except Exception:
                    desc = pid
            lines.append(f"- {pid}: {desc}")

        domain_line = f"Domain: {domain}\n" if domain else ""
        return (
            "You are a workflow plan router for an NL2SQL agent.\n"
            "Pick exactly ONE plan ID from the list below that best fits the query.\n\n"
            "Available plans:\n"
            + "\n".join(lines)
            + f"\n\n{domain_line}Query: {query_text}\n\n"
            "Reply with the plan ID only — no explanation."
        )

    # ── Adaptive optimization (Phase 5.9) ──────────────────────────

    def record_outcome(
        self,
        plan_id: str,
        edit_count: int = 0,
        validation_passed: bool = True,
        user_rating: int = 0,
    ) -> None:
        """Record a workflow outcome for adaptive optimization.

        Args:
            plan_id: The workflow plan that was used.
            edit_count: Number of user edits applied to the generated SQL.
            validation_passed: Whether validation succeeded.
            user_rating: User star rating (1–5). 0 = no rating.
        """
        metrics = self._workflow_stats.setdefault(
            plan_id,
            {
                "total_runs": 0,
                "total_edits": 0,
                "validation_passes": 0,
                "total_rating": 0,
                "rating_count": 0,
                "recent_history": [],
            },
        )
        metrics["total_runs"] += 1
        metrics["total_edits"] += edit_count
        if validation_passed:
            metrics["validation_passes"] += 1
        if user_rating > 0:
            metrics["total_rating"] += user_rating
            metrics["rating_count"] += 1
        metrics["recent_history"].append(
            {
                "edit_count": edit_count,
                "validation_passed": validation_passed,
                "rating": user_rating,
            }
        )
        # Keep last 100
        if len(metrics["recent_history"]) > 100:
            metrics["recent_history"] = metrics["recent_history"][-100:]

    def get_workflow_stats(self) -> dict[str, Any]:
        """Return per-plan workflow statistics.

        Returns:
            Dict mapping plan_id → stats dict with ``avg_edit_rate``,
            ``validation_pass_rate``, ``avg_rating``, ``total_runs``.
        """
        result: dict[str, Any] = {}
        for plan_id, m in self._workflow_stats.items():
            total = m["total_runs"]
            result[plan_id] = {
                "total_runs": total,
                "avg_edit_rate": round(m["total_edits"] / max(total, 1), 2),
                "validation_pass_rate": round(
                    m["validation_passes"] / max(total, 1) * 100, 1
                ),
                "avg_rating": round(
                    m["total_rating"] / max(m["rating_count"], 1), 2
                ),
            }
        return result

    def optimize_rules(self) -> list[str]:
        """Analyze workflow metrics and return suggested routing changes.

        Returns:
            List of human-readable optimization suggestions.
        """
        suggestions: list[str] = []
        stats = self.get_workflow_stats()

        for plan_id, s in stats.items():
            if s["total_runs"] < 10:
                continue

            if s["avg_edit_rate"] > 0.3:
                # Find a better plan for similar queries
                better = [
                    pid
                    for pid, ps in stats.items()
                    if pid != plan_id and ps["avg_edit_rate"] < s["avg_edit_rate"]
                ]
                if better:
                    suggestions.append(
                        f"Plan '{plan_id}' has high edit rate ({s['avg_edit_rate']:.0%}): "
                        f"consider redirecting similar queries to '{better[0]}' "
                        f"(edit rate: {stats[better[0]]['avg_edit_rate']:.0%})"
                    )

            if s["avg_rating"] < 3.0 and s["total_runs"] >= 10:
                suggestions.append(
                    f"Plan '{plan_id}' has low average rating ({s['avg_rating']:.1f}/5): "
                    f"review prompt quality or model selection"
                )

        return suggestions

    def get_optimization_suggestions(self) -> list[str]:
        """Human-readable optimization suggestions (alias for ``optimize_rules``)."""
        return self.optimize_rules()

    # ── Helpers ─────────────────────────────────────────────────────

    def _available_plans(self) -> set[str]:
        """Return the set of plan IDs currently loadable."""
        if self._plan_loader is not None:
            try:
                return set(self._plan_loader.list_available())
            except Exception:
                pass
        return {DEFAULT_PLAN}

    @staticmethod
    def _other_plans(selected: str, available: set[str]) -> list[str]:
        """Return sorted list of other available plans besides *selected*."""
        return sorted(a for a in available if a != selected)
