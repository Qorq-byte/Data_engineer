"""Holdout quality summaries and frozen acceptance gates for saved business outputs."""

from __future__ import annotations

import hashlib
import math
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from app.evaluation.acceptance_data import BusinessDataset, fields
from app.evaluation.artifacts import parse_json, read_bounded_bytes

METRICS = (
    "sql_execution_success_rate",
    "sql_answer_accuracy",
    "recall_at_k",
    "no_answer_accuracy",
)
THRESHOLD_VERSION = "business-quality-thresholds-v1"


def _binary_rate(successes: int, count: int) -> dict[str, Any]:
    score, interval = None, None
    if count:
        score = successes / count
        z = 1.959963984540054
        scale = 1 + z**2 / count
        center = (score + z**2 / (2 * count)) / scale
        radius = z * math.sqrt((score * (1 - score) + z**2 / (4 * count)) / count) / scale
        interval = [max(0.0, center - radius), min(1.0, center + radius)]
    return {"score": score, "count": count, "successes": successes, "interval_95": interval}


def _recall(scores: list[float]) -> dict[str, Any]:
    count = len(scores)
    score, interval = None, None
    if count:
        score = math.fsum(scores) / count
        radius = math.sqrt(math.log(40) / (2 * count))
        interval = [max(0.0, score - radius), min(1.0, score + radius)]
    return {"score": score, "count": count, "interval_95": interval}


def _summary(sql: list[dict[str, Any]], retrieval: list[dict[str, Any]]) -> dict[str, Any]:
    answerable = [row["recall_at_k"] for row in retrieval if row["answerable"]]
    no_answer = [row for row in retrieval if not row["answerable"]]
    return {
        "metrics": {
            "sql_execution_success_rate": _binary_rate(
                sum(row["execution_success"] for row in sql), len(sql),
            ),
            "sql_answer_accuracy": _binary_rate(
                sum(row["contract_match"] for row in sql), len(sql),
            ),
            "recall_at_k": _recall(answerable),
            "no_answer_accuracy": _binary_rate(
                sum(row["no_answer_correct"] for row in no_answer), len(no_answer),
            ),
        },
        "error_counts": {
            "sql": dict(Counter(row["error_code"] for row in sql if row["error_code"])),
            "retrieval": dict(Counter(code for row in retrieval for code in row["error_codes"])),
        },
    }


def _split_slices(
    dataset: BusinessDataset, sql: list[dict[str, Any]], retrieval: list[dict[str, Any]],
) -> dict[str, Any]:
    groups: dict[tuple[str, str], tuple[list[dict[str, Any]], list[dict[str, Any]]]] = defaultdict(
        lambda: ([], []),
    )
    for track, (cases, rows) in enumerate((
        (dataset.sql["sql_cases"], sql), (dataset.retrieval["queries"], retrieval),
    )):
        labels = {row["id"]: row for row in cases}
        for detail in rows:
            case = labels[detail["id"]]
            for key in (("domain", case["domain"]), ("database", case["db_id"]),
                        *(("tag", tag) for tag in case["tags"])):
                groups[key][track].append(detail)
    dimensions: dict[str, dict[str, Any]] = {"domain": {}, "database": {}, "tag": {}}
    for (dimension, label), (sql_rows, retrieval_rows) in sorted(groups.items()):
        dimensions[dimension][label] = _summary(sql_rows, retrieval_rows)
    return dimensions


def quality_report(
    dataset: BusinessDataset, sql: dict[str, Any], retrieval: dict[str, Any],
) -> dict[str, Any]:
    sql_rows, retrieval_rows = sql["details"], retrieval["details"]
    splits = ("dev", "holdout") if sql["split"] == "all" else (sql["split"],)
    by_split = {}
    for split in splits:
        queries = [row for row in sql_rows if row["split"] == split]
        rankings = [row for row in retrieval_rows if row["split"] == split]
        by_split[split] = {
            **_summary(queries, rankings),
            "slices": _split_slices(dataset, queries, rankings),
        }
    return {
        "version": "business-quality-report-v1",
        "k": retrieval["k"],
        "overall": _summary(sql_rows, retrieval_rows),
        "by_split": by_split,
        "interval_methods": {"binary": "Wilson", "recall_at_k": "Hoeffding"},
    }


def check_thresholds(
    path: Path, dataset: BusinessDataset, report: dict[str, Any],
) -> dict[str, Any]:
    raw = read_bounded_bytes(path, 16384)
    spec = fields(parse_json(raw), "version manifest_sha256 k metrics")
    if (
        spec["version"] != THRESHOLD_VERSION
        or spec["manifest_sha256"] != dataset.hashes["manifest"]
        or type(spec["k"]) is not int
        or spec["k"] != report["k"]
    ):
        raise ValueError("Thresholds must match the frozen business manifest and retrieval K")
    requirements = fields(spec["metrics"], " ".join(METRICS))
    holdout = report["by_split"]["holdout"]["metrics"]
    results = {}
    for metric, value in requirements.items():
        rule = fields(value, "min_score min_count")
        min_score, min_count = rule["min_score"], rule["min_count"]
        if (type(min_score) not in (int, float) or not 0 <= min_score <= 1
                or type(min_count) is not int or not 1 <= min_count <= 1000):
            raise ValueError("Thresholds need a score in [0, 1] and a positive minimum count")
        score, count = holdout[metric]["score"], holdout[metric]["count"]
        results[metric] = {
            **rule, "score": score, "count": count,
            "passed": count >= min_count and score is not None and score >= min_score,
        }
    return {
        "version": THRESHOLD_VERSION,
        "sha256": hashlib.sha256(raw).hexdigest(),
        "passed": all(result["passed"] for result in results.values()),
        "metrics": results,
    }
