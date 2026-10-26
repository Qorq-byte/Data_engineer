"""Versioned labels must distinguish plausible wrong queries on real SQLite data."""

import copy

import pytest

from app.evaluation.contract_suite import evaluate_suite, load_suite, validate_suite


def predictions(suite):
    return [{"id": c["id"], "sql": c["gold_sql"], "status": "completed", "finish_reason": "stop"}
            for c in suite["sql_cases"]]


def test_gold_matches_manual_labels_in_all_three_databases():
    suite = load_suite()
    report = evaluate_suite(suite, predictions(suite))
    assert report["case_count"] == report["robust_match_count"] == 4
    assert report["check_count"] == report["contract_match_count"] == 12
    assert report["actual_cost_usd"] is None


@pytest.mark.parametrize("sql,index", [
    ("SELECT u.id AS user_id, COUNT(o.id) AS order_count FROM users u "
     "JOIN orders o ON o.user_id=u.id GROUP BY u.id ORDER BY u.id", 2),
    ("SELECT u.id AS user_id, COUNT(*) AS order_count FROM users u "
     "LEFT JOIN orders o ON o.user_id=u.id GROUP BY u.id ORDER BY u.id", 2),
    ("SELECT u.id AS user_id, COUNT(o.id) AS order_count FROM users u "
     "LEFT JOIN orders o ON o.user_id=u.id GROUP BY u.name ORDER BY u.id", 2),
    ("SELECT COALESCE(SUM(DISTINCT amount),0) AS total_amount FROM orders "
     "WHERE status='completed'", 1),
    ("SELECT COALESCE(SUM(amount-refund_amount),0) AS total_amount FROM orders "
     "WHERE status='completed'", 1),
    ("SELECT COALESCE(SUM(amount),0) AS total_amount FROM orders", 1),
    ("SELECT SUM(amount) AS total_amount FROM orders WHERE status='completed'", 1),
    ("SELECT id FROM products WHERE 0 ORDER BY id", 3),
    ("SELECT id FROM products WHERE category <> 'unknown' ORDER BY id", 3),
    ("SELECT id FROM products WHERE category LIKE '%unknown%' ORDER BY id", 3),
    ("SELECT id FROM products WHERE category='unknown' ORDER BY id DESC", 3),
    ("SELECT id,name FROM products WHERE category='unknown' ORDER BY id", 3),
    ("SELECT id AS wrong_name FROM orders ORDER BY id", 0),
    ("SELECT id FROM orders ORDER BY id DESC", 0),
    ("SELECT id FROM orders", 0),
    ("SELECT id FROM (SELECT id FROM orders ORDER BY id)", 0),
    ("SELECT u.id AS user_id, COUNT(o.id) AS order_count FROM users u "
     "LEFT JOIN orders o ON o.user_id=u.id GROUP BY u.id ORDER BY u.name", 2),
    ("SELECT u.id AS user_id, COUNT(o.id) AS order_count FROM users u "
     "LEFT JOIN orders o ON o.user_id=u.id GROUP BY u.id ORDER BY order_count DESC, user_id", 2),
])
def test_counterexamples_reject_plausible_mutants(sql, index):
    suite = load_suite()
    rows = predictions(suite)
    rows[index]["sql"] = sql
    report = evaluate_suite(suite, rows)
    assert report["robust_match_count"] == 3
    assert report["check_count"] == 12


@pytest.mark.parametrize("change", ["label", "gold", "columns", "fixtures"])
def test_bad_annotations_are_rejected_before_predictions_execute(change):
    suite = copy.deepcopy(load_suite())
    if change == "label":
        suite["sql_cases"][0]["expected_rows"]["base"] = [[999]]
    elif change == "gold":
        suite["sql_cases"][0]["gold_sql"] = "DELETE FROM orders"
    elif change == "columns":
        suite["sql_cases"][0]["output_columns"] = ["wrong_name"]
    else:
        suite["fixtures"] = ["../../outside"]
    with pytest.raises(ValueError):
        validate_suite(suite)


@pytest.mark.parametrize("mode", ["missing", "failed", "length", "unknown", "write"])
def test_invalid_predictions_keep_all_cases_and_checks(mode):
    suite = load_suite()
    rows = predictions(suite)
    if mode == "missing":
        rows.pop()
    elif mode == "failed":
        rows[-1]["status"] = "failed"
    elif mode == "write":
        rows[-1]["sql"] = "DELETE FROM products"
    else:
        rows[-1]["finish_reason"] = "length" if mode == "length" else None
    report = evaluate_suite(suite, rows)
    assert report["robust_match_count"] == 3
    assert report["case_count"] == 4 and report["check_count"] == 12


def test_explain_rejection_does_not_log_prediction_sql(caplog):
    suite = load_suite()
    rows = predictions(suite)
    rows[0]["sql"] = (
        "EXPLAIN QUERY PLAN SELECT id AS PRIVATE_SQL_MARKER FROM orders ORDER BY id"
    )
    with caplog.at_level("WARNING", logger="sqlglot"):
        report = evaluate_suite(suite, rows)
    assert report["robust_match_count"] == 3
    assert "PRIVATE_SQL_MARKER" not in caplog.text


def test_huge_integer_telemetry_is_a_validation_error():
    suite = load_suite()
    rows = predictions(suite)
    rows[0]["cost_usd"] = 10 ** 1000
    with pytest.raises(ValueError, match="telemetry"):
        evaluate_suite(suite, rows)
