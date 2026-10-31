"""Strict input contracts and frozen identities for the public synthetic v3 set."""

from __future__ import annotations

import hashlib
import math
from collections import Counter
from pathlib import Path
from typing import Any

from app.evaluation.artifacts import read_json
from app.evaluation.inputs import text
from app.evaluation.predictions import fingerprint

DIRECTORY = Path(__file__).resolve().parents[2] / "tests/fixtures/acceptance_v3"
SCENARIOS = ("base", "edge", "empty")
FILES = ("sql.json", "retrieval.json", "schema.sql", "base.sql", "edge.sql", "empty.sql")
SPLITS = ("dev", "holdout")


def fields(value: object, names: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != set(names.split()):
        raise ValueError("Invalid acceptance object fields")
    return value


def objects(value: object, maximum: int = 1000) -> list[dict[str, Any]]:
    if (not isinstance(value, list) or len(value) > maximum
            or any(not isinstance(row, dict) for row in value)):
        raise ValueError("Invalid acceptance object list")
    return value


def labels(value: object, *, nonempty: bool = False) -> list[str]:
    if not isinstance(value, list) or len(value) > 1000 or (nonempty and not value):
        raise ValueError("Invalid acceptance labels")
    result = [text(item, 512) for item in value]
    if len(set(result)) != len(result):
        raise ValueError("Duplicate acceptance label")
    return result


def validate_cases(rows: list[dict[str, Any]]) -> None:
    if len(rows) != 30:
        raise ValueError("Acceptance requires exactly thirty cases per track")
    labels([row.get("id") for row in rows])
    for row in rows:
        if row.get("split") not in SPLITS:
            raise ValueError("Invalid acceptance split")
        labels(row.get("tags"), nonempty=True)
    if Counter(row["split"] for row in rows) != {"dev": 20, "holdout": 10}:
        raise ValueError("Acceptance requires twenty dev and ten public holdout cases")


def validate_sql(value: object) -> dict[str, Any]:
    suite = fields(value, "version scope fixtures sql_cases")
    if suite["version"] != "synthetic-acceptance-v3" or suite["fixtures"] != list(SCENARIOS):
        raise ValueError("Invalid SQL acceptance profile")
    text(suite["scope"], 2048)
    cases = objects(suite["sql_cases"], 30)
    validate_cases(cases)
    for case in cases:
        fields(case, "id split tags question gold_sql output_columns ordered requires_order_by "
               "row_semantics permission db_id domain expected_rows annotation counterexamples")
        for key in ("question", "gold_sql", "row_semantics"):
            text(case[key], 16384)
        if (case["permission"] != "select_only" or case["db_id"] != "shop_a"
                or case["domain"] != "commerce"
                or type(case["ordered"]) is not bool
                or type(case["requires_order_by"]) is not bool
                or (case["requires_order_by"] and not case["ordered"])):
            raise ValueError("Invalid SQL permission or row ordering contract")
        columns = labels(case["output_columns"], nonempty=True)
        if len(columns) > 8:
            raise ValueError("Too many acceptance columns")
        expected = fields(case["expected_rows"], "base edge empty")
        for rows in expected.values():
            if not isinstance(rows, list) or len(rows) > 1000:
                raise ValueError("Invalid expected rows")
            for row in rows:
                if not isinstance(row, list) or len(row) != len(columns):
                    raise ValueError("Expected row width mismatch")
                for cell in row:
                    if cell is None:
                        continue
                    if isinstance(cell, str):
                        text(cell, 4096, empty=True)
                    elif (type(cell) not in (int, float) or not -1e12 <= cell <= 1e12
                          or not math.isfinite(cell)):
                        raise ValueError("Invalid expected cell")
        annotation = fields(case["annotation"], "source derivation")
        if annotation["source"] != "manual-fixture-facts":
            raise ValueError("Explicit independent manual annotation required")
        text(annotation["derivation"], 4096)
        mutants = objects(case["counterexamples"], 10)
        if not mutants:
            raise ValueError("Each SQL case requires an executable wrong-query counterexample")
        for mutant in mutants:
            fields(mutant, "sql reason")
            text(mutant["sql"], 16384)
            text(mutant["reason"], 4096)
    fingerprint(suite)
    return suite


def validate_retrieval(value: object) -> dict[str, Any]:
    suite = fields(value, "version scope documents queries")
    if suite["version"] != "synthetic-retrieval-v3":
        raise ValueError("Invalid retrieval acceptance profile")
    text(suite["scope"], 2048)
    docs = objects(suite["documents"])
    labels([doc.get("id") for doc in docs], nonempty=True)
    for doc in docs:
        fields(doc, "id db_id domain text")
        for key in ("db_id", "domain", "text"):
            text(doc[key], 16384)
    by_id = {doc["id"]: doc for doc in docs}
    queries = objects(suite["queries"], 30)
    validate_cases(queries)
    for query in queries:
        fields(query, "id query db_id domain split tags relevant relevant_reasons hard_negatives "
               "negative_reasons answerable annotation_source no_answer_reason bad_ranking")
        for key in ("query", "db_id", "domain"):
            text(query[key], 16384)
        if (query["db_id"] not in {doc["db_id"] for doc in docs}
                or query["domain"] not in {doc["domain"] for doc in docs}):
            raise ValueError("Unknown retrieval database or domain")
        relevant = labels(query["relevant"])
        negative = labels(query["hard_negatives"], nonempty=True)
        bad = labels(query["bad_ranking"], nonempty=True)
        if (type(query["answerable"]) is not bool or query["answerable"] != bool(relevant)
                or query["annotation_source"] != "manual-document-facts"
                or set(relevant) & set(negative) or not set(bad) <= set(negative)):
            raise ValueError("Invalid qrels answerability, source or negative labels")
        if not query["answerable"]:
            text(query["no_answer_reason"], 4096)
        elif query["no_answer_reason"] != "":
            raise ValueError("Answerable qrels cannot have a no-answer rationale")
        for names, reasons in ((relevant, query["relevant_reasons"]),
                              (negative, query["negative_reasons"])):
            if not isinstance(reasons, dict) or set(reasons) != set(names):
                raise ValueError("Every relevance label needs its own rationale")
            for identity in names:
                if identity not in by_id:
                    raise ValueError("Unknown relevance document")
                text(reasons[identity], 4096)
        for identity in relevant:
            if any(by_id[identity][key] != query[key] for key in ("db_id", "domain")):
                raise ValueError("Relevant document outside query scope")
    fingerprint(suite)
    return suite


def source_paths() -> list[Path]:
    return [DIRECTORY / name for name in (*FILES, "manifest.json")]


def verify_manifest() -> dict[str, Any]:
    manifest = fields(read_json(DIRECTORY / "manifest.json"), "profile normalization files")
    if (manifest["profile"] != "synthetic-acceptance-v3"
            or manifest["normalization"] != "UTF-8 bytes with CRLF normalized to LF"
            or not isinstance(manifest["files"], dict) or set(manifest["files"]) != set(FILES)):
        raise ValueError("Invalid frozen acceptance manifest")
    actual = {name: hashlib.sha256((DIRECTORY / name).read_bytes().replace(b"\r\n", b"\n"))
              .hexdigest() for name in FILES}
    if manifest["files"] != actual:
        raise ValueError("Frozen acceptance input hash mismatch")
    return manifest
