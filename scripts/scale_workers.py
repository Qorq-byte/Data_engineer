"""Measure whether independent worker processes raise pipeline throughput.

Stage 51 showed that cutting per-request CPU inside one process does not raise
throughput with asyncio concurrency: the remaining work is Python CPU bound by
the GIL. This probe answers the next question with a measurement instead of an
argument: if N separate processes each serve the same read-only fixture, does
aggregate throughput grow roughly with N?

Every worker builds its own database connection and schema cache and runs the
same in-process pipeline as ``scripts.scale_load``; nothing is shared except the
SQLite file on disk. That is deliberately *not* a deployment: it measures the
ceiling that process-level parallelism offers before any shared-state design.

Example::

    python -m scripts.scale_workers --output workers.json \\
        --workdir .scale_probe/workers --tables 1000 --workers 1,2,4 --requests 6
"""

from __future__ import annotations

import argparse
import asyncio
import json
import multiprocessing as mp
import os
import queue
import secrets
import sqlite3
import statistics
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.evaluation.scale_probe import (
    ScaleSpec,
    build_synthetic_database,
    environment_facts,
    percentile,
)

DB_ID = "scale-fixture"


@dataclass(frozen=True)
class WorkerJob:
    """What one worker process needs to know."""

    database: str
    requests: int
    warmup: int


def _apply_runtime_env() -> None:
    """Pin the same offline defaults the load probe uses."""
    os.environ.setdefault("JWT_SECRET", secrets.token_urlsafe(48))
    os.environ["LLM_MOCK_MODE"] = "1"
    os.environ["LITELLM_LOCAL_MODEL_COST_MAP"] = "True"
    os.environ["RAG_EMBEDDING_PROVIDER"] = "mock"


async def _serve(job: WorkerJob, ready: Any, result: Any) -> None:
    from app.db.connections import ConnectionFactory, DatabaseType
    from app.db.schema_extractor import SchemaExtractor
    from scripts.scale_load import _pipeline_action

    connection = await ConnectionFactory.create(
        DatabaseType.SQLITE, {"path": job.database}, DB_ID
    )
    try:
        await SchemaExtractor.extract(connection, DatabaseType.SQLITE, DB_ID)
        action = await _pipeline_action()
        for _ in range(job.warmup):
            await action()
        ready.wait()
        # Timestamp after the barrier so the shared window measures the
        # measurement itself, not process start-up skew.
        started = time.time()
        latencies: list[float] = []
        statuses: dict[str, int] = {}
        for _ in range(job.requests):
            step = time.perf_counter()
            status = await action()
            latencies.append((time.perf_counter() - step) * 1000)
            statuses[status] = statuses.get(status, 0) + 1
        finished = time.time()
        result.put(
            {
                "pid": os.getpid(),
                "started": started,
                "finished": finished,
                "attempts": len(latencies),
                "statuses": statuses,
                "latencies_ms": latencies,
            }
        )
    finally:
        await ConnectionFactory.aclose(DB_ID)


def _worker_entry(job: WorkerJob, ready: Any, result: Any) -> None:
    _apply_runtime_env()
    asyncio.run(_serve(job, ready, result))


def summarise_workers(workers: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate per-worker samples into one throughput and latency view.

    Throughput uses the shared wall window (earliest start to latest finish) so
    a slow-started worker cannot inflate the number, while latency pools every
    request the workers actually attempted.
    """
    if not workers:
        raise ValueError("no worker results to summarise")

    pooled: list[float] = []
    attempts = 0
    statuses: dict[str, int] = {}
    for worker in workers:
        pooled.extend(float(value) for value in worker["latencies_ms"])
        attempts += int(worker["attempts"])
        for status, count in worker["statuses"].items():
            statuses[status] = statuses.get(status, 0) + int(count)

    started = min(float(worker["started"]) for worker in workers)
    finished = max(float(worker["finished"]) for worker in workers)
    window = max(finished - started, 1e-9)
    pooled.sort()

    return {
        "workers": len(workers),
        "attempts": attempts,
        "statuses": statuses,
        "window_seconds": round(window, 3),
        "attempt_rps": round(attempts / window, 3),
        "p50_ms": round(statistics.median(pooled), 2),
        "p95_ms": round(percentile(pooled, 0.95) or 0.0, 2),
        "per_worker_attempts": sorted(int(worker["attempts"]) for worker in workers),
    }


def _ensure_fixture(database: Path, spec: ScaleSpec) -> dict[str, int]:
    if database.exists():
        raise FileExistsError(f"refusing to reuse an existing database: {database}")
    database.parent.mkdir(parents=True, exist_ok=True)
    structure = build_synthetic_database(database, spec)
    _seed_facts(database)
    return structure


def _seed_facts(database: Path) -> None:
    """Create the small fact table the pipeline's scripted SQL queries.

    ``scripts.scale_load`` seeds it through a registered connection; workers each
    open their own connection, so the table is created once here and only read
    afterwards. Doing it in the parent also keeps four workers from racing on the
    same CREATE TABLE.
    """
    connection = sqlite3.connect(str(database))
    try:
        with connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS facts "
                "(id INTEGER PRIMARY KEY, bucket INTEGER, amount INTEGER)"
            )
            connection.executemany(
                "INSERT INTO facts VALUES (?, ?, ?)", ((i, i % 10, i % 100) for i in range(100))
            )
    finally:
        connection.close()


def run_level(database: Path, workers: int, requests: int, warmup: int) -> dict[str, Any]:
    """Start *workers* processes at the same moment and collect their samples."""
    context = mp.get_context("spawn")
    ready = context.Barrier(workers)
    result = context.Queue()
    job = WorkerJob(database=str(database), requests=requests, warmup=warmup)
    processes = [
        context.Process(target=_worker_entry, args=(job, ready, result), daemon=False)
        for _ in range(workers)
    ]
    for process in processes:
        process.start()
    samples: list[dict[str, Any]] = []
    for _ in processes:
        try:
            samples.append(result.get(timeout=1800))
        except queue.Empty as exc:  # pragma: no cover - only on a crashed worker
            for process in processes:
                process.terminate()
            raise RuntimeError("a worker never reported a result") from exc
    for process in processes:
        process.join(timeout=120)
        if process.is_alive():
            process.terminate()
    failures = [process.exitcode for process in processes if process.exitcode not in (0, None)]
    summary = summarise_workers(samples)
    summary["nonzero_exit_codes"] = failures
    return summary


def _levels(text: str) -> list[int]:
    values = [int(part) for part in str(text).split(",") if part]
    if not values or any(value < 1 for value in values):
        raise ValueError("worker counts must be positive integers")
    return values


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--workdir", default=".scale_probe/workers")
    parser.add_argument("--tables", type=int, default=1000)
    parser.add_argument("--columns", type=int, default=12)
    parser.add_argument("--workers", default="1,2,4")
    parser.add_argument("--requests", type=int, default=6)
    parser.add_argument("--warmup", type=int, default=1)
    args = parser.parse_args(argv)

    try:
        levels = _levels(args.workers)
    except ValueError as exc:
        print(f"worker probe refused: {exc}", file=sys.stderr)
        return 2
    if args.requests < 1 or args.warmup < 0:
        print("worker probe refused: requests must be >= 1 and warmup >= 0", file=sys.stderr)
        return 2

    _apply_runtime_env()
    spec = ScaleSpec(tables=args.tables, columns_per_table=args.columns, documents=1)
    workdir = Path(args.workdir)
    database = workdir / "fixture.sqlite3"
    try:
        structure = _ensure_fixture(database, spec)
    except FileExistsError as exc:
        print(f"worker probe refused: {exc}", file=sys.stderr)
        return 2

    report: dict[str, Any] = {
        "profile": "synthetic-worker-scaling",
        "spec": {**spec.describe(), "database_bytes": structure["bytes"]},
        "environment": {
            **environment_facts(),
            "transport": "one process per worker; nothing shared but the SQLite file",
            "llm": "scripted fixed SQL; no external model call",
            "embedding": "no retrieval index is built for this scenario",
        },
        "requests_per_worker": args.requests,
        "warmup_per_worker": args.warmup,
        "levels": [],
    }

    for workers in levels:
        level = run_level(database, workers, args.requests, args.warmup)
        report["levels"].append({"worker_processes": workers, **level})
        print(json.dumps({"worker_processes": workers, **level}, ensure_ascii=False), flush=True)

    report["reading"] = (
        "aggregate attempts over the shared wall window; a flat curve means the work "
        "does not parallelise across processes either, a rising curve means process "
        "count is a real lever and the blockers are shared state, not CPU"
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({"levels": len(report["levels"]), "status": "finished"}), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
