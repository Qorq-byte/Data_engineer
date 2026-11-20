"""Run all bounded offline quality probes and write one truthful aggregate report.

This command never calls an LLM, embedding provider or external database. It is a
contract/retrieval/orchestration baseline for repository fixtures, not a business
accuracy, semantic recall or production capacity measurement.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run_probe(name: str, output: Path, args: list[str]) -> dict[str, Any]:
    command = [sys.executable, "-m", *args, "--output", str(output)]
    completed = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, check=False)
    if completed.returncode != 0:
        raise RuntimeError(f"{name} failed: {completed.stdout[-400:]} {completed.stderr[-400:]}")
    report = json.loads(output.read_text(encoding="utf-8"))
    return {
        "report": str(output.relative_to(ROOT)), "sha256": digest(output),
        "stdout": completed.stdout.strip()[-500:], "result": report,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--requests", type=int, default=20)
    parser.add_argument("--rows", type=int, default=1000)
    args = parser.parse_args(argv)
    output = args.output.resolve()
    if output.exists() or not 1 <= args.requests <= 1000 or not 1 <= args.rows <= 100_000:
        parser.error("output must be new; requests and rows are bounded")
    output.parent.mkdir(parents=True, exist_ok=True)
    reports: dict[str, Any] = {}
    reports["retrieval"] = run_probe(
        "quality", output.parent / "quality.json", ["scripts.evaluate_quality"],
    )
    reports["schema_linking"] = run_probe(
        "schema_linking", output.parent / "schema-linking.json",
        ["scripts.evaluate_schema_linking"],
    )
    reports["sql_validation"] = run_probe(
        "sql_validation", output.parent / "sql-validation.json",
        ["scripts.evaluate_sql_validation"],
    )
    reports["sql_contracts"] = run_probe(
        "sql_contracts", output.parent / "sql-contracts.json", ["scripts.evaluate_sql_contracts"],
    )
    load_output = output.parent / "load.json"
    command = [sys.executable, "-m", "scripts.load_test", "--output", str(load_output),
               "--requests", str(args.requests), "--rows", str(args.rows)]
    completed = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, check=False)
    if completed.returncode != 0:
        raise RuntimeError(f"load_test failed: {completed.stdout[-400:]} {completed.stderr[-400:]}")
    reports["capacity"] = {
        "report": str(load_output.relative_to(ROOT)), "sha256": digest(load_output),
        "stdout_tail": completed.stdout[-1000:],
        "result": json.loads(load_output.read_text(encoding="utf-8")),
    }
    aggregate = {
        "version": "synthetic-quality-capacity-baseline-v1",
        "date": "2026-11-18", "python": platform.python_version(),
        "interpreter": str(Path(sys.executable).resolve().relative_to(ROOT)),
        "fixture_scope": {
            "retrieval": "tests/fixtures/retrieval_eval.json; real SQLite FTS5 BM25",
            "schema_linking": (
                "tests/fixtures/schema_linking_eval.json; deterministic boundary seam"
            ),
            "sql_validation": "tests/fixtures/sql_validation_eval.json; real temporary SQLite",
            "sql_contracts": "tests/fixtures/model_eval_v2; three temporary SQLite scenarios",
            "capacity": (
                f"in-process ASGI/SQLite; {args.requests} requests per level; {args.rows} row cap"
            ),
        },
        "external_calls": {"llm": 0, "embedding": 0, "database": 0},
        "reports": reports,
        "interpretation": {
            "accuracy": "Not measured: no model predictions supplied.",
            "recall": "Lexical BM25 binary relevance only; not semantic or business recall.",
            "capacity": (
                "Local orchestration/backpressure probe only; no production SLO, billing "
                "or network model."
            ),
            "required_external_inputs": [
                "Frozen business questions and independent gold SQL/result labels",
                "Semantic relevance annotations and production embedding/model configuration",
                "Target deployment, concurrency budget, latency/error SLO and cost boundaries",
            ],
        },
    }
    output.write_text(json.dumps(aggregate, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(output), "probes": list(reports)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
