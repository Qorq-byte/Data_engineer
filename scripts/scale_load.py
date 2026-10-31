"""Synthetic scale/load measurement CLI; no model, embedding service or network call.

Structural size, document size and concurrency are varied independently so a
thousand tables is never reported as a thousand documents. RAG scenarios use
the deterministic mock embedding provider and a private index directory.

    python -m scripts.scale_load --output .scale_probe/report.json \
        --tables 1000 --columns 12 --documents 1000 --requests 40 \
        --concurrency 1,2,5,10 --scenarios schema_extract,keyword_retrieve,rag_retrieve
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import platform
import secrets
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.evaluation.scale_probe import (
    ProbeLimits,
    ScaleSpec,
    build_synthetic_database,
    current_rss_bytes,
    directory_bytes,
    environment_facts,
    latency_summary,
    measure_level,
    synthetic_documents,
)

DB_ID = "scale-fixture"
SCENARIOS = ("schema_extract", "keyword_retrieve", "rag_index", "rag_retrieve", "pipeline")


@dataclass
class Fixture:
    """Everything a scenario may need, built once per (spec, workdir)."""

    workdir: Path
    spec: ScaleSpec
    connection: Any
    snapshot: Any
    retriever: Any
    rag_retriever: Any
    rag: Any
    build: dict[str, Any]


async def _prepare(workdir: Path, spec: ScaleSpec, with_rag: bool) -> Fixture:
    os.environ.setdefault("JWT_SECRET", secrets.token_urlsafe(48))
    os.environ["LLM_MOCK_MODE"] = "1"
    os.environ["LITELLM_LOCAL_MODEL_COST_MAP"] = "True"
    os.environ["RAG_EMBEDDING_PROVIDER"] = "mock"

    from app.db.connections import ConnectionFactory, DatabaseType
    from app.db.schema_extractor import SchemaExtractor
    from app.knowledge.schema_retriever import SchemaRetriever

    workdir.mkdir(parents=True, exist_ok=True)
    database = workdir / "fixture.sqlite3"
    structure = build_synthetic_database(database, spec)
    started = time.perf_counter()
    connection = await ConnectionFactory.create(
        DatabaseType.SQLITE, {"path": str(database)}, DB_ID
    )
    snapshot = await SchemaExtractor.extract(connection, DatabaseType.SQLITE, DB_ID)
    extract_ms = (time.perf_counter() - started) * 1000

    build: dict[str, Any] = {
        "database_bytes": structure["bytes"],
        "tables": structure["tables"],
        "snapshot_tables": len(snapshot.tables),
        "snapshot_columns": sum(len(t.columns) for t in snapshot.tables.values()),
        "cold_extract_ms": extract_ms,
        "documents": 0,
    }
    rag = None
    rag_retriever = None
    if with_rag:
        from app.knowledge.retrieval.bm25_index import BM25Index
        from app.knowledge.retrieval.embedding import EmbeddingGenerator, MockEmbeddingProvider
        from app.knowledge.retrieval.lancedb_store import LanceDBStore
        from app.knowledge.retrieval.schema_rag import SchemaMetadataRAG
        from app.models.rag_schema import SchemaDocument

        embedding = EmbeddingGenerator(MockEmbeddingProvider())
        dense = LanceDBStore(dimension=embedding.dimension, uri=str(workdir / "index" / "lance"))
        sparse = BM25Index(db_path=str(workdir / "index" / "sparse.sqlite3"))
        rag = SchemaMetadataRAG(dense, sparse, embedding)
        documents = [
            SchemaDocument(doc_id=row["doc_id"], db_id=row["db_id"], schema_name="main",
                           table_name=row["table_name"], embedding_text=row["text"])
            for row in synthetic_documents(spec)
        ]
        index_started = time.perf_counter()
        await rag.index_schemas(documents)
        build["index_build_ms"] = (time.perf_counter() - index_started) * 1000
        build["documents"] = len(documents)
        build["index_bytes"] = directory_bytes(workdir / "index")
        rag_retriever = SchemaRetriever(schema_rag=rag)

    return Fixture(workdir=workdir, spec=spec, connection=connection, snapshot=snapshot,
                   retriever=SchemaRetriever(), rag_retriever=rag_retriever, rag=rag, build=build)


def _actions(fixture: Fixture) -> dict[str, Any]:
    from app.db.connections import DatabaseType
    from app.db.schema_extractor import SchemaExtractor

    query = "订单数量和完成金额按用户分组，最近 30 天"

    async def schema_extract() -> str:
        await SchemaExtractor.extract(fixture.connection, DatabaseType.SQLITE, DB_ID)
        return "ok"

    async def keyword_retrieve() -> str:
        result = await fixture.retriever.retrieve(query, fixture.snapshot, db_id=DB_ID)
        return "ok" if result.has_schema else "empty_schema"

    async def rag_retrieve() -> str:
        if fixture.rag_retriever is None:
            return "rag_unavailable"
        result = await fixture.rag_retriever.retrieve(query, fixture.snapshot, db_id=DB_ID)
        return "ok" if result.has_schema else "empty_schema"

    return {
        "schema_extract": schema_extract,
        "keyword_retrieve": keyword_retrieve,
        "rag_retrieve": rag_retrieve,
    }


async def _pipeline_action() -> Any:
    """Full Harness path in-process with a scripted router; no external model call."""
    from app.api.query import QueryRequest
    from app.knowledge.schema_retriever import SchemaRetriever
    from app.llm.router import LiteLLMRouter, RouterConfig
    from app.workflow.query_service import run_query

    sql = "SELECT bucket, SUM(amount) AS total FROM facts GROUP BY bucket ORDER BY bucket"

    class ScriptedRouter(LiteLLMRouter):
        async def complete(self, messages: list[dict[str, str]], model: str | None = None,
                           temperature: float = 0.0, n: int = 1,
                           **kwargs: Any) -> dict[str, Any]:
            return {"choices": [{"message": {"content": sql}}]}

    router = ScriptedRouter(RouterConfig(), mock_mode=True)

    async def emit(event: str, payload: dict[str, Any]) -> None:
        return None

    async def action() -> str:
        response = await run_query(
            QueryRequest(nl_text="total amount by bucket in facts", domain="", database=DB_ID,
                         dialect="sqlite", num_candidates=1, execute=True, run_validation=True),
            emit, llm_router=router, schema_retriever=SchemaRetriever())
        execution = response.get("execution") or {}
        return "ok" if execution.get("rows") is not None else "missing_execution"

    return action


async def run(args: argparse.Namespace) -> dict[str, Any]:
    spec = ScaleSpec(tables=args.tables, columns_per_table=args.columns, documents=args.documents)
    limits = ProbeLimits(scenario_seconds=args.scenario_seconds,
                         batch_seconds=args.batch_seconds,
                         rss_bytes=int(args.max_rss_mib * 1024 * 1024),
                         artifact_bytes=int(args.max_artifact_mib * 1024 * 1024))
    workdir = Path(args.workdir).resolve()
    if workdir.exists() and any(workdir.iterdir()):
        raise ValueError(f"Refusing to reuse a non-empty work directory: {workdir}")
    selected = [name.strip() for name in args.scenarios.split(",") if name.strip()]
    unknown = sorted(set(selected) - set(SCENARIOS))
    if unknown:
        raise ValueError(f"Unknown scenarios: {', '.join(unknown)}")

    report: dict[str, Any] = {
        "profile": "synthetic-scale-load",
        "spec": spec.describe(),
        "limits": limits.describe(),
        "environment": {**environment_facts(), "platform_detail": platform.platform(),
                        "transport": "in-process run_query; no HTTP serialisation",
                        "llm": "scripted fixed SQL; no external model call",
                        "embedding": "deterministic mock provider; no network",
                        "concurrency_levels": args.concurrency},
        "requests_per_level": args.requests,
        "warmup_requests": args.warmup,
        "levels": [],
        "batch_stop_reason": None,
    }
    batch_started = time.perf_counter()
    wants_rag = "rag_index" in selected or "rag_retrieve" in selected
    fixture = await _prepare(workdir, spec, with_rag=wants_rag)
    from app.config.settings import settings

    report["environment"]["max_concurrent_queries"] = settings.max_concurrent_queries
    report["environment"]["serialisation"] = (
        "Per-connection lease allows one in-flight query per registered connection; "
        "the admission gate capacity is max_concurrent_queries"
    )
    report["build"] = fixture.build
    pipeline_action: Any = None
    try:
        if "pipeline" in selected:
            await _seed_pipeline_database()
            pipeline_action = await _pipeline_action()
        actions = _actions(fixture)
        for name in selected:
            if name == "rag_index":
                build_ms = float(fixture.build.get("index_build_ms") or 0.0)
                report["levels"].append({
                    "scenario": "rag_index", "concurrency": 1, "requests_planned": 1,
                    "attempts": 1, "not_attempted": 0,
                    "wall_seconds": build_ms / 1000,
                    "attempt_rps": 1 / (build_ms / 1000) if build_ms else None,
                    "success_rps": 1 / (build_ms / 1000) if build_ms else None,
                    "statuses": {"ok": 1}, "documents": fixture.build.get("documents"),
                    "index_bytes": fixture.build.get("index_bytes"),
                    "latency": latency_summary([build_ms], [build_ms]),
                    "event_loop_lag": {"samples": 0, "p50_ms": None, "p95_ms": None,
                                       "max_ms": None,
                                       "policy": "one-shot build; no sampler during ingestion"},
                    "process_peak_rss_bytes": current_rss_bytes(),
                    "stop_reason": None,
                })
                continue
            action_fn = pipeline_action if name == "pipeline" else actions.get(name)
            if action_fn is None:
                continue
            for _ in range(args.warmup):
                await action_fn()
            for concurrency in args.concurrency:
                level = await measure_level(name, concurrency, args.requests, action_fn,
                                            limits=limits, batch_started=batch_started,
                                            artifact_root=workdir)
                report["levels"].append(level)
                print(json.dumps({k: level[k] for k in ("scenario", "concurrency", "attempts",
                      "statuses", "stop_reason")}), flush=True)
                if level["stop_reason"] is not None:
                    report["batch_stop_reason"] = level["stop_reason"]
                    break
            if report["batch_stop_reason"] is not None:
                break
    finally:
        from app.db.connections import ConnectionFactory

        await ConnectionFactory.aclose_all()
    report["elapsed_seconds"] = time.perf_counter() - batch_started
    report["artifact_bytes"] = directory_bytes(workdir)
    report["final_rss_bytes"] = current_rss_bytes()
    report["measurement_scope"] = (
        "Synthetic schema and documents on this machine; orchestration capacity only. "
        "No model or embedding latency is included, so this is not end-to-end capacity."
    )
    return report


async def _seed_pipeline_database() -> None:
    """Add a small fact table to the same fixture database the pipeline queries."""
    from app.db.connections import ConnectionFactory

    connection = ConnectionFactory.get(DB_ID)
    connection.execute(
        "CREATE TABLE IF NOT EXISTS facts (id INTEGER PRIMARY KEY, bucket INTEGER, amount INTEGER)"
    )
    existing = connection.execute("SELECT COUNT(*) FROM facts").fetchone()
    if not existing or not existing[0]:
        connection.executemany("INSERT INTO facts VALUES (?, ?, ?)",
                               ((i, i % 10, i % 100) for i in range(100)))
    connection.commit()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--workdir", default=".scale_probe/run")
    parser.add_argument("--tables", type=int, default=1000)
    parser.add_argument("--columns", type=int, default=12)
    parser.add_argument("--documents", type=int, default=1000)
    parser.add_argument("--requests", type=int, default=40)
    parser.add_argument("--concurrency", default="1,2,5,10")
    parser.add_argument("--warmup", type=int, default=2)
    parser.add_argument("--scenario-seconds", type=float, default=60.0)
    parser.add_argument("--batch-seconds", type=float, default=1200.0)
    parser.add_argument("--max-rss-mib", type=float, default=2048.0)
    parser.add_argument("--max-artifact-mib", type=float, default=1024.0)
    parser.add_argument("--scenarios", default="schema_extract,keyword_retrieve")
    args = parser.parse_args(argv)
    args.concurrency = [int(value) for value in str(args.concurrency).split(",") if value]
    try:
        report = asyncio.run(run(args))
    except (ValueError, FileExistsError) as exc:
        print(f"scale probe refused: {exc}", file=sys.stderr)
        return 2
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({"levels": len(report["levels"]), "status": "finished"}), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
