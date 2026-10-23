"""Offline BM25 smoke baseline; optional externally generated SQL predictions.

Run from the repository root: python -m scripts.evaluate_quality --output result.json
--predictions accepts [{id, sql?, status?, finish_reason?, latency_ms?, cost_usd?}].
No LLM is called, and no mock embedding score is reported as semantic quality.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import platform
import sqlite3
import statistics
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any

from app.knowledge.retrieval.bm25_index import BM25Index
from app.llm.completion import IncompleteCompletionError, check_completion
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


def evaluate(
    dataset: dict[str, Any], k: int, predictions: list[dict[str, Any]] | None,
) -> dict[str, Any]:
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
    result: dict[str, Any] = {
        "dataset": dataset["version"],
        "dataset_sha256": _fingerprint(dataset),
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
        "measurement_scope": (
            "This tool calls no LLM/embedding; external SQL costs are reported only."
        ),
        "sql_model_metrics": None,
        "limitations": "Not a business/semantic accuracy estimate. No reranker or real LLM run.",
    }
    if predictions is not None:
        result["sql_model_metrics"] = evaluate_sql(dataset["sql_cases"], predictions)
    return result


class EvaluationQueryError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _fingerprint(value: object) -> str:
    try:
        encoded = json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ValueError("Evaluation input must be JSON-compatible with finite numbers") from exc
    return hashlib.sha256(encoded).hexdigest()


def _reported_number(prediction: dict[str, Any], key: str) -> float | None:
    value = prediction.get(key)
    if value is None:
        return None
    try:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError
        number = float(value)
        if not math.isfinite(number) or number < 0:
            raise ValueError
    except (ValueError, OverflowError) as exc:
        raise ValueError(f"{key} must be a finite non-negative number or null") from exc
    return number


def _sample_summary(values: list[float], case_count: int) -> dict[str, Any]:
    ordered = sorted(values)
    return {
        "sample_count": len(values), "case_count": case_count,
        "coverage": len(values) / case_count, "complete": len(values) == case_count,
        "mean": statistics.mean(values) if values else None,
        **{f"p{percent}": ordered[math.ceil(percent / 100 * len(ordered)) - 1] if ordered else None
           for percent in (50, 95, 99)},
        "percentile_method": "nearest_rank",
    }


def _reported_cost_total(predictions: list[dict[str, Any]]) -> float | None:
    values = [_reported_number(prediction, "cost_usd") for prediction in predictions]
    costs = [value for value in values if value is not None]
    try:
        return math.fsum(costs) if costs else None
    except OverflowError as exc:
        raise ValueError("Reported cost total must be finite") from exc


def _validate_predictions(
    cases: list[dict[str, Any]], predictions: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    if not isinstance(cases, list) or not cases or not isinstance(predictions, list):
        raise ValueError("Non-empty cases and a predictions list are required")
    for case in cases:
        if (not isinstance(case, dict) or not isinstance(case.get("id"), str)
                or not case["id"].strip() or not isinstance(case.get("gold_sql"), str)
                or not case["gold_sql"].strip()
                or ("ordered" in case and not isinstance(case["ordered"], bool))):
            raise ValueError("Cases require non-empty string id/gold_sql and boolean ordered")
    for prediction in predictions:
        if (not isinstance(prediction, dict) or not isinstance(prediction.get("id"), str)
                or not prediction["id"].strip()):
            raise ValueError("Predictions require a non-empty string id")
        if "status" in prediction and prediction["status"] not in ("completed", "failed"):
            raise ValueError("Prediction status must be completed or failed")
        for key in ("latency_ms", "cost_usd"):
            _reported_number(prediction, key)
    ids = {case["id"] for case in cases}
    by_id = {prediction["id"]: prediction for prediction in predictions}
    if len(ids) != len(cases) or len(by_id) != len(predictions) or set(by_id) - ids:
        raise ValueError("Duplicate case/prediction or unknown prediction IDs")
    return by_id


def _execute_evaluation_query(
    conn: sqlite3.Connection, sql: str, timeout_seconds: float, max_rows: int
) -> list[tuple[Any, ...]]:
    try:
        require_read_query(sql, "sqlite")
    except ValueError as exc:
        raise EvaluationQueryError("SQL_POLICY_REJECTED", str(exc)) from exc
    deadline = time.monotonic() + timeout_seconds
    conn.set_progress_handler(lambda: int(time.monotonic() >= deadline), 100)
    try:
        rows = conn.execute(sql).fetchmany(max_rows + 1)
        if time.monotonic() >= deadline:
            raise EvaluationQueryError("SQL_TIMEOUT", "SQL evaluation deadline exceeded")
        if len(rows) > max_rows:
            raise EvaluationQueryError("SQL_RESULT_LIMIT", "Result exceeds evaluation row limit")
        return rows
    except sqlite3.Error as exc:
        code = "SQL_TIMEOUT" if time.monotonic() >= deadline else "SQL_EXECUTION_ERROR"
        raise EvaluationQueryError(code, str(exc)) from exc
    finally:
        conn.set_progress_handler(None, 0)


def evaluate_sql(
    cases: list[dict[str, Any]], predictions: list[dict[str, Any]], *,
    timeout_seconds: float = 2, max_rows: int = 1000,
) -> dict[str, Any]:
    try:
        valid_timeout = (
            not isinstance(timeout_seconds, bool) and isinstance(timeout_seconds, (int, float))
            and math.isfinite(timeout_seconds) and timeout_seconds > 0
        )
    except OverflowError:
        valid_timeout = False
    if (not valid_timeout or isinstance(max_rows, bool) or not isinstance(max_rows, int)
            or not 1 <= max_rows < sys.maxsize):
        raise ValueError("Non-empty cases and positive finite evaluation limits are required")
    by_id = _validate_predictions(cases, predictions)
    cost_total = _reported_cost_total(predictions)  # before opening SQLite, including failed rows
    provenance = {
        "cases_sha256": _fingerprint(cases), "predictions_sha256": _fingerprint(predictions),
        "sqlite_fixture_sha256": hashlib.sha256(
            (ROOT / "tests/fixtures/test_schema.sql").read_bytes()
        ).hexdigest(),
    }
    conn = sqlite3.connect(":memory:")
    rows = []
    try:
        conn.executescript((ROOT / "tests/fixtures/test_schema.sql").read_text(encoding="utf-8"))
        conn.execute("PRAGMA query_only=ON")
        for case in cases:
            try:
                expected = _execute_evaluation_query(
                    conn, case["gold_sql"], timeout_seconds, max_rows
                )
            except EvaluationQueryError as exc:
                raise ValueError(f"Invalid gold SQL for '{case['id']}': {exc.code}") from exc
            prediction = by_id.get(case["id"], {})
            succeeded, correct, error, code = False, False, "", None
            completion_status = "missing" if not prediction else "legacy_unknown"
            execution_latency = None
            if case["id"] not in by_id:
                code, error = "MISSING_PREDICTION", "No prediction supplied"
            elif prediction.get("status") == "failed":
                code, error = "GENERATION_FAILED", "Prediction reports generation failure"
                completion_status = "generation_failed"
            else:
                try:
                    check_completion({
                        "finish_reason": prediction.get("finish_reason"), "message": prediction,
                    })
                    if prediction.get("finish_reason") == "stop":
                        completion_status = "stop"
                except IncompleteCompletionError as exc:
                    code, error = "INCOMPLETE_COMPLETION", str(exc)
                    completion_status = "rejected"
            if code is None and (
                not isinstance(prediction.get("sql"), str) or not prediction["sql"].strip()
            ):
                code, error = "EMPTY_SQL", "Prediction must contain non-empty SQL text"
            if code is None:
                started = time.perf_counter()
                try:
                    actual = _execute_evaluation_query(
                        conn, prediction["sql"], timeout_seconds, max_rows
                    )
                    succeeded = True
                except EvaluationQueryError as exc:
                    code, error = exc.code, str(exc)
                finally:
                    execution_latency = (time.perf_counter() - started) * 1000
                if succeeded:
                    correct = (
                        actual == expected if case.get("ordered")
                        else Counter(actual) == Counter(expected)
                    )
                    if not correct:
                        code, error = "RESULT_MISMATCH", "Executed result differs from gold result"
            rows.append({
                "id": case["id"], "execution_success": succeeded, "result_correct": correct,
                "error": error, "error_code": code,
                "completion_status": completion_status,
                "execution_latency_ms": execution_latency,
                "reported_generation_latency_ms": _reported_number(prediction, "latency_ms"),
                "reported_cost_usd": _reported_number(prediction, "cost_usd"),
            })
    finally:
        conn.close()
    latency = [row["reported_generation_latency_ms"] for row in rows
               if row["reported_generation_latency_ms"] is not None]
    costs = [row["reported_cost_usd"] for row in rows if row["reported_cost_usd"] is not None]
    attempts = [row["execution_latency_ms"] for row in rows
                if row["execution_latency_ms"] is not None]
    return {
        "report_version": "sql-evaluation-v2", **provenance,
        "limits": {"timeout_seconds": timeout_seconds, "max_rows": max_rows, "dialect": "sqlite"},
        "case_count": len(cases),
        "prediction_count": len(by_id),
        "execution_success_count": sum(row["execution_success"] for row in rows),
        "result_correct_count": sum(row["result_correct"] for row in rows),
        "execution_success_rate": statistics.mean(row["execution_success"] for row in rows),
        "result_correct_rate": statistics.mean(row["result_correct"] for row in rows),
        "error_counts": dict(Counter(row["error_code"] for row in rows if row["error_code"])),
        "completion_counts": dict(Counter(row["completion_status"] for row in rows)),
        "reported_generation_latency_ms": _sample_summary(latency, len(cases)),
        "reported_cost_usd": {
            "sample_count": len(costs), "case_count": len(cases),
            "coverage": len(costs) / len(cases), "complete": len(costs) == len(cases),
            "total": cost_total,
            "mean": statistics.mean(costs) if costs else None,
        },
        "execution_attempt_latency_ms": _sample_summary(attempts, len(cases)),
        "measurement_scope": {
            "rates": "All cases, including missing and failed predictions",
            "generation_and_cost": "External reports, including failed predictions; not measured",
            "execution": "Local policy+SQLite fetch attempt; excludes gold and result comparison",
            "completion": "Missing finish_reason remains legacy_unknown, not confirmed complete",
        },
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
    serialized = json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(serialized, encoding="utf-8")
    print(
        json.dumps(
            {key: value for key, value in result.items() if key != "details"}, ensure_ascii=False
        )
    )


if __name__ == "__main__":
    main()
