"""Local ASGI/SQLite load experiment; no external LLM, network server or production data.

python -m scripts.load_test --output result.json --requests 100 --rows 10000
The pipeline scenario uses fixed SQL from an explicit scripted router. It measures
request orchestration, validation and execution, not model or semantic RAG performance.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import platform
import secrets
import sqlite3
import time
from collections import Counter
from pathlib import Path
from typing import Any


def percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    lo = int(position)
    hi = min(lo + 1, len(ordered) - 1)
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (position - lo)


def latencies(values: list[float]) -> dict[str, float | None]:
    return {f"p{int(q * 100)}_ms": percentile(values, q) for q in [0.5, 0.95, 0.99]}


async def experiment(requests: int, rows: int) -> dict[str, Any]:
    # Isolated process configuration only; no environment or secrets are persisted.
    os.environ.setdefault("JWT_SECRET", secrets.token_urlsafe(48))
    os.environ["LLM_MOCK_MODE"] = "1"
    os.environ["LITELLM_LOCAL_MODEL_COST_MAP"] = "True"
    os.environ["RAG_EMBEDDING_PROVIDER"] = "mock"
    os.environ.setdefault("DE_TRACE_DIR", ".verify_tmp_load/traces")
    import httpx

    from app.api import create_app, query
    from app.auth.security import create_access_token
    from app.config.settings import settings
    from app.db.connections import ConnectionFactory, DatabaseType
    from app.llm.router import LiteLLMRouter, RouterConfig

    sql = "SELECT bucket, SUM(amount) AS total FROM facts GROUP BY bucket ORDER BY bucket"

    class ScriptedRouter(LiteLLMRouter):
        async def complete(self, messages, **kwargs):
            return {"choices": [{"message": {"content": sql}}]}

    router = ScriptedRouter(RouterConfig(), mock_mode=True)
    original_factory = query.get_default_router
    query.get_default_router = lambda: router
    conn = await ConnectionFactory.create(DatabaseType.SQLITE, {}, "load-fixture")
    conn.execute("CREATE TABLE facts (id INTEGER PRIMARY KEY, bucket INTEGER, amount INTEGER)")
    conn.executemany(
        "INSERT INTO facts VALUES (?, ?, ?)", ((i, i % 10, i % 100) for i in range(rows))
    )
    conn.commit()
    expected = [list(row) for row in conn.execute(sql).fetchall()]
    scenarios = [
        ("direct_sql", "/api/v1/execute", {"sql": sql, "database": "load-fixture"}),
        (
            "fixed_sql_pipeline",
            "/api/v1/query",
            {
                "nl_text": "total amount by bucket in facts",
                "database": "load-fixture",
                "execute": True,
                "num_candidates": 1,
            },
        ),
    ]
    process = None
    try:
        import psutil

        process = psutil.Process()
    except ImportError:
        pass
    result = {
        "environment": {
            "python": platform.python_version(),
            "platform": platform.platform(),
            "logical_cpus": os.cpu_count(),
            "sqlite": sqlite3.sqlite_version,
            "httpx": httpx.__version__,
            "transport": "in-process ASGI; lifespan not run",
            "database": "single factory-managed in-memory SQLite connection",
            "rows": rows,
            "llm": "scripted fixed SQL; no external model calls",
            "rag": "not initialized",
            "query_capacity": settings.max_concurrent_queries,
            "warmup_requests_per_scenario": 3,
            "requests_per_level": requests,
        },
        "runs": [],
    }
    try:
        token = create_access_token(settings.admin_user_ids[0], "local-load-fixture")
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=create_app()),
            base_url="http://load.local",
            headers={"Authorization": f"Bearer {token}"},
        ) as client:
            for name, endpoint, body in scenarios:
                for _ in range(3):
                    warmup = await client.post(endpoint, json=body)
                    if warmup.status_code != 200:
                        raise RuntimeError(f"Warmup failed: {warmup.status_code}")
                for concurrency in [1, 2, 5, 10]:
                    limiter = asyncio.Semaphore(concurrency)
                    statuses: Counter[str] = Counter()
                    all_ms, successful_ms, lags = [], [], []
                    peak_rss = [process.memory_info().rss if process else None]
                    cpu_start = sum(process.cpu_times()[:2]) if process else None
                    stop = asyncio.Event()

                    async def monitor(stop=stop, lags=lags, peak_rss=peak_rss):
                        while not stop.is_set():
                            started = time.perf_counter()
                            await asyncio.sleep(0.005)
                            lags.append(max(0, time.perf_counter() - started - 0.005) * 1000)
                            if process:
                                peak_rss[0] = max(peak_rss[0] or 0, process.memory_info().rss)

                    async def one(
                        limiter=limiter,
                        endpoint=endpoint,
                        body=body,
                        name=name,
                        all_ms=all_ms,
                        successful_ms=successful_ms,
                        statuses=statuses,
                    ):
                        async with limiter:
                            started = time.perf_counter()
                            try:
                                response = await client.post(endpoint, json=body)
                                data = response.json()
                                execution = (
                                    data if name == "direct_sql" else data.get("execution") or {}
                                )
                                succeeded = (
                                    response.status_code == 200
                                    and execution.get("rows") == expected
                                )
                                status = (
                                    "ok"
                                    if succeeded
                                    else (
                                        f"http_{response.status_code}"
                                        if response.status_code != 200
                                        else "result_mismatch"
                                    )
                                )
                            except Exception as exc:
                                succeeded = False
                                status = type(exc).__name__
                            elapsed = (time.perf_counter() - started) * 1000
                            all_ms.append(elapsed)
                            if succeeded:
                                successful_ms.append(elapsed)
                            statuses[status] += 1

                    sampler = asyncio.create_task(monitor())
                    started = time.perf_counter()
                    await asyncio.gather(*(one() for _ in range(requests)))
                    elapsed = time.perf_counter() - started
                    stop.set()
                    await sampler
                    cpu_seconds = sum(process.cpu_times()[:2]) - cpu_start if process else None
                    run = {
                        "scenario": name,
                        "concurrency": concurrency,
                        "requests": requests,
                        "wall_seconds": elapsed,
                        "attempt_rps": requests / elapsed,
                        "successful_rps": len(successful_ms) / elapsed,
                        "error_rate": 1 - len(successful_ms) / requests,
                        "statuses": dict(statuses),
                        "all_latency": latencies(all_ms),
                        "success_latency": latencies(successful_ms),
                        "event_loop_lag_p95_ms": percentile(lags, 0.95),
                        "process_peak_rss_bytes": peak_rss[0],
                        "process_cpu_seconds": cpu_seconds,
                        "cpu_percent_one_core": cpu_seconds / elapsed * 100
                        if cpu_seconds is not None
                        else None,
                    }
                    result["runs"].append(run)
                    print(json.dumps(run), flush=True)
    finally:
        query.get_default_router = original_factory
        await ConnectionFactory.aclose_all()
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--requests", type=int, default=100)
    parser.add_argument("--rows", type=int, default=10000)
    args = parser.parse_args()
    if args.requests < 1 or args.rows < 1:
        parser.error("requests and rows must be positive")
    result = asyncio.run(experiment(args.requests, args.rows))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    main()
