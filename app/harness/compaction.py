"""Auto-compaction — deterministic context compression at a token threshold.

See SPEC §4.12.3 (⑥ Auto-Compaction): when the serialized workflow context
approaches the token budget (default 90% of 32k), it is compacted so LLM
nodes keep working within their window.

Token estimation reuses the heuristic from ``app.core.prompt_builder``
(English ~4 chars/token, CJK ~1.5 chars/token) so budgets stay consistent
across prompt building and compaction.

The default strategy is fully deterministic (no LLM):
  - keys listed in ``keep_keys`` are preserved verbatim,
  - long string values are reduced to a head + marker + tail digest,
  - list values keep only the most recent N items,
  - nested dicts are compacted recursively,
  - scalars pass through untouched.

An optional async ``summarizer`` callable can be injected for future
LLM-based summarization; when absent, deterministic truncation is used.
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from app.core.prompt_builder import PromptBuilder

TRUNCATION_MARKER = "...[compacted]..."


def estimate_tokens(text: str) -> int:
    """Rough token count estimate (English ~4 chars/token, CJK ~1.5).

    Delegates to :meth:`PromptBuilder.estimate_tokens` so the whole
    codebase shares a single estimation heuristic.
    """
    return PromptBuilder.estimate_tokens(text)


@dataclass
class ContextCompactor:
    """Compacts a workflow context dict when it nears the token budget.

    Attributes:
        threshold: Usage ratio (0.0-1.0) at which compaction triggers.
        max_tokens: Token budget the ratio is measured against.
        head_chars: Characters kept from the start of a long string.
        tail_chars: Characters kept from the end of a long string.
        max_list_items: Most recent list items retained during compaction.
        summarizer: Optional async callable ``(text) -> summary`` used by
            :meth:`compact_async` for LLM summarization. When None, the
            deterministic head/tail truncation is used instead.
    """

    threshold: float = 0.90
    max_tokens: int = 32000
    head_chars: int = 500
    tail_chars: int = 200
    max_list_items: int = 10
    summarizer: Callable[[str], Awaitable[str]] | None = None

    # ── Measurement ───────────────────────────────────────────────────

    def usage_ratio(self, context: dict[str, Any]) -> float:
        """Estimate the fraction of the token budget used by *context*."""
        if self.max_tokens <= 0:
            return 0.0
        return estimate_tokens(self._serialize(context)) / self.max_tokens

    def should_compact(self, context: dict[str, Any]) -> bool:
        """True when the context has reached the compaction threshold."""
        return self.usage_ratio(context) >= self.threshold

    @staticmethod
    def _serialize(context: dict[str, Any]) -> str:
        """Flatten a context dict to text for token estimation."""
        parts: list[str] = []
        for key, value in context.items():
            text = value if isinstance(value, str) else repr(value)
            parts.append(f"{key}: {text}")
        return "\n".join(parts)

    # ── Deterministic compaction ──────────────────────────────────────

    def compact(
        self, context: dict[str, Any], keep_keys: list[str] | None = None
    ) -> dict[str, Any]:
        """Return a compacted copy of *context*. The original is untouched.

        Args:
            context: The workflow context to compact.
            keep_keys: Top-level keys preserved verbatim (e.g. the current
                SQL or the user question).
        """
        keep = set(keep_keys or [])
        return {
            key: value if key in keep else self._compact_value(value)
            for key, value in context.items()
        }

    def _compact_value(self, value: object) -> object:
        """Compact a single value (recursive for lists and dicts)."""
        if isinstance(value, str):
            return self._truncate_text(value)
        if isinstance(value, list):
            recent = value[-self.max_list_items :] if self.max_list_items > 0 else []
            return [self._compact_value(item) for item in recent]
        if isinstance(value, dict):
            return {k: self._compact_value(v) for k, v in value.items()}
        return value

    def _truncate_text(self, text: str) -> str:
        """Reduce a long string to a head + marker + tail digest."""
        limit = self.head_chars + self.tail_chars + len(TRUNCATION_MARKER)
        if len(text) <= limit:
            return text
        return text[: self.head_chars] + TRUNCATION_MARKER + text[-self.tail_chars :]

    # ── Optional LLM-backed compaction ────────────────────────────────

    async def compact_async(
        self, context: dict[str, Any], keep_keys: list[str] | None = None
    ) -> dict[str, Any]:
        """Compact using the injected async summarizer when available.

        Long string values are replaced by ``await summarizer(text)``;
        everything else follows the deterministic rules. Falls back to
        :meth:`compact` when no summarizer is configured.
        """
        if self.summarizer is None:
            return self.compact(context, keep_keys)

        keep = set(keep_keys or [])
        limit = self.head_chars + self.tail_chars + len(TRUNCATION_MARKER)
        result: dict[str, Any] = {}
        for key, value in context.items():
            if key in keep:
                result[key] = value
            elif isinstance(value, str) and len(value) > limit:
                result[key] = await self.summarizer(value)
            else:
                result[key] = self._compact_value(value)
        return result
