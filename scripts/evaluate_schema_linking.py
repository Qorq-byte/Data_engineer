"""Synthetic linking boundary regression; no semantic accuracy or production timing claim.

Run: python -m scripts.evaluate_schema_linking --output result.json
The real retriever/node consume explicitly injected RAG boundary fixtures.
Use evaluate_quality separately for the real SQLite FTS5 lexical smoke set.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
from pathlib import Path
from typing import Any

from app.evaluation.artifacts import protect_inputs, read_json, write_atomic
from app.evaluation.inputs import evaluation_summary, schema_linking_dataset
from app.knowledge.retrieval.schema_rag import SchemaSearchResult
from app.knowledge.schema_retriever import SchemaRetriever
from app.models.schema import ColumnSchema, ForeignKey, SchemaSnapshot, TableSchema
from app.nodes.base import NodeInput
from app.nodes.schema_linking import SchemaLinkingNode

ROOT = Path(__file__).resolve().parents[1]


def build_schema(dataset: dict[str, Any]) -> SchemaSnapshot:
    return SchemaSnapshot(
        database_type="sqlite",
        database_name="fixture",
        tables={
            name: TableSchema(
                name=name,
                columns=[ColumnSchema(name=col, type="TEXT") for col in spec["columns"]],
                foreign_keys=[
                    ForeignKey(f"fk_{name}_{i}", column, target, ref_column)
                    for i, (column, target, ref_column) in enumerate(spec["references"])
                ],
            )
            for name, spec in dataset["tables"].items()
        },
    )


class FixtureSearch:
    """Deterministic search seam; intentionally not an embedding or BM25 model."""

    def __init__(self, case: dict[str, Any]) -> None:
        self.case = case

    async def find_relevant_tables(self, query: str, **kwargs: Any) -> list[SchemaSearchResult]:
        if self.case.get("error"):
            raise RuntimeError("synthetic private backend failure")
        return [
            SchemaSearchResult(doc_id=f"fixture-{i}", rrf_score=0.8, **hit)
            for i, hit in enumerate(self.case.get("hits", []))
        ]


async def evaluate(dataset: dict[str, Any]) -> dict[str, Any]:
    dataset = schema_linking_dataset(dataset)
    schema = build_schema(dataset)
    rows = []
    for case in dataset["cases"]:
        retriever = None if case.get("no_retriever") else SchemaRetriever(FixtureSearch(case))
        output = await SchemaLinkingNode(retriever).execute(
            NodeInput(case["query"], context={"schema": schema}, config={
                "db_id": "fixture", "fk_expand": case.get("fk_expand", True),
            })
        )
        tables = output.context["linked_tables"]
        columns = output.context["linked_columns"]
        warnings = output.metadata.get("warnings", [])
        linked = output.context["linked_schema"]
        checks = {
            "table_set": set(tables) == set(case["expected"]),
            "columns_valid": all(
                col.get("table") in schema.tables
                and schema.tables[col["table"]].get_column(col["name"]) is not None
                for col in columns
            ),
            "only_source_tables": set(tables) <= set(schema.tables),
            "warning_visible": bool(warnings) == case.get("expect_warning", False),
        }
        rows.append({
            "id": case["id"], "tables": tables, "expected": case["expected"],
            "columns": columns, "warnings": warnings, "checks": checks,
            "passed": all(checks.values()),
            "detailed_schema_columns": sum(len(t.columns) for t in linked.tables.values())
            if linked else 0,
        })
    return {
        "dataset": dataset["version"], "scope": dataset["scope"],
        "dataset_sha256": hashlib.sha256(
            json.dumps(dataset, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest(),
        "case_count": len(rows), "passed": sum(row["passed"] for row in rows),
        "details": rows, "llm_or_embedding_calls": 0,
        "limitations": "Functional boundary fixtures only; no semantic retrieval or SQL accuracy.",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path,
                        default=ROOT / "tests/fixtures/schema_linking_eval.json")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        protect_inputs(args.output, [args.dataset])
        dataset = schema_linking_dataset(read_json(args.dataset))
        result = asyncio.run(evaluate(dataset))
        write_atomic(args.output, result, prefix=".evaluation-report-")
    except (OSError, ValueError, TypeError, KeyError):
        parser.exit(2, "Schema linking evaluation failed; check input contracts and file access.\n")
    print(json.dumps(evaluation_summary(result), ensure_ascii=False))
    raise SystemExit(0 if result["passed"] == result["case_count"] else 1)


if __name__ == "__main__":
    main()
