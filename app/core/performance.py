"""Performance instrumentation — centralized timing, counting, and reporting.

See SPEC §5.15 (Phase 5: Performance optimization).

Provides decorators for tracking function latency and call counts,
plus an aggregate reporting facility for dashboards.

Usage::

    from app.core.performance import track_latency, count_calls, get_performance_report

    @track_latency("generate_sql")
    async def generate(sql: str) -> str: ...

    @count_calls("cache_lookup")
    def cache_lookup(key: str) -> Any: ...

    report = get_performance_report()
"""

from __future__ import annotations

import functools
import time
from collections.abc import Callable
from typing import Any


class PerformanceStore:
    """Thread-safe store for performance counters and latency histograms."""

    def __init__(self) -> None:
        self._counters: dict[str, int] = {}
        self._latencies: dict[str, list[float]] = {}
        self._max_samples = 1000

    def increment(self, name: str, delta: int = 1) -> None:
        self._counters[name] = self._counters.get(name, 0) + delta

    def record_latency(self, name: str, latency_sec: float) -> None:
        self._latencies.setdefault(name, []).append(latency_sec)
        # Cap history
        if len(self._latencies[name]) > self._max_samples:
            self._latencies[name] = self._latencies[name][-self._max_samples:]

    def get_stats(self) -> dict[str, Any]:
        """Return aggregate stats for all tracked operations."""
        import statistics

        result: dict[str, Any] = {"counters": dict(self._counters)}

        latency_stats: dict[str, dict[str, float]] = {}
        for name, samples in self._latencies.items():
            if not samples:
                continue
            latency_stats[name] = {
                "count": len(samples),
                "avg_ms": round(statistics.mean(samples) * 1000, 2),
                "p50_ms": round(statistics.median(samples) * 1000, 2),
                "p95_ms": round(
                    sorted(samples)[int(len(samples) * 0.95)] * 1000, 2
                )
                if len(samples) >= 20
                else 0,
                "min_ms": round(min(samples) * 1000, 2),
                "max_ms": round(max(samples) * 1000, 2),
            }
        result["latencies"] = latency_stats

        # Compute totals
        total_calls = sum(self._counters.values())
        total_latency_ms = sum(
            sum(samples) * 1000 for samples in self._latencies.values()
        )
        result["totals"] = {
            "total_calls": total_calls,
            "total_latency_ms": round(total_latency_ms, 2),
        }

        return result

    def reset(self) -> None:
        """Clear all counters and latency samples."""
        self._counters.clear()
        self._latencies.clear()


# ── module-level singleton ────────────────────────────────────────────────

_store = PerformanceStore()


# ── public API ─────────────────────────────────────────────────────────────


def track_latency(name: str):
    """Decorator: record function wall-clock latency.

    Works with both sync and async functions.

    Args:
        name: Metric name for this operation (e.g. ``"generate_sql"``).
    """

    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
            start = time.perf_counter()
            try:
                return await func(*args, **kwargs)
            finally:
                elapsed = time.perf_counter() - start
                _store.record_latency(name, elapsed)

        @functools.wraps(func)
        def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
            start = time.perf_counter()
            try:
                return func(*args, **kwargs)
            finally:
                elapsed = time.perf_counter() - start
                _store.record_latency(name, elapsed)

        import asyncio

        if asyncio.iscoroutinefunction(func):
            return async_wrapper
        return sync_wrapper

    return decorator


def count_calls(name: str):
    """Decorator: increment a counter each time the function is called.

    Args:
        name: Counter name (e.g. ``"cache_lookup"``).
    """

    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            _store.increment(name)
            return func(*args, **kwargs)

        @functools.wraps(func)
        async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
            _store.increment(name)
            return await func(*args, **kwargs)

        import asyncio

        if asyncio.iscoroutinefunction(func):
            return async_wrapper
        return wrapper

    return decorator


def record_latency(name: str, elapsed_sec: float) -> None:
    """Manually record a latency measurement.

    Use this when you can't use the decorator (e.g., inside a context manager).

    Args:
        name: Metric name.
        elapsed_sec: Elapsed time in seconds.
    """
    _store.record_latency(name, elapsed_sec)


def increment_counter(name: str, delta: int = 1) -> None:
    """Manually increment a named counter.

    Args:
        name: Counter name.
        delta: Amount to increment by.
    """
    _store.increment(name, delta)


def get_performance_report() -> dict[str, Any]:
    """Return the current aggregate performance report.

    Returns:
        Dict with ``counters``, ``latencies``, and ``totals`` sections.
    """
    return _store.get_stats()


def reset_performance() -> None:
    """Reset all performance counters and samples."""
    _store.reset()
