"""Bounded request-local observations, never billing or router-global deltas."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import asdict, dataclass
from typing import Any, Literal

from app.llm.completion import response_field

type UsageBasis = Literal["response_fields", "sdk_normalized"]


def _usage_field(value: object, name: str) -> Any:
    """Optional SDK metadata must not replace the completion/error being observed."""
    try:
        return response_field(value, name)
    except Exception:
        return None


@dataclass
class Attempt:
    attempt: int
    provider: str
    model: str
    source: str
    status: str = "pending"
    latency_ms: float = 0
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    missing_reason: str | None = "usage_missing"
    finish_status: str = "unknown"
    usage_basis: UsageBasis = "response_fields"


class UsageCollector:
    def __init__(self, limit: int = 1000) -> None:
        self.records: list[Attempt] = []
        self.limit = max(0, min(limit, 1000))
        self.dropped = 0

    def observe(self, response: Any, *, usage_basis: UsageBasis | None = None) -> None:
        record = current_attempt.get()
        if record is None:
            return
        if usage_basis is not None:
            record.usage_basis = usage_basis
        usage = _usage_field(response, "usage")
        if usage is not None:
            for key in ("prompt_tokens", "completion_tokens"):
                value = _usage_field(usage, key)
                setattr(record, key, value if type(value) is int and value >= 0 else None)
            record.missing_reason = (
                None
                if record.prompt_tokens is not None and record.completion_tokens is not None
                else "usage_invalid_or_partial"
            )
        choices = _usage_field(response, "choices")
        if not isinstance(choices, (list, tuple)):
            return
        for choice in choices:
            for name in ("message", "delta"):
                message = _usage_field(choice, name)
                if any(
                    _usage_field(message, key) for key in ("refusal", "tool_calls", "function_call")
                ):
                    record.finish_status = "other"
            reason = _usage_field(choice, "finish_reason")
            if reason is not None and record.finish_status not in ("length", "other"):
                record.finish_status = (
                    reason if type(reason) is str and reason in ("stop", "length") else "other"
                )

    def summary(self) -> dict[str, Any]:
        missing = sum(r.missing_reason is not None for r in self.records)
        return {
            "attempt_count": len(self.records),
            "attempts": [asdict(r) for r in self.records],
            "known_prompt_tokens": sum(
                r.prompt_tokens for r in self.records if r.prompt_tokens is not None
            ),
            "known_completion_tokens": sum(
                r.completion_tokens for r in self.records if r.completion_tokens is not None
            ),
            "missing_usage_count": missing,
            "usage_coverage": (len(self.records) - missing) / len(self.records)
            if self.records
            else None,
            "complete": not missing
            and not self.dropped
            and all(r.status != "pending" for r in self.records),
            "dropped_attempts": self.dropped,
        }


active_usage: ContextVar[UsageCollector | None] = ContextVar("llm_usage", default=None)
current_attempt: ContextVar[Attempt | None] = ContextVar("llm_attempt", default=None)


def observe_response(response: Any, *, usage_basis: UsageBasis | None = None) -> None:
    collector = active_usage.get()
    if collector is not None:
        collector.observe(response, usage_basis=usage_basis)


@contextmanager
def observe(
    collector: UsageCollector | None,
    provider: str,
    model: str,
    source: str,
    *,
    existing: Attempt | None = None,
    started: float | None = None,
) -> Iterator[None]:
    if collector is None:
        yield
        return
    if existing is None and len(collector.records) >= collector.limit:
        collector.dropped += 1
        empty_token = current_attempt.set(None)
        try:
            yield
        finally:
            current_attempt.reset(empty_token)
        return
    record = existing or Attempt(len(collector.records) + 1, provider, model, source)
    if existing is None:
        collector.records.append(record)
    token = current_attempt.set(record)
    started = time.perf_counter() if started is None else started
    try:
        yield
        record.status = "incomplete" if record.finish_status in ("length", "other") else "completed"
    except asyncio.CancelledError:
        record.status = "cancelled"
        raise
    except GeneratorExit:
        record.status = "closed"
        raise
    except BaseException as exc:
        observe_response(exc)
        record.status = "timeout" if isinstance(exc, TimeoutError) else "failed"
        raise
    finally:
        record.latency_ms = (time.perf_counter() - started) * 1000
        current_attempt.reset(token)
