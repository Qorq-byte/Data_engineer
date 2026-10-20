"""Offline BM25 smoke baseline; optional externally generated SQL predictions.

Run from the repository root: python -m scripts.evaluate_quality --output result.json
--predictions expects [{id, sql, latency_ms?, cost_usd?}] for every sql_case.
No LLM is called, and no mock embedding score is reported as semantic quality.
"""

from __future__ import annotations

import argparse
import json
import math
import platform
import sqlite3
import statistics
import time
from collections import Counter
from pathlib import Path
from typing import Any

from app.knowledge.retrieval.bm25_index import BM25Index
from app.security.sql_policy import require_read_query

ROOT = Path(__file__).resolve().parents[1]


def ranking_metrics(retrieved: list[str], relevant: set[str], k: int) -> dict[str, float]:
    hits = [int(doc in relevant) for doc in retrieved[:k]]
    dcg = sum(hit / math.log2(rank + 2) for rank, hit in enumerate(hits))
    ideal = sum(1 / math.log2(rank + 2) for rank in range(min(k, len(relevant))))
    return {
        "recall_at_k": sum(hits) / len(relevant) if relevant else 0,
        "precision_at_k": sum(hits) / k,
        "mrr_at_k": next((1 / (rank + 1) for rank, hit in enumerate(hits) if hit), 0),
        "ndcg_at_k": dcg / ideal if ideal else 0,
    }


def evaluate(dataset: dict[str, Any], k: int, predictions: list[dict] | None) -> dict[str, Any]:
    bm25 = BM25Index()
    rows = []
    try:
        bm25.index(
            "quality",
            [
                {"doc_id": doc["id"], "text": doc["text"], "metadata": {"domain": doc["domain"]}}
                for doc in dataset["documents"]
            ],
        )
        for query in dataset["queries"]:
            started = time.perf_counter()
            hits = bm25.search(
                "quality", query["query"], k, metadata_filter={"domain": query["domain"]}
            )
            ids = [hit["doc_id"] for hit in hits]
            rows.append(
                {
                    "id": query["id"],
                    "retrieved": ids,
                    "latency_ms": (time.perf_counter() - started) * 1000,
                    **ranking_metrics(ids, set(query["relevant"]), k),
                }
            )
    finally:
        bm25.close()
    result = {
        "dataset": dataset["version"],
        "python": platform.python_version(),
        "retrieval_mode": "real SQLite FTS5 BM25 only; synthetic lexical smoke set",
        "k": k,
        "query_count": len(rows),
        "document_count": len(dataset["documents"]),
        "retrieval": {
            metric: statistics.mean(row[metric] for row in rows)
            for metric in ["recall_at_k", "precision_at_k", "mrr_at_k", "ndcg_at_k", "latency_ms"]
        },
        "details": rows,
        "embedding_or_llm_cost_usd": 0,
        "sql_model_metrics": None,
        "limitations": "Not a business/semantic accuracy estimate. No reranker or real LLM run.",
    }
    if predictions is not None:
        result["sql_model_metrics"] = evaluate_sql(dataset["sql_cases"], predictions)
    return result


def evaluate_sql(cases: list[dict], predictions: list[dict]) -> dict[str, Any]:
    by_id = {prediction["id"]: prediction for prediction in predictions}
    if len(by_id) != len(predictions) or set(by_id) - {case["id"] for case in cases}:
        raise ValueError("Duplicate or unknown prediction IDs")
    conn = sqlite3.connect(":memory:")
    conn.executescript((ROOT / "tests/fixtures/test_schema.sql").read_text(encoding="utf-8"))
    conn.execute("PRAGMA query_only=ON")
    rows = []
    try:
        for case in cases:
            expected = conn.execute(case["gold_sql"]).fetchall()
            prediction = by_id.get(case["id"], {})
            started = time.perf_counter()
            deadline = time.monotonic() + 2
            conn.set_progress_handler(
                lambda deadline=deadline: int(time.monotonic() > deadline), 1000
            )
            succeeded, correct, error = False, False, ""
            try:
                sql = prediction.get("sql", "")
                require_read_query(sql, "sqlite")
                actual = conn.execute(sql).fetchmany(1001)
                if len(actual) > 1000:
                    raise ValueError("Result exceeds evaluation row limit")
                succeeded = True
                correct = (
                    actual == expected
                    if case.get("ordered")
                    else Counter(actual) == Counter(expected)
                )
            except Exception as exc:
                error = str(exc)
            finally:
                conn.set_progress_handler(None, 0)
            rows.append(
                {
                    "id": case["id"],
                    "execution_success": succeeded,
                    "result_correct": correct,
                    "error": error,
                    "execution_latency_ms": (time.perf_counter() - started) * 1000,
                    "reported_generation_latency_ms": prediction.get("latency_ms"),
                    "reported_cost_usd": prediction.get("cost_usd"),
                }
            )
    finally:
        conn.close()
    return {
        "case_count": len(cases),
        "execution_success_rate": statistics.mean(row["execution_success"] for row in rows),
        "result_correct_rate": statistics.mean(row["result_correct"] for row in rows),
        "details": rows,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=ROOT / "tests/fixtures/retrieval_eval.json")
    parser.add_argument("--predictions", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--k", type=int, default=3)
    args = parser.parse_args()
    if args.k < 1:
        parser.error("k must be positive")
    predictions = (
        json.loads(args.predictions.read_text(encoding="utf-8")) if args.predictions else None
    )
    result = evaluate(json.loads(args.dataset.read_text(encoding="utf-8")), args.k, predictions)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {key: value for key, value in result.items() if key != "details"}, ensure_ascii=False
        )
    )


if __name__ == "__main__":
    main()
