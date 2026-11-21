"""Manual SQL contracts and saved predictions on three bounded synthetic datasets."""

from __future__ import annotations

import sqlite3
from collections import Counter
from collections.abc import Iterator
from contextlib import contextmanager, nullcontext
from typing import Any

import sqlglot

from app.db.sqlite_source import read_scope
from app.evaluation import acceptance_data as data
from app.evaluation.artifacts import read_json
from app.evaluation.inputs import text
from app.evaluation.predictions import fingerprint, validate_exported_predictions
from app.evaluation.sqlite_query import EvaluationQueryError, QueryResult, execute_query
from app.llm.completion import IncompleteCompletionError, check_completion


def load_sql() -> dict[str, Any]:
    return data.validate_sql(read_json(data.DIRECTORY / "sql.json"))


@contextmanager
def scenario_database(scenario: str, snapshot: bytes | None = None) -> Iterator[sqlite3.Connection]:
    if scenario not in data.SCENARIOS:
        raise ValueError("Unknown acceptance scenario")
    conn = sqlite3.connect(":memory:")
    try:
        if snapshot is None:
            conn.execute("PRAGMA foreign_keys=ON")
            for name in ("schema.sql", f"{scenario}.sql"):
                conn.executescript((data.DIRECTORY / name).read_text(encoding="utf-8"))
        else:
            conn.deserialize(snapshot)
            conn.execute("PRAGMA trusted_schema=OFF")
        conn.execute("PRAGMA query_only=ON")
        with read_scope(conn) if snapshot is not None else nullcontext():
            yield conn
    finally:
        conn.close()


def matches(case: dict[str, Any], scenario: str, result: QueryResult, sql: str) -> bool:
    if list(result.columns) != case["output_columns"]:
        return False
    if (case["requires_order_by"]
            and sqlglot.parse_one(sql, read="sqlite").args.get("order") is None):
        return False
    expected = [tuple(row) for row in case["expected_rows"][scenario]]
    return (result.rows == expected if case["ordered"] else
            Counter(result.rows) == Counter(expected))


def sql_self_check(suite: object, *, snapshot: bytes | None = None) -> dict[str, Any]:
    suite = data.validate_sql(suite, business=snapshot is not None)
    checks = 0
    killed: dict[str, list[str]] = {}
    for scenario in suite["fixtures"]:
        with scenario_database(scenario, snapshot) as conn:
            for case in suite["sql_cases"]:
                sql = case["gold_sql"]
                if not matches(case, scenario, execute_query(conn, sql), sql):
                    raise ValueError(f"Manual SQL labels disagree: {case['id']}/{scenario}")
                checks += 1
                for index, mutant in enumerate(case["counterexamples"]):
                    identity = f"{case['id']}:{index}"
                    killed.setdefault(identity, [])
                    # Invalid SQL is not accepted as proof of semantic discrimination.
                    result = execute_query(conn, mutant["sql"])
                    if not matches(case, scenario, result, mutant["sql"]):
                        killed[identity].append(scenario)
    if any(not scenarios for scenarios in killed.values()):
        raise ValueError("Wrong SQL counterexample survived every scenario")
    source = "manual fixture facts" if snapshot is None else "manual business facts"
    return {"gold_check_count": checks, "counterexample_count": len(killed),
            "counterexamples_killed": len(killed), "counterexample_witnesses": killed,
            "label_source": f"{source}; SQL execution only checks labels"}


def selected(rows: list[dict[str, Any]], split: str) -> list[dict[str, Any]]:
    if split not in (*data.SPLITS, "all"):
        raise ValueError("Unknown acceptance split")
    return [row for row in rows if split == "all" or row["split"] == split]


def predictions_by_id(value: object, cases: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    predictions = data.objects(value, len(cases))
    identities = data.labels([row.get("id") for row in predictions])
    if not set(identities) <= {case["id"] for case in cases}:
        raise ValueError("Unknown acceptance prediction id")
    fingerprint(predictions)
    return dict(zip(identities, predictions, strict=True))


def evaluate_sql(
    suite: object, predictions: object, split: str = "all", *, snapshot: bytes | None = None,
) -> dict[str, Any]:
    suite = data.validate_sql(suite, business=snapshot is not None)
    all_cases = suite["sql_cases"]
    by_id = predictions_by_id(predictions, all_cases)
    validate_exported_predictions(all_cases, list(by_id.values()), dataset=suite)
    for row in by_id.values():
        if row.get("status", "completed") not in ("completed", "failed"):
            raise ValueError("Invalid SQL prediction status")
        if row.get("sql") is not None:
            text(row["sql"], empty=True, allow_nul=True)
    cases = selected(all_cases, split)
    details = []
    for scenario in suite["fixtures"]:
        with scenario_database(scenario, snapshot) as conn:
            for case in cases:
                prediction = by_id.get(case["id"])
                code = None
                if prediction is None:
                    code = "MISSING_PREDICTION"
                elif prediction.get("status") == "failed":
                    code = "GENERATION_FAILED"
                else:
                    try:
                        check_completion({"finish_reason": prediction.get("finish_reason"),
                                          "message": prediction})
                    except IncompleteCompletionError:
                        code = "INCOMPLETE_COMPLETION"
                    if code is None and prediction.get("finish_reason") != "stop":
                        code = "UNCONFIRMED_COMPLETION"
                    if code is None and (not isinstance(prediction.get("sql"), str)
                                         or not prediction["sql"].strip()):
                        code = "EMPTY_SQL"
                executed = False
                if code is None and prediction is not None:
                    try:
                        result = execute_query(conn, prediction["sql"])
                        executed = True
                        if list(result.columns) != case["output_columns"]:
                            code = "COLUMN_MISMATCH"
                        elif not matches(case, scenario, result, prediction["sql"]):
                            code = "CONTRACT_MISMATCH"
                    except EvaluationQueryError as exc:
                        code = exc.code
                details.append({"id": case["id"], "scenario": scenario, "split": case["split"],
                                "execution_success": executed, "contract_match": code is None,
                                "error_code": code})
    robust = sum(all(r["contract_match"] for r in details if r["id"] == case["id"])
                 for case in cases)
    scope = "synthetic contracts" if snapshot is None else "frozen business SQLite snapshot"
    return {"split": split, "case_count": len(cases), "check_count": len(details),
            "prediction_count": sum(case["id"] in by_id for case in cases),
            "provided_prediction_count": len(by_id), "robust_match_count": robust,
            "robust_match_rate": robust / len(cases),
            "execution_success_count": sum(r["execution_success"] for r in details),
            "contract_match_count": sum(r["contract_match"] for r in details),
            "error_counts": dict(Counter(r["error_code"] for r in details if r["error_code"])),
            "predictions_sha256": fingerprint(predictions), "actual_cost_usd": None,
            "measurement_scope": f"Saved predictions on {scope}; not model calls",
            "details": details}
