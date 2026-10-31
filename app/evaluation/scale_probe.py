"""Repeatable synthetic scale and load probing with explicit stop limits.

The probe never calls an external model or embedding service. The RAG
scenarios use the deterministic mock embedding provider and a private
temporary index directory, so the reported numbers describe this machine
and this build, not a production capacity promise.

Every level keeps a fixed request denominator: failed, timed-out and
cancelled attempts stay in the sample instead of being dropped.
"""

from __future__ import annotations

import asyncio
import os
import sqlite3
import sys
import time
from collections import Counter
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

MIB = 1024 * 1024


def _windows_rss_bytes() -> int | None:
    import ctypes
    from ctypes import wintypes

    class Counters(ctypes.Structure):
        _fields_ = [
            ("cb", wintypes.DWORD),
            ("PageFaultCount", wintypes.DWORD),
            ("PeakWorkingSetSize", ctypes.c_size_t),
            ("WorkingSetSize", ctypes.c_size_t),
            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
            ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
            ("PagefileUsage", ctypes.c_size_t),
            ("PeakPagefileUsage", ctypes.c_size_t),
        ]

    counters = Counters()
    counters.cb = ctypes.sizeof(Counters)
    load = cast("Callable[..., Any]", cast("Any", ctypes).WinDLL)
    kernel32 = load("kernel32", use_last_error=True)
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    query = getattr(kernel32, "K32GetProcessMemoryInfo", None)
    if query is None:
        query = load("psapi", use_last_error=True).GetProcessMemoryInfo
    query.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
    query.restype = wintypes.BOOL
    handle = kernel32.GetCurrentProcess()
    if not query(handle, ctypes.byref(counters), counters.cb):
        return None
    return int(counters.WorkingSetSize)


def _posix_rss_bytes() -> int | None:
    with open("/proc/self/status", encoding="ascii") as handle:
        for line in handle:
            if line.startswith("VmRSS:"):
                return int(line.split()[1]) * 1024
    return None


def current_rss_bytes() -> int | None:
    """Best-effort resident set size; ``None`` when the platform API is unavailable."""
    try:
        return _windows_rss_bytes() if os.name == "nt" else _posix_rss_bytes()
    except Exception:
        return None


def percentile(values: list[float], fraction: float) -> float | None:
    """Nearest-rank percentile over the full sample, including failures."""
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, min(len(ordered), int(-(-fraction * len(ordered) // 1))))
    return ordered[rank - 1]


def latency_summary(all_ms: list[float], success_ms: list[float]) -> dict[str, Any]:
    return {
        "attempts": len(all_ms),
        "successful": len(success_ms),
        "all": {f"p{int(q * 100)}_ms": percentile(all_ms, q) for q in (0.5, 0.95, 0.99)},
        "success": {f"p{int(q * 100)}_ms": percentile(success_ms, q) for q in (0.5, 0.95, 0.99)},
        "policy": "nearest-rank over attempted requests; failures keep the denominator",
    }


def directory_bytes(directory: Path) -> int:
    total = 0
    for path in directory.rglob("*"):
        try:
            if path.is_file():
                total += path.stat().st_size
        except OSError:
            continue
    return total


@dataclass(frozen=True)
class ScaleSpec:
    """Separate structural size from document size; a table is not a document."""

    tables: int
    columns_per_table: int
    documents: int

    def __post_init__(self) -> None:
        for name in ("tables", "columns_per_table", "documents"):
            if getattr(self, name) < 1:
                raise ValueError(f"{name} must be positive")
        if self.columns_per_table < 2:
            raise ValueError("columns_per_table must leave room for a primary key")

    def describe(self) -> dict[str, int]:
        return {
            "tables": self.tables,
            "columns_per_table": self.columns_per_table,
            "column_objects": self.tables * self.columns_per_table,
            "documents": self.documents,
        }


@dataclass(frozen=True)
class ProbeLimits:
    """Stop policy fixed before the run; exceeding any bound ends the level."""

    scenario_seconds: float = 60.0
    batch_seconds: float = 1200.0
    rss_bytes: int = 2 * 1024**3
    artifact_bytes: int = 1024**3

    def describe(self) -> dict[str, Any]:
        return {
            "scenario_seconds": self.scenario_seconds,
            "batch_seconds": self.batch_seconds,
            "rss_bytes": self.rss_bytes,
            "artifact_bytes": self.artifact_bytes,
            "applies_to": "checked between attempts; a trip keeps partial samples",
        }


class ProbeStoppedError(RuntimeError):
    """A declared stop bound was reached; the level result stays partial."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def synthetic_schema(spec: ScaleSpec) -> list[tuple[str, list[tuple[str, str]]]]:
    """Deterministic table/column plan; names stay stable across runs."""
    plan: list[tuple[str, list[tuple[str, str]]]] = []
    for table_index in range(spec.tables):
        columns: list[tuple[str, str]] = [("id", "INTEGER PRIMARY KEY")]
        for column_index in range(spec.columns_per_table - 1):
            name = f"col_{column_index:03d}"
            if column_index == 0:
                columns.append((f"{name}", "TEXT"))
            elif column_index % 4 == 1:
                columns.append((name, "INTEGER"))
            elif column_index % 4 == 2:
                columns.append((name, "REAL"))
            else:
                columns.append((name, "TEXT"))
        plan.append((f"entity_{table_index:05d}", columns))
    return plan


def synthetic_ddl(spec: ScaleSpec) -> list[str]:
    return [
        f"CREATE TABLE {table} ({', '.join(f'{name} {kind}' for name, kind in columns)})"
        for table, columns in synthetic_schema(spec)
    ]


def synthetic_documents(spec: ScaleSpec) -> list[dict[str, str]]:
    """One metadata document per table; the document count is reported separately."""
    documents: list[dict[str, str]] = []
    for table, columns in synthetic_schema(spec):
        names = ", ".join(name for name, _ in columns)
        documents.append(
            {
                "doc_id": f"{table}.table",
                "db_id": "scale-fixture",
                "table_name": table,
                "text": f"table {table} columns {names} synthetic metadata for scale probing",
            }
        )
    return documents[: spec.documents]


def build_synthetic_database(path: Path, spec: ScaleSpec) -> dict[str, int]:
    """Create a real SQLite file with the planned tables; returns structural counts."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise FileExistsError(f"Refusing to reuse an existing database: {path}")
    connection = sqlite3.connect(str(path))
    try:
        with connection:
            for statement in synthetic_ddl(spec):
                connection.execute(statement)
        tables = connection.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name LIKE 'entity_%'"
        ).fetchone()[0]
    finally:
        connection.close()
    return {"tables": int(tables), "bytes": path.stat().st_size}


def _check(limits: ProbeLimits, deadline: float, batch_started: float,
           rss_peak: list[int | None], artifact_root: Path | None) -> str | None:
    now = time.perf_counter()
    if now > deadline:
        return "SCENARIO_DEADLINE"
    if now - batch_started > limits.batch_seconds:
        return "BATCH_DEADLINE"
    rss = current_rss_bytes()
    if rss is not None:
        rss_peak[0] = max(rss_peak[0] or 0, rss)
        if rss > limits.rss_bytes:
            return "RSS_LIMIT"
    if artifact_root is not None and directory_bytes(artifact_root) > limits.artifact_bytes:
        return "ARTIFACT_LIMIT"
    return None


async def measure_level(
    name: str,
    concurrency: int,
    requests: int,
    action: Callable[[], Awaitable[str]],
    *,
    limits: ProbeLimits,
    batch_started: float,
    artifact_root: Path | None = None,
) -> dict[str, Any]:
    """Run one scenario level under a fixed denominator and declared stop policy."""
    if concurrency < 1 or requests < 1:
        raise ValueError("concurrency and requests must be positive")
    limiter = asyncio.Semaphore(concurrency)
    statuses: Counter[str] = Counter()
    all_ms: list[float] = []
    success_ms: list[float] = []
    lags: list[float] = []
    rss_peak: list[int | None] = [current_rss_bytes()]
    stop_reason: list[str | None] = []
    finished = asyncio.Event()
    deadline = time.perf_counter() + limits.scenario_seconds

    async def monitor() -> None:
        while not finished.is_set():
            started = time.perf_counter()
            await asyncio.sleep(0.005)
            lags.append(max(0.0, time.perf_counter() - started - 0.005) * 1000)
            rss = current_rss_bytes()
            if rss is not None:
                rss_peak[0] = max(rss_peak[0] or 0, rss)

    async def one() -> None:
        async with limiter:
            if stop_reason:
                statuses["stopped_before_attempt"] += 1
                return
            tripped = _check(limits, deadline, batch_started, rss_peak, artifact_root)
            if tripped is not None:
                stop_reason.append(tripped)
                statuses["stopped_before_attempt"] += 1
                return
            started = time.perf_counter()
            try:
                status = await action()
            except asyncio.CancelledError:
                status = "cancelled"
                raise
            except Exception as exc:  # noqa: BLE001 - status names stay data, not control flow
                status = type(exc).__name__
            elapsed = (time.perf_counter() - started) * 1000
            all_ms.append(elapsed)
            statuses[status] += 1
            if status == "ok":
                success_ms.append(elapsed)

    sampler = asyncio.create_task(monitor())
    started = time.perf_counter()
    try:
        await asyncio.gather(*(one() for _ in range(requests)))
    finally:
        finished.set()
        await sampler
    wall = time.perf_counter() - started
    result: dict[str, Any] = {
        "scenario": name,
        "concurrency": concurrency,
        "requests_planned": requests,
        "attempts": len(all_ms),
        "not_attempted": requests - len(all_ms),
        "wall_seconds": wall,
        "attempt_rps": len(all_ms) / wall if wall else None,
        "success_rps": len(success_ms) / wall if wall else None,
        "statuses": dict(statuses),
        "latency": latency_summary(all_ms, success_ms),
        "event_loop_lag": {
            "samples": len(lags),
            "p50_ms": percentile(lags, 0.50),
            "p95_ms": percentile(lags, 0.95),
            "max_ms": max(lags) if lags else None,
        },
        "process_peak_rss_bytes": rss_peak[0],
        "stop_reason": stop_reason[0] if stop_reason else None,
    }
    return result


def environment_facts() -> dict[str, Any]:
    rss = current_rss_bytes()
    return {
        "python": sys.version.split()[0],
        "platform": sys.platform,
        "cpu_count": os.cpu_count(),
        "rss_probe": "GetProcessMemoryInfo" if os.name == "nt" else "/proc/self/status",
        "rss_readable": rss is not None,
    }


__all__ = [
    "ProbeLimits",
    "ProbeStoppedError",
    "ScaleSpec",
    "build_synthetic_database",
    "current_rss_bytes",
    "directory_bytes",
    "environment_facts",
    "latency_summary",
    "measure_level",
    "percentile",
    "synthetic_ddl",
    "synthetic_documents",
    "synthetic_schema",
]
