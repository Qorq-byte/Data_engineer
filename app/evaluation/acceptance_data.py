"""Strict input contracts for synthetic and externally frozen evaluation data."""

from __future__ import annotations

import hashlib
import math
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.evaluation.artifacts import parse_json, read_bounded_bytes, read_json
from app.evaluation.inputs import text
from app.evaluation.predictions import fingerprint

DIRECTORY = Path(__file__).resolve().parents[2] / "tests/fixtures/acceptance_v3"
SCENARIOS = ("base", "edge", "empty")
FILES = ("sql.json", "retrieval.json", "schema.sql", "base.sql", "edge.sql", "empty.sql")
SPLITS = ("dev", "holdout")
BUSINESS_PROFILE = "business-acceptance-v1"
BUSINESS_FILES = ("sql.json", "retrieval.json", "snapshot.sqlite3")


@dataclass(frozen=True)
class BusinessDataset:
    sql: dict[str, Any]
    retrieval: dict[str, Any]
    snapshot: bytes
    hashes: dict[str, str]


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


def validate_cases(rows: list[dict[str, Any]], *, business: bool = False) -> None:
    labels([row.get("id") for row in rows])
    for row in rows:
        if row.get("split") not in SPLITS:
            raise ValueError("Invalid acceptance split")
        labels(row.get("tags"), nonempty=True)
    counts = Counter(row["split"] for row in rows)
    if business:
        if set(counts) != set(SPLITS):
            raise ValueError("Business acceptance requires both dev and holdout cases")
    elif counts != {"dev": 20, "holdout": 10}:
        raise ValueError("Acceptance requires twenty dev and ten public holdout cases")


def validate_sql(value: object, *, business: bool = False) -> dict[str, Any]:
    suite = fields(value, "version scope fixtures sql_cases")
    profile = BUSINESS_PROFILE if business else "synthetic-acceptance-v3"
    scenarios = ["base"] if business else list(SCENARIOS)
    if suite["version"] != profile or suite["fixtures"] != scenarios:
        raise ValueError("Invalid SQL acceptance profile")
    text(suite["scope"], 2048)
    cases = objects(suite["sql_cases"], 1000 if business else 30)
    validate_cases(cases, business=business)
    for case in cases:
        fields(case, "id split tags question gold_sql output_columns ordered requires_order_by "
               "row_semantics permission db_id domain expected_rows annotation counterexamples")
        for key in ("question", "gold_sql", "row_semantics", "db_id", "domain"):
            text(case[key], 16384)
        if (case["permission"] != "select_only"
                or (not business and (case["db_id"], case["domain"]) != ("shop_a", "commerce"))
                or type(case["ordered"]) is not bool
                or type(case["requires_order_by"]) is not bool
                or (case["requires_order_by"] and not case["ordered"])):
            raise ValueError("Invalid SQL permission or row ordering contract")
        columns = labels(case["output_columns"], nonempty=True)
        if len(columns) > 8:
            raise ValueError("Too many acceptance columns")
        expected = fields(case["expected_rows"], " ".join(scenarios))
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
        expected_source = "manual-business-facts" if business else "manual-fixture-facts"
        if annotation["source"] != expected_source:
            raise ValueError("Explicit independent manual annotation required")
        text(annotation["derivation"], 4096)
        mutants = objects(case["counterexamples"], 10)
        if not mutants and not business:
            raise ValueError("Each SQL case requires an executable wrong-query counterexample")
        for mutant in mutants:
            fields(mutant, "sql reason")
            text(mutant["sql"], 16384)
            text(mutant["reason"], 4096)
    if business and len({case["db_id"] for case in cases}) != 1:
        raise ValueError("One business SQLite snapshot must describe one database")
    fingerprint(suite)
    return suite


def validate_retrieval(value: object, *, business: bool = False) -> dict[str, Any]:
    suite = fields(value, "version scope documents queries")
    profile = "business-retrieval-v1" if business else "synthetic-retrieval-v3"
    if suite["version"] != profile:
        raise ValueError("Invalid retrieval acceptance profile")
    text(suite["scope"], 2048)
    docs = objects(suite["documents"])
    labels([doc.get("id") for doc in docs], nonempty=True)
    for doc in docs:
        fields(doc, "id db_id domain text")
        for key in ("db_id", "domain", "text"):
            text(doc[key], 16384)
    by_id = {doc["id"]: doc for doc in docs}
    queries = objects(suite["queries"], 1000 if business else 30)
    validate_cases(queries, business=business)
    source = "manual-business-facts" if business else "manual-document-facts"
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
                or query["annotation_source"] != source
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


def business_source_paths(manifest_path: Path) -> list[Path]:
    return [manifest_path, *(manifest_path.parent / name for name in BUSINESS_FILES)]


def load_business_dataset(manifest_path: Path) -> BusinessDataset:
    manifest_raw = read_bounded_bytes(manifest_path, 16384)
    manifest = fields(parse_json(manifest_raw), "profile normalization files")
    if (manifest["profile"] != BUSINESS_PROFILE or manifest["normalization"] != "raw bytes"
            or not isinstance(manifest["files"], dict) or set(manifest["files"]) != set(
                BUSINESS_FILES
            )):
        raise ValueError("Invalid business acceptance manifest")
    inputs = {
        path.name: read_bounded_bytes(path) for path in business_source_paths(manifest_path)[1:]
    }
    digests = {name: hashlib.sha256(raw).hexdigest() for name, raw in inputs.items()}
    if manifest["files"] != digests:
        raise ValueError("Frozen business acceptance input hash mismatch")
    snapshot = inputs["snapshot.sqlite3"]
    if not snapshot.startswith(b"SQLite format 3\x00") or snapshot[18:20] != b"\x01\x01":
        raise ValueError("Business acceptance needs a checkpointed SQLite snapshot")
    return BusinessDataset(
        sql=validate_sql(parse_json(inputs["sql.json"]), business=True),
        retrieval=validate_retrieval(parse_json(inputs["retrieval.json"]), business=True),
        snapshot=snapshot,
        hashes={"manifest": hashlib.sha256(manifest_raw).hexdigest(), **digests},
    )


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
