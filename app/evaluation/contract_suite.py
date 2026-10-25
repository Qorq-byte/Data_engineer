"""Versioned four-question contracts checked against three independent SQLite datasets."""

from __future__ import annotations

import math
import sqlite3
from collections import Counter
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import sqlglot

from app.evaluation.artifacts import read_json
from app.evaluation.inputs import sql_cases, text
from app.evaluation.predictions import fingerprint, validate_exported_predictions
from app.evaluation.sqlite_query import EvaluationQueryError, QueryResult, execute_query
from app.llm.completion import IncompleteCompletionError, check_completion

ROOT = Path(__file__).resolve().parents[2]
DIRECTORY = ROOT / "tests/fixtures/model_eval_v2"
DATASET = DIRECTORY / "cases.json"
PROFILE = "synthetic-sqlite-model-v2"
SCENARIOS = ("base", "edge", "empty")


def suite_paths() -> list[Path]:
    return [DATASET, DIRECTORY / "schema.sql", *(DIRECTORY / f"{s}.sql" for s in SCENARIOS)]


def fixture_sql(scenario: str) -> str:
    if scenario not in SCENARIOS:
        raise ValueError("Unknown contract scenario")
    return (DIRECTORY / "schema.sql").read_text(encoding="utf-8") + "\n" + (
        DIRECTORY / f"{scenario}.sql"
    ).read_text(encoding="utf-8")


@contextmanager
def scenario_database(scenario: str) -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(":memory:")
    try:
        conn.execute("PRAGMA foreign_keys=ON")
        conn.executescript(fixture_sql(scenario))
        conn.execute("PRAGMA query_only=ON")
        yield conn
    finally:
        conn.close()


def _matches(case: dict[str, Any], scenario: str, result: QueryResult) -> bool:
    return bool(list(result.columns) == case["output_columns"]
                and [list(row) for row in result.rows] == case["expected_rows"][scenario])


def _has_required_order(case: dict[str, Any], sql: str) -> bool:
    return not case["requires_order_by"] or sqlglot.parse_one(sql, read="sqlite").args.get(
        "order"
    ) is not None


def validate_suite(value: object) -> dict[str, Any]:
    if (not isinstance(value, dict)
            or set(value) != {"version", "scope", "fixtures", "sql_cases"}
            or value.get("version") != "synthetic-sqlite-contract-v2"
            or value.get("fixtures") != list(SCENARIOS)):
        raise ValueError("Invalid contract suite profile")
    text(value.get("scope"), 1024)
    cases = sql_cases(value.get("sql_cases"))
    if len(cases) != 4:
        raise ValueError("Contract suite requires four cases")
    for case in cases:
        fields = {"id", "question", "gold_sql", "ordered", "output_columns", "expected_rows",
                  "requires_order_by"}
        if (set(case) != fields
                or case.get("ordered") is not True
                or type(case.get("requires_order_by")) is not bool):
            raise ValueError("Invalid contract case fields")
        text(case.get("question"), 16384)
        columns = case.get("output_columns")
        if not isinstance(columns, list) or not 1 <= len(columns) <= 8:
            raise ValueError("Invalid contract columns")
        for column in columns:
            text(column, 128)
        if len(set(columns)) != len(columns):
            raise ValueError("Duplicate contract columns")
        expected = case.get("expected_rows")
        if not isinstance(expected, dict) or set(expected) != set(SCENARIOS):
            raise ValueError("Invalid expected scenarios")
        for rows in expected.values():
            if not isinstance(rows, list) or len(rows) > 1000:
                raise ValueError("Expected rows exceed limit")
            for row in rows:
                if not isinstance(row, list) or len(row) != len(columns):
                    raise ValueError("Expected row width mismatch")
                for cell in row:
                    if cell is None:
                        continue
                    if isinstance(cell, str):
                        text(cell, 4096, empty=True)
                    elif type(cell) not in (int, float) or not -1e12 <= cell <= 1e12:
                        raise ValueError("Invalid expected cell")
    fingerprint(value)
    # Labels are hand-authored independently; never infer them from the gold query.
    for scenario in SCENARIOS:
        with scenario_database(scenario) as conn:
            for case in cases:
                try:
                    result = execute_query(conn, case["gold_sql"])
                except EvaluationQueryError:
                    raise ValueError("Contract gold SQL was rejected") from None
                if (not _matches(case, scenario, result)
                        or not _has_required_order(case, case["gold_sql"])):
                    raise ValueError("Contract gold and manual labels disagree")
    return value


def load_suite() -> dict[str, Any]:
    return validate_suite(read_json(DATASET))


def _predictions(
    suite: dict[str, Any], value: object,
) -> dict[str, dict[str, Any]]:
    if not isinstance(value, list) or len(value) > 4:
        raise ValueError("Invalid contract predictions")
    allowed = {case["id"] for case in suite["sql_cases"]}
    by_id = {}
    for row in value:
        if not isinstance(row, dict):
            raise ValueError("Contract predictions must be objects")
        identity = text(row.get("id"), 512)
        if identity not in allowed or identity in by_id:
            raise ValueError("Unknown or duplicate contract prediction")
        if row.get("status", "completed") not in ("completed", "failed"):
            raise ValueError("Invalid contract prediction status")
        if isinstance(row.get("sql"), str):
            text(row["sql"], empty=True, allow_nul=True)
        for key in ("cost_usd", "latency_ms"):
            number = row.get(key)
            try:
                invalid = number is not None and (
                    isinstance(number, bool) or not isinstance(number, (int, float))
                    or not math.isfinite(number) or number < 0
                )
            except OverflowError:
                invalid = True
            if invalid:
                raise ValueError("Invalid prediction telemetry")
        by_id[identity] = row
    fingerprint(value)
    validate_exported_predictions(suite["sql_cases"], value, dataset=suite)
    return by_id


def _rejection(prediction: dict[str, Any] | None) -> str | None:
    if prediction is None:
        return "MISSING_PREDICTION"
    if prediction.get("status") == "failed":
        return "GENERATION_FAILED"
    try:
        check_completion({"finish_reason": prediction.get("finish_reason"), "message": prediction})
    except IncompleteCompletionError:
        return "INCOMPLETE_COMPLETION"
    if prediction.get("finish_reason") != "stop":
        return "UNCONFIRMED_COMPLETION"
    if not isinstance(prediction.get("sql"), str) or not prediction["sql"].strip():
        return "EMPTY_SQL"
    return None


def evaluate_suite(suite: object, predictions: object) -> dict[str, Any]:
    suite = validate_suite(suite)
    by_id = _predictions(suite, predictions)
    details = []
    for scenario in SCENARIOS:
        with scenario_database(scenario) as conn:
            for case in suite["sql_cases"]:
                prediction = by_id.get(case["id"])
                code = _rejection(prediction)
                executed, matched = False, False
                if code is None and prediction is not None:
                    try:
                        result = execute_query(conn, prediction["sql"])
                        executed = True
                        if not _matches(case, scenario, result):
                            code = ("COLUMN_MISMATCH"
                                    if list(result.columns) != case["output_columns"]
                                    else "RESULT_MISMATCH")
                        elif not _has_required_order(case, prediction["sql"]):
                            code = "ORDER_BY_REQUIRED"
                        else:
                            matched = True
                    except EvaluationQueryError as exc:
                        code = exc.code
                details.append({"id": case["id"], "scenario": scenario,
                                "execution_success": executed, "contract_match": matched,
                                "error_code": code})
    robust = sum(all(row["contract_match"] for row in details if row["id"] == case["id"])
                 for case in suite["sql_cases"])
    return {
        "report_version": "sql-contract-evaluation-v1", "profile": PROFILE,
        "suite_sha256": fingerprint(suite), "predictions_sha256": fingerprint(predictions),
        "fixture_sha256": {s: fingerprint(fixture_sql(s)) for s in SCENARIOS},
        "gold_self_check_count": 12, "case_count": 4, "prediction_count": len(by_id),
        "check_count": 12, "execution_success_count": sum(r["execution_success"] for r in details),
        "contract_match_count": sum(r["contract_match"] for r in details),
        "robust_match_count": robust, "robust_match_rate": robust / 4,
        "error_counts": dict(Counter(r["error_code"] for r in details if r["error_code"])),
        "actual_cost_usd": None, "details": details,
        "measurement_scope": "Four predictions, three local datasets; not twelve model calls. "
                             "Not comparable to v1 scores or a business accuracy estimate.",
    }
