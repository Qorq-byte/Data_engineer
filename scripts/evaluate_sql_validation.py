"""Fixed SQL validation contracts; real read-only SQLite, no LLM accuracy claim.

python -m scripts.evaluate_sql_validation --output after.json
Add --baseline-stage10 to replay the immutable original validator without editing files.
"""

import argparse
import asyncio
import hashlib
import importlib.util
import json
import platform
import sqlite3
import subprocess
import sys
from importlib.metadata import version
from pathlib import Path
from typing import Any

from app.evaluation.artifacts import protect_inputs, read_json, write_atomic
from app.evaluation.inputs import evaluation_summary, sql_validation_dataset
from app.models.schema import ColumnSchema, SchemaSnapshot, TableSchema
from scripts.evaluate_quality import EvaluationQueryError, _execute_evaluation_query

ROOT = Path(__file__).resolve().parents[1]
BASELINE_REF = "044f1705e737a0ee8d35a4c739c4ab2b24e91d49"


def fixture_schema(conn: sqlite3.Connection) -> SchemaSnapshot:
    tables = {}
    for (name,) in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
    ):
        quoted = '"' + name.replace('"', '""') + '"'
        tables[name] = TableSchema(name=name, columns=[
            ColumnSchema(name=row[1], type=row[2])
            for row in conn.execute(f"PRAGMA table_info({quoted})")
        ])
    return SchemaSnapshot("sqlite", "main", tables=tables)


async def evaluate(dataset: dict[str, Any]) -> dict[str, Any]:
    from app.core.sql_validator import SQLValidator

    dataset = sql_validation_dataset(dataset)
    conn = sqlite3.connect(":memory:")
    rows = []
    try:
        conn.executescript((ROOT / "tests/fixtures/test_schema.sql").read_text(encoding="utf-8"))
        schema = fixture_schema(conn)
        conn.execute("PRAGMA query_only=ON")
        for case in dataset["cases"]:
            report = await SQLValidator(dialect="sqlite").validate(case["sql"], schema=schema)
            try:
                _execute_evaluation_query(conn, case["sql"], 2, 1000)
                reference, reference_error = True, None
            except EvaluationQueryError as exc:
                reference, reference_error = False, exc.code
            checks = {
                "decision": report.passed == case["expected"],
                "reference": reference == case["expected"],
                "policy_rejected": not case.get("policy") or (
                    not report.syntax_ok
                    and any("READ_ONLY_ERROR" in error for error in report.syntax_errors)
                ),
            }
            rows.append({
                "id": case["id"], "passed": all(checks.values()), "checks": checks,
                "validator_accepted": report.passed, "expected": case["expected"],
                "syntax_errors": report.syntax_errors, "schema_errors": report.schema_errors,
                "reference_execution_success": reference, "reference_error_code": reference_error,
            })
    finally:
        conn.close()
    return {
        "dataset": dataset["version"], "scope": dataset["scope"],
        "python": platform.python_version(), "sqlglot": version("sqlglot"),
        "dataset_sha256": hashlib.sha256(
            json.dumps(dataset, sort_keys=True, ensure_ascii=False).encode("utf-8")
        ).hexdigest(),
        "case_count": len(rows), "passed": sum(row["passed"] for row in rows),
        "details": rows, "model_accuracy": None, "llm_or_embedding_calls": 0,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--dataset", type=Path,
                        default=ROOT / "tests/fixtures/sql_validation_eval.json")
    parser.add_argument("--baseline-stage10", action="store_true")
    args = parser.parse_args()
    try:
        protect_inputs(args.output, [args.dataset, ROOT / "tests/fixtures/test_schema.sql"])
        dataset = sql_validation_dataset(read_json(args.dataset))
        if args.baseline_stage10:
            name = "app.core.sql_validator"
            source = subprocess.check_output(
                ["git", "show", f"{BASELINE_REF}:app/core/sql_validator.py"],
                cwd=ROOT, encoding="utf-8",
            )
            spec = importlib.util.spec_from_loader(name, loader=None)
            if spec is None:
                raise RuntimeError("Cannot load baseline validator")
            module = importlib.util.module_from_spec(spec)
            sys.modules[name] = module
            exec(compile(source, f"{BASELINE_REF}:sql_validator.py", "exec"), module.__dict__)
        result = asyncio.run(evaluate(dataset))
        write_atomic(args.output, result, prefix=".evaluation-report-")
    except (OSError, ValueError, TypeError, KeyError, sqlite3.Error, subprocess.CalledProcessError):
        parser.exit(2, "SQL validation evaluation failed; check input contracts and file access.\n")
    print(json.dumps(evaluation_summary(result), ensure_ascii=False))
    raise SystemExit(0 if args.baseline_stage10 or result["passed"] == result["case_count"] else 1)


if __name__ == "__main__":
    main()
