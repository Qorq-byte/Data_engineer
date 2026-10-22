"""Evaluation distinguishes runnable SQL from correct answers and enforces scope."""

import json

import pytest

from scripts.evaluate_quality import ROOT, evaluate, evaluate_sql, ranking_metrics


def test_ranking_penalizes_delayed_and_irrelevant_results():
    metrics = ranking_metrics(["noise", "right"], {"right"}, 3)
    assert metrics["recall_at_k"] == 1
    assert metrics["precision_at_k"] == pytest.approx(1 / 3)
    assert metrics["mrr_at_k"] == 0.5
    assert 0 < metrics["ndcg_at_k"] < 1


def test_sql_success_is_distinct_from_answer_correctness():
    cases = [
        {"id": "a", "gold_sql": "SELECT COUNT(*) FROM orders"},
        {"id": "b", "gold_sql": "SELECT id FROM orders ORDER BY id", "ordered": True},
        {"id": "c", "gold_sql": "SELECT id FROM orders"},
    ]
    predictions = [
        {"id": "a", "sql": "SELECT COUNT(*) FROM users"},
        {"id": "b", "sql": "SELECT id FROM orders ORDER BY id"},
        {"id": "c", "sql": "DELETE FROM orders"},
    ]
    result = evaluate_sql(cases, predictions)
    assert result["execution_success_rate"] == pytest.approx(2 / 3)
    assert result["result_correct_rate"] == pytest.approx(1 / 3)
    assert result["details"][2]["error"]


def test_no_model_predictions_means_no_claimed_sql_accuracy():
    dataset = json.loads((ROOT / "tests/fixtures/retrieval_eval.json").read_text(encoding="utf-8"))
    result = evaluate(dataset, 3, None)
    assert result["sql_model_metrics"] is None
    # The repeated sales term in the HR distractor cannot enter the commerce query.
    assert next(row for row in result["details"] if row["id"] == "q4")["retrieved"] == ["orders"]
