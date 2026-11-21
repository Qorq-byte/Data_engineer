"""Replay externally frozen labels and snapshots through the offline CLI."""

import hashlib
import json
import sqlite3
from contextlib import closing

import pytest

from app.evaluation.acceptance_data import BUSINESS_FILES, BUSINESS_PROFILE
from app.evaluation.artifacts import read_json, write_atomic
from scripts.evaluate_acceptance import main


def freeze(directory):
    manifest = directory / "manifest.json"
    files = {name: hashlib.sha256((directory / name).read_bytes()).hexdigest()
             for name in BUSINESS_FILES}
    write_atomic(manifest, {
        "profile": BUSINESS_PROFILE, "normalization": "raw bytes", "files": files,
    })
    return manifest


@pytest.fixture
def business_dataset(tmp_path):
    fixture = tmp_path / "snapshot.sqlite3"
    with closing(sqlite3.connect(fixture)) as conn, conn:
        conn.execute("CREATE TABLE invoices (id INTEGER PRIMARY KEY, amount INTEGER)")
        conn.executemany("INSERT INTO invoices VALUES (?, ?)", [(1, 100), (2, 250)])
    questions = [
        ("dev-1", "dev", "Count invoices", "SELECT count(*) AS total FROM invoices", 2),
        ("test-1", "holdout", "Sum amounts", "SELECT sum(amount) AS total FROM invoices", 350),
        ("test-2", "holdout", "Largest amount", "SELECT max(amount) AS total FROM invoices", 250),
    ]
    write_atomic(tmp_path / "sql.json", {
        "version": BUSINESS_PROFILE, "scope": "De-identified invoices", "fixtures": ["base"],
        "sql_cases": [
            {
                "id": identity, "split": split, "tags": ["billing", f"case:{identity}"],
                "question": question,
                "gold_sql": sql, "output_columns": ["total"], "ordered": False,
                "requires_order_by": False, "row_semantics": "Single aggregate",
                "permission": "select_only", "db_id": "billing", "domain": "accounts",
                "expected_rows": {"base": [[total]]},
                "annotation": {"source": "manual-business-facts", "derivation": "Two invoices"},
                "counterexamples": [],
            }
            for identity, split, question, sql, total in questions
        ],
    })
    write_atomic(tmp_path / "retrieval.json", {
        "version": "business-retrieval-v1", "scope": "Invoice knowledge",
        "documents": [
            {"id": identity, "db_id": "billing", "domain": "accounts", "text": passage}
            for identity, passage in [
                ("invoice", "Invoices contain amounts and identifiers"),
                ("contact", "Contact accounting for corrections"),
            ]
        ],
        "queries": [
            {
                "id": identity, "query": question, "db_id": "billing", "domain": "accounts",
                "split": split, "tags": ["billing"], "answerable": answerable,
                "relevant": ["invoice"] if answerable else [],
                "relevant_reasons": {"invoice": "Explains invoice fields"} if answerable else {},
                "hard_negatives": ["contact"],
                "negative_reasons": {"contact": "Describes administrative process"},
                "annotation_source": "manual-business-facts",
                "no_answer_reason": "" if answerable else "No tax documents in the corpus",
                "bad_ranking": ["contact"],
            }
            for identity, split, question, answerable in [
                ("dev-1", "dev", "What is an invoice?", True),
                ("test-1", "holdout", "Are invoice amounts available?", True),
                ("test-2", "holdout", "How is sales tax calculated?", False),
            ]
        ],
    })
    return freeze(tmp_path)


def test_business_report_replays_and_keeps_missing_predictions_in_denominator(
    business_dataset, tmp_path,
):
    predictions, ranks = tmp_path / "predictions.json", tmp_path / "rankings.json"
    write_atomic(predictions, [{
        "id": "test-1", "sql": "SELECT sum(amount) AS total FROM invoices", "finish_reason": "stop",
    }])
    write_atomic(ranks, [{"id": "test-1", "retrieved": ["invoice"]}])
    options = [
        "--dataset-manifest", str(business_dataset),
        "--sql-predictions", str(predictions), "--rankings", str(ranks),
    ]
    first, second = tmp_path / "first.json", tmp_path / "second.json"
    assert main([*options, "--split", "holdout", "--output", str(first)]) == 0
    assert main([*options, "--split", "holdout", "--output", str(second)]) == 0
    assert first.read_bytes() == second.read_bytes()
    report = read_json(first)
    assert report["profile"] == BUSINESS_PROFILE
    assert report["self_check"]["manifest_sha256"] == hashlib.sha256(
        business_dataset.read_bytes(),
    ).hexdigest()
    assert report["self_check"]["input_sha256"] == read_json(business_dataset)["files"]
    assert report["sql"]["case_count"] == 2
    assert report["sql"]["robust_match_rate"] == 0.5
    assert report["sql"]["error_counts"] == {"MISSING_PREDICTION": 1}
    assert report["retrieval"]["query_count"] == 2
    assert report["retrieval"]["error_counts"] == {"MISSING_PREDICTION": 1}
    assert report["retrieval"]["no_answer_correct_empty_count"] == 0
    assert report["llm_calls"] == report["embedding_calls"] == 0
    quality = report["business_quality"]
    assert set(quality["by_split"]) == {"holdout"}
    holdout = quality["by_split"]["holdout"]
    accuracy = holdout["metrics"]["sql_answer_accuracy"]
    assert (accuracy["count"], accuracy["successes"], accuracy["score"]) == (2, 1, 0.5)
    assert accuracy["interval_95"][0] < 0.5 < accuracy["interval_95"][1]
    assert holdout["metrics"]["sql_execution_success_rate"]["score"] == 0.5
    assert holdout["metrics"]["recall_at_k"]["score"] == 1.0
    assert holdout["metrics"]["recall_at_k"]["count"] == 1
    assert holdout["metrics"]["no_answer_accuracy"]["score"] == 0.0
    assert holdout["error_counts"] == {
        "sql": {"MISSING_PREDICTION": 1}, "retrieval": {"MISSING_PREDICTION": 1},
    }
    assert holdout["slices"]["domain"]["accounts"]["metrics"] == holdout["metrics"]
    assert holdout["slices"]["database"]["billing"]["metrics"] == holdout["metrics"]
    tag = holdout["slices"]["tag"]
    assert "case:dev-1" not in tag
    assert tag["case:test-1"]["metrics"]["sql_answer_accuracy"]["score"] == 1.0
    assert tag["case:test-1"]["metrics"]["recall_at_k"]["interval_95"] is None

    assert main([*options, "--output", str(second)]) == 0
    combined = read_json(second)["business_quality"]
    assert combined["by_split"]["holdout"] == holdout
    assert combined["by_split"]["dev"]["metrics"]["no_answer_accuracy"]["count"] == 0
    recall = combined["overall"]["metrics"]["recall_at_k"]
    assert recall["count"] == 2 and recall["score"] == 0.5
    assert recall["interval_95"][0] < 0.5 < recall["interval_95"][1]


@pytest.mark.parametrize(
    "defect", [None, "score", "sample_count", "manifest", "k", "split", "ranks"],
)
def test_holdout_thresholds_validate_evidence_and_report_failures(
    business_dataset, tmp_path, defect,
):
    predictions, ranks, thresholds = (
        tmp_path / "predictions.json", tmp_path / "rankings.json", tmp_path / "thresholds.json",
    )
    write_atomic(predictions, [
        {"id": "test-1", "sql": "SELECT sum(amount) AS total FROM invoices",
         "finish_reason": "stop"},
        # Valid SQL execution with an incorrect answer.
        {"id": "test-2", "sql": "SELECT min(amount) AS total FROM invoices",
         "finish_reason": "stop"},
    ])
    write_atomic(ranks, [
        {"id": "test-1", "retrieved": ["invoice"]}, {"id": "test-2", "retrieved": []},
    ])
    rules = {
        "version": "business-quality-thresholds-v1",
        "manifest_sha256": "0" * 64 if defect == "manifest" else hashlib.sha256(
            business_dataset.read_bytes(),
        ).hexdigest(),
        "k": 2 if defect == "k" else 3,
        "metrics": {
            "sql_execution_success_rate": {"min_score": 1.0, "min_count": 2},
            "sql_answer_accuracy": {
                "min_score": 0.8 if defect == "score" else 0.5,
                "min_count": 3 if defect == "sample_count" else 2,
            },
            "recall_at_k": {"min_score": 1.0, "min_count": 1},
            "no_answer_accuracy": {"min_score": 1.0, "min_count": 1},
        },
    }
    write_atomic(thresholds, rules)
    output = tmp_path / "report.json"
    output.write_bytes(b"previous")
    args = [
        "--dataset-manifest", str(business_dataset), "--split",
        "dev" if defect == "split" else "holdout", "--thresholds", str(thresholds),
        "--sql-predictions", str(predictions), "--output", str(output),
    ]
    if defect != "ranks":
        args.extend(["--rankings", str(ranks)])
    if defect in {"manifest", "k", "split", "ranks"}:
        assert main(args) == 2
        assert output.read_bytes() == b"previous"
        return
    passed = defect is None
    assert main(args) == (0 if passed else 1)
    gate = read_json(output)["business_quality"]["thresholds"]
    assert gate["sha256"] == hashlib.sha256(thresholds.read_bytes()).hexdigest()
    assert gate["passed"] is passed
    assert gate["metrics"]["sql_answer_accuracy"]["passed"] is passed
    assert gate["metrics"]["sql_execution_success_rate"]["passed"] is True


@pytest.mark.parametrize("name", BUSINESS_FILES)
def test_hash_drift_keeps_previous_report(business_dataset, tmp_path, name):
    source = tmp_path / name
    source.write_bytes(source.read_bytes() + b" ")
    output = tmp_path / "report.json"
    output.write_bytes(b"previous")
    assert main(["--dataset-manifest", str(business_dataset), "--output", str(output)]) == 2
    assert output.read_bytes() == b"previous"


def test_business_snapshot_cannot_be_overwritten_by_report(business_dataset, tmp_path):
    snapshot = tmp_path / "snapshot.sqlite3"
    original = snapshot.read_bytes()
    assert main(["--dataset-manifest", str(business_dataset), "--output", str(snapshot)]) == 2
    assert snapshot.read_bytes() == original


def test_business_bundle_requires_separate_dev_and_holdout(business_dataset, tmp_path):
    sql = tmp_path / "sql.json"
    labels = json.loads(sql.read_bytes())
    labels["sql_cases"] = labels["sql_cases"][:1]
    write_atomic(sql, labels)
    freeze(tmp_path)
    output = tmp_path / "report.json"
    assert main(["--dataset-manifest", str(business_dataset), "--output", str(output)]) == 2
    assert not output.exists()
