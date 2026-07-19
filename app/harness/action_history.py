"""ActionHistoryManager — records every execute() call for audit.

See SPEC §4.12.3 (⑤ ActionHistoryManager) and §4.11.8 (Agent observability).

Each entry records: node_name, input summary, output summary, timestamp,
latency, and any errors. Used for WorkflowTrace persistence.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class ActionEntry:
    """A single action record in the history."""

    node_name: str
    input_summary: str
    output_summary: str = ""
    started_at: datetime = field(default_factory=datetime.now)
    completed_at: datetime | None = None
    latency_ms: float = 0.0
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


class ActionHistoryManager:
    """Ring-buffer style action history for a single node.

    Stores up to max_entries records. Oldest entries evicted first.
    """

    def __init__(self, max_entries: int = 100):
        self.max_entries = max_entries
        self._entries: list[ActionEntry] = []

    def record_start(self, node_name: str, input_summary: str) -> int:
        """Record the start of an action. Returns entry index."""
        entry = ActionEntry(
            node_name=node_name,
            input_summary=input_summary,
        )
        self._entries.append(entry)
        if len(self._entries) > self.max_entries:
            self._entries.pop(0)
        return len(self._entries) - 1

    def record_end(
        self,
        index: int,
        output_summary: str = "",
        error: str | None = None,
    ) -> None:
        """Record the completion of an action."""
        if 0 <= index < len(self._entries):
            entry = self._entries[index]
            entry.output_summary = output_summary
            entry.completed_at = datetime.now()
            entry.error = error
            if entry.completed_at:
                entry.latency_ms = (
                    entry.completed_at - entry.started_at
                ).total_seconds() * 1000

    def last(self) -> ActionEntry | None:
        """Return the most recent entry."""
        return self._entries[-1] if self._entries else None

    def all(self) -> list[ActionEntry]:
        """Return all entries (newest last)."""
        return list(self._entries)

    def clear(self) -> None:
        """Clear all history."""
        self._entries.clear()

    def __len__(self) -> int:
        return len(self._entries)
