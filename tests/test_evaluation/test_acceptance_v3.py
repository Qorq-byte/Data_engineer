"""Thirty independent contracts/qrels, their counterexamples, and frozen input boundaries."""

import copy
import json
import os
import shutil
import subprocess
import sys

import pytest

from app.evaluation import acceptance_data as data
from app.evaluation.acceptance_retrieval import (
    bm25_rankings,
    evaluate_retrieval,
    load_retrieval,
    retrieval_self_check,
)
from app.evaluation.acceptance_sql import evaluate_sql, load_sql, sql_self_check
from app.evaluation.acceptance_suite import self_check
from app.evaluation.artifacts import read_json
from scripts.evaluate_acceptance import main


def oracle_sql():
    # Evaluator positive control only: these are never reported as model predictions.
    return [{"id": c["id"], "sql": c["gold_sql"], "finish_reason": "stop"}
            for c in load_sql()["sql_cases"]]


def oracle_ranks():
    return [{"id": q["id"], "retrieved": q["relevant"]} for q in load_retrieval()["queries"]]


def test_frozen_manifest_gold_and_bad_output_self_checks():
    report = self_check()
    assert report["sql"]["gold_check_count"] == 90
    assert report["sql"]["counterexamples_killed"] == 30
    assert report["retrieval"]["qrels_checked"] == 30
    assert report["retrieval"]["bad_rankings_rejected"] == 30
    assert report["document_count"] == 23
    for kind in ("sql_statistics", "retrieval_statistics"):
        assert report[kind]["split_counts"] == {"dev": 20, "holdout": 10}
        assert report[kind]["by_split"]["holdout"]
    assert report["llm_calls"] == report["embedding_calls"] == 0
    assert report["actual_cost_usd"] is None


@pytest.mark.parametrize("index", range(30))
def test_each_sql_counterexample_is_executable_and_killed(index):
    suite = load_sql()
    predictions = oracle_sql()
    case = suite["sql_cases"][index]
    predictions[index]["sql"] = case["counterexamples"][0]["sql"]
    report = evaluate_sql(suite, predictions)
    assert report["execution_success_count"] == 90
    assert report["robust_match_count"] == 29
    witnesses = [row for row in report["details"] if row["id"] == case["id"]]
    assert any(not row["contract_match"] for row in witnesses)


@pytest.mark.parametrize("defect", ["unknown", "cross_domain", "cross_db", "duplicate",
                                   "overlap", "reason", "answerability"])
def test_retrieval_labels_must_exist_and_match_scope(defect):
    suite = copy.deepcopy(load_retrieval())
    q = suite["queries"][0]
    if defect in ("unknown", "cross_domain", "cross_db", "duplicate"):
        q["relevant"] = {"unknown": ["missing"], "cross_domain": ["a.users"],
                         "cross_db": ["b.orders"], "duplicate": ["a.orders", "a.orders"]}[defect]
        q["relevant_reasons"] = dict.fromkeys(q["relevant"], "independent test label")
    elif defect == "overlap":
        q["hard_negatives"].append("a.orders")
    elif defect == "reason":
        q["relevant_reasons"] = {}
    else:
        q["answerable"] = False
    with pytest.raises(ValueError):
        data.validate_retrieval(suite)


@pytest.mark.parametrize("defect", ["width", "nan", "boolean", "huge", "permission", "split",
                                   "duplicate_id", "order", "annotation", "no_mutant"])
def test_invalid_sql_labels_are_rejected(defect):
    suite = copy.deepcopy(load_sql())
    c = suite["sql_cases"][0]
    if defect in ("width", "nan", "boolean", "huge"):
        c["expected_rows"]["base"] = [[1, 2]] if defect == "width" else [
            [{"nan": float("nan"), "boolean": True, "huge": 10**500}[defect]]]
    elif defect == "permission":
        c["permission"] = "allow_write"
    elif defect == "split":
        c["split"] = "holdout"
    elif defect == "duplicate_id":
        c["id"] = suite["sql_cases"][1]["id"]
    elif defect == "order":
        c["ordered"] = False
    elif defect == "annotation":
        c["annotation"]["source"] = "model-output"
    else:
        c["counterexamples"] = []
    with pytest.raises(ValueError):
        data.validate_sql(suite)


def test_self_check_rejects_wrong_manual_rows_and_surviving_mutant():
    suite = copy.deepcopy(load_sql())
    suite["sql_cases"][0]["expected_rows"]["edge"] = []
    with pytest.raises(ValueError, match="labels disagree"):
        sql_self_check(suite)
    suite = copy.deepcopy(load_sql())
    suite["sql_cases"][0]["counterexamples"][0]["sql"] = suite["sql_cases"][0]["gold_sql"]
    with pytest.raises(ValueError, match="survived"):
        sql_self_check(suite)


def test_retrieval_self_check_rejects_non_discriminating_bad_ranking():
    suite = copy.deepcopy(load_retrieval())
    # A structurally valid in-scope hard negative can never be the positive oracle.
    suite["queries"][0]["bad_ranking"] = ["a.orders"]
    with pytest.raises(ValueError):
        retrieval_self_check(suite)


@pytest.mark.parametrize("split,count", [("all", 30), ("dev", 20), ("holdout", 10)])
def test_sql_oracle_and_missing_predictions_keep_full_denominator(split, count):
    correct = evaluate_sql(load_sql(), oracle_sql(), split)
    assert correct["case_count"] == correct["robust_match_count"] == count
    missing = evaluate_sql(load_sql(), [], split)
    assert missing["check_count"] == count * 3
    assert missing["prediction_count"] == missing["robust_match_count"] == 0
    assert missing["error_counts"] == {"MISSING_PREDICTION": count * 3}
    assert missing["actual_cost_usd"] is None


@pytest.mark.parametrize("reason", [None, "length", "tool_calls", "unknown"])
def test_unconfirmed_sql_never_executes(reason):
    rows = oracle_sql()
    rows[0]["finish_reason"] = reason
    rows[0]["sql"] = "DELETE FROM orders"
    report = evaluate_sql(load_sql(), rows)
    assert report["error_counts"] == {
        "UNCONFIRMED_COMPLETION" if reason is None else "INCOMPLETE_COMPLETION": 3}
    assert report["execution_success_count"] == 87


def test_order_required_and_bag_multiplicity_are_separate():
    suite = copy.deepcopy(load_sql())
    rows = oracle_sql()
    rows[0]["sql"] = "SELECT id FROM orders"  # PK order coincidentally matches labels.
    assert evaluate_sql(suite, rows)["robust_match_count"] == 29
    rows = oracle_sql()
    rows[20]["sql"] += " ORDER BY id DESC"  # This case explicitly ignores order.
    assert evaluate_sql(suite, rows)["robust_match_count"] == 30
    suite["sql_cases"][29]["ordered"] = False
    suite["sql_cases"][29]["requires_order_by"] = False
    rows[29]["sql"] = suite["sql_cases"][29]["counterexamples"][0]["sql"]
    assert evaluate_sql(suite, rows)["robust_match_count"] == 29


@pytest.mark.parametrize("ranking,code", [(["missing"], "UNKNOWN_DOCUMENT"),
    (["a.orders", "a.orders"], "DUPLICATE_DOCUMENT"), (["b.orders"], "OUT_OF_SCOPE"),
    (["a.users"], "OUT_OF_SCOPE")])
def test_invalid_rankings_score_zero_without_silent_filtering(ranking, code):
    rows = oracle_ranks()
    rows[0]["retrieved"] = ranking
    report = evaluate_retrieval(load_retrieval(), rows)
    result = report["details"][0]
    assert code in result["error_codes"] and result["retrieved"] == ranking
    assert result["recall_at_k"] == result["ndcg_at_k"] == 0


def test_missing_no_answer_prediction_is_failure_not_correct_abstention():
    suite = load_retrieval()
    oracle = evaluate_retrieval(suite, oracle_ranks())
    assert oracle["answerable_count"] == 24 and oracle["no_answer_count"] == 6
    assert oracle["answerable_macro"]["recall_at_k"] == 1
    assert oracle["no_answer_correct_empty_rate"] == 1
    assert oracle["all_queries_macro"]["recall_at_k"] == 0.8
    missing = evaluate_retrieval(suite, [])
    assert missing["no_answer_correct_empty_rate"] == 0
    assert missing["error_counts"] == {"MISSING_PREDICTION": 30}


@pytest.mark.parametrize("split,count", [("dev", 20), ("holdout", 10)])
def test_bm25_applies_both_filters_before_ranking_and_respects_split(split, count):
    suite = load_retrieval()
    rows = bm25_rankings(suite, 3, split)
    assert len(rows) == count
    report = evaluate_retrieval(suite, rows, 3, split)
    assert report["query_count"] == count and report["invalid_ranking_count"] == 0
    assert report["prediction_count"] == count
    queries = {q["id"]: q for q in suite["queries"]}
    docs = {d["id"]: d for d in suite["documents"]}
    for row in rows:
        assert queries[row["id"]]["split"] == split
        for identity in row["retrieved"]:
            assert all(docs[identity][key] == queries[row["id"]][key]
                       for key in ("db_id", "domain"))


@pytest.mark.parametrize("kind", ["sql", "retrieval"])
@pytest.mark.parametrize("defect", ["unknown", "duplicate"])
def test_saved_prediction_id_ambiguity_is_rejected(kind, defect):
    rows = oracle_sql() if kind == "sql" else oracle_ranks()
    rows[0]["id"] = "unknown" if defect == "unknown" else rows[1]["id"]
    evaluator, suite = ((evaluate_sql, load_sql()) if kind == "sql" else
                        (evaluate_retrieval, load_retrieval()))
    with pytest.raises(ValueError):
        evaluator(suite, rows)


@pytest.mark.parametrize("source", ["schema.sql", "sql.json", "retrieval.json"])
def test_frozen_input_drift_is_rejected(source, tmp_path, monkeypatch):
    directory = tmp_path / "fixture"
    shutil.copytree(data.DIRECTORY, directory)
    monkeypatch.setattr(data, "DIRECTORY", directory)
    target = directory / source
    target.write_bytes(target.read_bytes() + b"\n")
    with pytest.raises(ValueError, match="hash mismatch"):
        data.verify_manifest()


@pytest.mark.parametrize("source", data.source_paths())
def test_cli_refuses_overwriting_any_frozen_input(source):
    before = source.read_bytes()
    assert main(["--output", str(source)]) == 2
    assert source.read_bytes() == before


def test_cli_refuses_hardlink_input_alias(tmp_path):
    source = data.DIRECTORY / "sql.json"
    alias = tmp_path / "alias.json"
    os.link(source, alias)
    assert main(["--output", str(alias)]) == 2
    assert alias.read_bytes() == source.read_bytes()


@pytest.mark.parametrize("kind", ["--sql-predictions", "--rankings"])
@pytest.mark.parametrize("raw", ["{}", "[NaN]", '[{"id":"x","id":"y"}]'])
def test_malformed_saved_outputs_preserve_old_report(kind, raw, tmp_path, capsys):
    source, output = tmp_path / "private.json", tmp_path / "report.json"
    source.write_text(raw, encoding="utf-8")
    output.write_bytes(b"previous report")
    assert main([kind, str(source), "--output", str(output)]) == 2
    assert output.read_bytes() == b"previous report"
    assert "private" not in capsys.readouterr().err


def test_cli_baseline_does_not_disguise_gold_as_model_predictions(tmp_path):
    output = tmp_path / "report.json"
    assert main(["--output", str(output)]) == 0
    report = read_json(output)
    assert report["sql"]["prediction_count"] == 0
    assert report["sql"]["error_counts"] == {"MISSING_PREDICTION": 90}
    assert report["retrieval"]["query_count"] == 30
    assert report["llm_calls"] == report["embedding_calls"] == 0
    assert report["actual_cost_usd"] is None


def test_fresh_cli_never_imports_sdk_or_reads_model_credentials(tmp_path):
    output = tmp_path / "report.json"
    code = """
import sys
from scripts.evaluate_acceptance import main
assert main(['--output',sys.argv[1],'--split','dev'])==0
assert 'litellm' not in sys.modules and 'openai' not in sys.modules
"""
    result = subprocess.run([sys.executable, "-c", code, str(output)],
        env=dict(os.environ, LLM_MOCK_MODE="0", DEEPSEEK_API_KEY="synthetic-do-not-call"),
        capture_output=True, check=True, timeout=30)
    assert b"synthetic-do-not-call" not in result.stdout + result.stderr
    assert json.loads(output.read_text(encoding="utf-8"))["sql"]["case_count"] == 20


def test_stop_with_refusal_still_rejects_sql_without_executing():
    rows = oracle_sql()
    rows[0]["refusal"] = "synthetic refusal"
    rows[0]["sql"] = "DELETE FROM orders"
    result = evaluate_sql(load_sql(), rows)
    assert result["error_counts"] == {"INCOMPLETE_COMPLETION": 3}
    assert result["execution_success_count"] == 87

@pytest.mark.parametrize("kind", ["--sql-predictions", "--rankings"])
def test_cli_refuses_overwriting_saved_prediction_input(kind, tmp_path):
    source = tmp_path / "predictions.json"
    source.write_bytes(b"[]")
    assert main([kind, str(source), "--output", str(source)]) == 2
    assert source.read_bytes() == b"[]"


def test_atomic_publish_failure_preserves_previous_report(tmp_path, monkeypatch):
    from app.evaluation import artifacts

    output = tmp_path / "report.json"
    output.write_bytes(b"previous report")

    def denied(*args):
        raise PermissionError("private location")

    monkeypatch.setattr(artifacts.os, "replace", denied)
    assert main(["--output", str(output)]) == 2
    assert output.read_bytes() == b"previous report"
    assert not list(tmp_path.glob(".offline-report-*.tmp"))


@pytest.mark.parametrize("k", [0, -1, True, 1001])
def test_invalid_retrieval_k_is_rejected(k):
    with pytest.raises(ValueError):
        evaluate_retrieval(load_retrieval(), [], k)
