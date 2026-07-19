"""Global constraint enforcer — Harness-level safety limits.

See SPEC §3.4.5 (constraints) and §3.4.6 (Hard Invariants).

These constraints are enforced by the WorkflowRunner and cannot be
bypassed by LLM decisions. They are loaded from agent.yml's `constraints`
section and also available as sensible defaults for standalone use.
"""

from dataclasses import dataclass


@dataclass
class ConstraintLimits:
    """Hard limits enforced by the Harness at runtime."""

    max_llm_calls_per_query: int = 10
    max_node_retries: int = 3
    reflection_max_rounds: int = 3
    reflection_on_exhausted: str = "force_output"  # "force_output" | "abort"
    session_ttl_seconds: int = 3600
    read_only: bool = True
    max_result_rows: int = 1000
    statement_timeout_ms: int = 30000


class ConstraintEnforcer:
    """Validates runtime actions against configured limits."""

    def __init__(self, limits: ConstraintLimits):
        self.limits = limits
        self._llm_call_count: int = 0
        self._node_retry_count: dict[str, int] = {}

    # ── LLM call tracking ──────────────────────────────────────

    def record_llm_call(self) -> None:
        """Increment the LLM call counter."""
        self._llm_call_count += 1

    def can_call_llm(self) -> bool:
        """Check if another LLM call is allowed."""
        return self._llm_call_count < self.limits.max_llm_calls_per_query

    @property
    def llm_calls_used(self) -> int:
        return self._llm_call_count

    @property
    def llm_calls_remaining(self) -> int:
        return max(0, self.limits.max_llm_calls_per_query - self._llm_call_count)

    # ── Retry tracking ─────────────────────────────────────────

    def record_retry(self, node_id: str) -> None:
        self._node_retry_count[node_id] = self._node_retry_count.get(node_id, 0) + 1

    def can_retry(self, node_id: str) -> bool:
        return self._node_retry_count.get(node_id, 0) < self.limits.max_node_retries

    # ── Safety checks ──────────────────────────────────────────

    def is_read_only_enforced(self) -> bool:
        return self.limits.read_only

    def clamp_rows(self, requested: int) -> int:
        """Clamp requested row count to max_result_rows."""
        return min(requested, self.limits.max_result_rows)

    def reset(self) -> None:
        """Reset all counters for a new query."""
        self._llm_call_count = 0
        self._node_retry_count.clear()
