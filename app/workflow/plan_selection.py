"""Capability-filtered plan selection for the request path.

``PlanSelector`` answers "which template fits this question".  This module adds
the operational half: a fixed plan catalog, a conservative auto-selectable set,
a process-wide history store, and a decision record the API can return.

Automatic routing intentionally covers only ``gensql_agentic`` and
``ez_query``.  ``reflection``, ``chat_agentic`` and ``explore`` change cost and
output shape, so they stay explicit ``plan_id`` choices.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.harness.plan_loader import PlanLoader
from app.workflow.history_store import PlanHistoryStore
from app.workflow.plan_runtime import (
    AUTO_SELECTABLE,
    DEFAULT_PLAN,
    executable_plan_ids,
    plan_blocker,
)
from app.workflow.plan_selector import PlanSelector

_ROOT = Path(__file__).resolve().parents[1]
_PLANS_DIR = _ROOT / "workflow/plans"

# Selector verdicts are plan-template names.  At the API boundary the full
# pipeline includes NL parsing, so ``gensql_agentic`` maps to ``request_query``.
_SELECTOR_TO_API: dict[str, str] = {
    "gensql_agentic": "request_query",
    "request_query": "request_query",
    "ez_query": "ez_query",
}


@dataclass
class PlanDecision:
    """A plan choice with enough provenance to explain and replay it."""

    plan_id: str
    layer: str = "default"
    reason: str = ""
    confidence: float = 1.0
    alternatives: list[str] = field(default_factory=list)
    auto: bool = True
    requested: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "plan_id": self.plan_id,
            "layer": self.layer,
            "reason": self.reason,
            "confidence": self.confidence,
            "alternatives": list(self.alternatives),
            "auto": self.auto,
            "requested": self.requested,
        }


# ── Process-wide selector ──────────────────────────────────────────────


class _SelectionRuntime:
    """Lazily built loader/selector/history trio shared by every request."""

    def __init__(self) -> None:
        self.loader: PlanLoader | None = None
        self.history: PlanHistoryStore | None = None
        self.selector: PlanSelector | None = None

    def get(self) -> PlanSelector:
        if self.selector is None:
            self.loader = PlanLoader(str(_PLANS_DIR))
            self.history = PlanHistoryStore(_history_path())
            self.selector = PlanSelector(
                plan_loader=self.loader, history_store=self.history
            )
        return self.selector

    def get_loader(self) -> PlanLoader:
        self.get()
        assert self.loader is not None
        return self.loader

    def get_history(self) -> PlanHistoryStore:
        self.get()
        assert self.history is not None
        return self.history

    def reset(self) -> None:
        self.loader = None
        self.history = None
        self.selector = None


_RUNTIME = _SelectionRuntime()


def _history_path() -> str:
    """Return the history database path (override with ``DE_PLAN_HISTORY``)."""
    configured = os.environ.get("DE_PLAN_HISTORY", "").strip()
    if configured:
        return configured
    target = _ROOT.parent / "data" / "plan_history.db"
    target.parent.mkdir(parents=True, exist_ok=True)
    return str(target)


def reset_runtime() -> None:
    """Drop cached selector state (tests and release reloads)."""
    _RUNTIME.reset()


def available_plans() -> list[str]:
    """Return every loadable plan ID."""
    return list(_RUNTIME.get_loader().list_available())


def selectable_plans() -> list[str]:
    """Return executable plans automatic routing may pick."""
    available = available_plans()
    return [plan_id for plan_id in AUTO_SELECTABLE if plan_id in available]


def plan_catalog_state(plan_id: str) -> tuple[bool, str]:
    """Return ``(executable, blocker_reason)`` for *plan_id*."""
    if plan_id not in available_plans():
        return False, "plan template not found"
    blocker = plan_blocker(plan_id)
    return (not blocker), blocker


async def select_plan(nl_text: str, domain: str = "") -> PlanDecision:
    """Choose a plan for *nl_text*, filtered to the auto-selectable set.

    A selector verdict outside that set is preserved in ``alternatives`` and
    explained in ``reason`` instead of being silently dropped.
    """
    selector = _RUNTIME.get()
    result = await selector.select(nl_text, domain or None)

    candidates = selectable_plans()
    mapped = _SELECTOR_TO_API.get(result.plan_id)
    if mapped is not None and mapped in candidates:
        reason = result.reason
        if mapped != result.plan_id:
            reason = f"{reason} (selector: {result.plan_id})"
        return PlanDecision(
            plan_id=mapped,
            layer=result.layer,
            reason=reason,
            confidence=result.confidence,
            alternatives=[p for p in candidates if p != mapped],
        )

    filtered = f"selector chose '{result.plan_id}' outside auto-selectable {candidates}"
    return PlanDecision(
        plan_id=DEFAULT_PLAN,
        layer="filtered",
        reason=filtered,
        confidence=0.3,
        alternatives=[result.plan_id, *candidates],
    )


def record_decision(
    nl_text: str,
    plan_id: str,
    *,
    domain: str = "",
    success: bool = True,
) -> None:
    """Record a resolved plan for future L2 lookups; never raises."""
    try:
        history = _RUNTIME.get_history()
        history.record(nl_text, plan_id, domain=domain or None, success=success)
    except Exception:
        return
    try:
        selector = _RUNTIME.get()
        selector.record_outcome(plan_id, validation_passed=success)
    except Exception:
        return


def executable_catalog() -> list[str]:
    """Return loadable plans that can actually be executed."""
    return executable_plan_ids(available_plans())
