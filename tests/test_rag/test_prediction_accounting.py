"""Offline prediction accounting must not turn incomplete SQL into a correct answer."""

import copy
import hashlib
import json
import os
import subprocess
import sys

import pytest

from scripts.evaluate_quality import ROOT, evaluate_sql

CASES = [{"id": "one", "gold_sql": "SELECT 1"}]


def test_length_prediction_cannot_score_as_correct():
    result = evaluate_sql(CASES, [{"id": "one", "sql": "SELECT 1", "finish_reason": "length"}])
    assert result["result_correct_rate"] == 0
    assert result["details"][0]["error_code"] == "INCOMPLETE_COMPLETION"


def test_missing_prediction_has_no_execution_latency():
    result = evaluate_sql(CASES, [])
    assert result["details"][0]["execution_latency_ms"] is None


def test_invalid_reported_cost_rejected():
    with pytest.raises(ValueError):
        evaluate_sql(CASES, [{"id": "one", "sql": "SELECT 1", "cost_usd": -1}])


@pytest.mark.parametrize("metadata", [
    {"finish_reason": "length"}, {"finish_reason": "content_filter"},
    {"finish_reason": "unknown-private-reason"}, {"finish_reason": "tool_calls"},
    {"finish_reason": "stop", "refusal": "private body"},
    {"finish_reason": "stop", "tool_calls": [{"arguments": "private body"}]},
    {"function_call": {"arguments": "private body"}}, {"status": "failed"},
])
def test_rejected_generation_never_attempts_prediction_sql(monkeypatch, metadata):
    from scripts import evaluate_quality as module

    original = module._execute_evaluation_query
    calls = []

    def traced(conn, sql, *args):
        calls.append(sql)
        return original(conn, sql, *args)

    monkeypatch.setattr(module, "_execute_evaluation_query", traced)
    result = evaluate_sql(CASES, [{"id": "one", "sql": "SELECT 1", **metadata}])
    assert calls == ["SELECT 1"]  # gold only; not even prediction policy evaluation
    assert result["execution_success_rate"] == result["result_correct_rate"] == 0
    detail = result["details"][0]
    assert detail["error_code"] == (
        "GENERATION_FAILED" if metadata.get("status") == "failed" else "INCOMPLETE_COMPLETION"
    )
    assert detail["execution_latency_ms"] is None
    assert "private" not in detail["error"]


def test_all_case_denominator_unknowns_failed_costs_and_nearest_rank():
    dataset = json.loads(
        (ROOT / "tests/fixtures/sql_prediction_accounting.json").read_text(encoding="utf-8")
    )
    cases, predictions = dataset["cases"], dataset["predictions"]
    original = copy.deepcopy(predictions)
    result = evaluate_sql(cases, predictions)
    assert predictions == original
    assert result["case_count"] == 6 and result["prediction_count"] == 5
    assert result["execution_success_count"] == 2 and result["result_correct_count"] == 1
    assert result["execution_success_rate"] == pytest.approx(2 / 6)
    assert result["result_correct_rate"] == pytest.approx(1 / 6)
    assert result["completion_counts"] == {
        "missing": 1, "legacy_unknown": 2, "generation_failed": 1, "rejected": 1, "stop": 1,
    }
    summary = result["reported_generation_latency_ms"]
    assert summary == {"sample_count": 2, "case_count": 6, "coverage": 2 / 6,
                       "complete": False, "mean": 50, "p50": 0, "p95": 100, "p99": 100,
                       "percentile_method": "nearest_rank"}
    assert result["reported_cost_usd"] == {
        "sample_count": 2, "case_count": 6, "coverage": 2 / 6, "complete": False,
        "total": 1, "mean": 0.5,
    }
    assert result["execution_attempt_latency_ms"]["sample_count"] == 2
    assert len(result["error_counts"]) == 5


def test_missing_measurements_unknown_and_valid_zero_known():
    unknown = evaluate_sql(CASES, [])
    assert unknown["reported_generation_latency_ms"]["p99"] is None
    assert unknown["reported_cost_usd"]["total"] is None
    assert unknown["execution_attempt_latency_ms"]["mean"] is None
    known = evaluate_sql(CASES, [{"id": "one", "sql": "SELECT 1", "finish_reason": "stop",
                                  "latency_ms": 0, "cost_usd": 0}])
    assert known["reported_cost_usd"]["total"] == 0
    assert known["reported_cost_usd"]["complete"] is True
    assert known["reported_generation_latency_ms"]["mean"] == 0


@pytest.mark.parametrize("key", ["latency_ms", "cost_usd"])
@pytest.mark.parametrize("value", [-1, float("nan"), float("inf"), float("-inf"),
                                   True, False, "3", [], {}, 10**400])
def test_bad_telemetry_fails_before_database_open(monkeypatch, key, value):
    from scripts import evaluate_quality as module

    def forbidden(*args, **kwargs):
        pytest.fail("Invalid telemetry must be rejected before opening SQLite")

    monkeypatch.setattr(module.sqlite3, "connect", forbidden)
    with pytest.raises(ValueError, match="finite non-negative"):
        evaluate_sql(CASES, [{"id": "one", "sql": "SELECT 1", key: value}])


def test_overflowing_cost_total_rejected_before_sqlite(monkeypatch):
    from scripts import evaluate_quality as module

    def forbidden(*args, **kwargs):
        pytest.fail("Cost overflow must be rejected before opening SQLite")

    monkeypatch.setattr(module.sqlite3, "connect", forbidden)
    with pytest.raises(ValueError, match="cost total"):
        evaluate_sql([*CASES, {"id": "two", "gold_sql": "SELECT 1"}], [
            {"id": "one", "cost_usd": 1e308}, {"id": "two", "cost_usd": 1e308},
        ])


@pytest.mark.parametrize("cases,predictions", [
    ([], []), (None, []), (CASES, None), (CASES, {}),
    ([{"id": [], "gold_sql": "SELECT 1"}], []),
    ([{"id": " ", "gold_sql": "SELECT 1"}], []),
    ([{"id": "one", "gold_sql": ""}], []),
    ([{"id": "one", "gold_sql": "SELECT 1", "ordered": "false"}], []),
    (CASES * 2, []), (CASES, [{"id": "unknown"}]), (CASES, [{"id": []}]),
    (CASES, [{"id": "one"}] * 2), (CASES, [{"id": "one", "status": "cancelled"}]),
    (CASES, [{"id": "one", "status": None}]),
])
def test_invalid_input_shape_and_ids_rejected(cases, predictions):
    with pytest.raises(ValueError):
        evaluate_sql(cases, predictions)


@pytest.mark.parametrize("limits", [
    {"timeout_seconds": True}, {"timeout_seconds": 0}, {"timeout_seconds": float("nan")},
    {"max_rows": True}, {"max_rows": 1.5}, {"max_rows": 0},
    {"timeout_seconds": 10**400}, {"max_rows": 10**400},
])
def test_invalid_evaluation_limits_rejected(monkeypatch, limits):
    from scripts import evaluate_quality as module

    def forbidden(*args, **kwargs):
        pytest.fail("Unsupported limits must fail before opening SQLite")

    monkeypatch.setattr(module.sqlite3, "connect", forbidden)
    with pytest.raises(ValueError):
        evaluate_sql(CASES, [], **limits)


def test_provenance_hashes_limits_and_input_order_reproducible():
    from scripts.evaluate_quality import ROOT

    predictions = [{"id": "one", "sql": "SELECT 1", "finish_reason": "stop"}]
    first = evaluate_sql(CASES, predictions, timeout_seconds=1, max_rows=7)
    reordered_keys = [{"finish_reason": "stop", "sql": "SELECT 1", "id": "one"}]
    second = evaluate_sql(CASES, reordered_keys, timeout_seconds=1, max_rows=7)
    assert first["predictions_sha256"] == second["predictions_sha256"]
    assert first["cases_sha256"] == second["cases_sha256"]
    assert first["sqlite_fixture_sha256"] == hashlib.sha256(
        (ROOT / "tests/fixtures/test_schema.sql").read_bytes()
    ).hexdigest()
    assert first["limits"] == {"timeout_seconds": 1, "max_rows": 7, "dialect": "sqlite"}
    assert first["report_version"] == "sql-evaluation-v2"


def test_fixture_initialization_failure_closes_connection(monkeypatch):
    import sqlite3

    from scripts import evaluate_quality as module

    class FailingConnection(sqlite3.Connection):
        closed = False

        def executescript(self, *args):
            raise sqlite3.OperationalError("synthetic fixture failure")

        def close(self):
            self.closed = True
            super().close()

    connection = sqlite3.connect(":memory:", factory=FailingConnection)
    monkeypatch.setattr(module.sqlite3, "connect", lambda *args: connection)
    with pytest.raises(sqlite3.OperationalError, match="fixture failure"):
        evaluate_sql(CASES, [])
    assert connection.closed


def test_fresh_offline_import_never_loads_litellm_or_router():
    environment = dict(os.environ)
    environment.pop("LITELLM_LOCAL_MODEL_COST_MAP", None)
    code = '''
import importlib.abc
import sys
class ForbidSDK(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "litellm" or fullname.startswith("litellm."):
            raise AssertionError("Offline import attempted to initialize external SDK")
sys.meta_path.insert(0, ForbidSDK())
from scripts.evaluate_quality import evaluate_sql
assert "app.llm.router" not in sys.modules
assert "litellm" not in sys.modules
assert evaluate_sql([{"id":"one", "gold_sql":"SELECT 1"}],
                    [{"id":"one", "sql":"SELECT 1", "finish_reason":"length"}])[
                        "execution_success_count"] == 0
print("PASS: offline import and completion validation without SDK")
'''
    result = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=environment,
                            text=True, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert "PASS: offline import" in result.stdout


def test_llm_public_exports_remain_identical():
    import app.llm as package
    from app.llm import router

    for name in package.__all__:
        assert getattr(package, name) is getattr(router, name)
    with pytest.raises(AttributeError):
        _ = package.nonexistent_export


def test_cli_round_trip_and_invalid_input_preserves_previous_report(tmp_path, monkeypatch):
    import sys

    from scripts import evaluate_quality as module

    output, predictions = tmp_path / "result.json", tmp_path / "predictions.json"
    monkeypatch.setattr(sys, "argv", ["evaluate_quality", "--output", str(output)])
    module.main()
    lexical_only = json.loads(output.read_text(encoding="utf-8"))
    assert lexical_only["sql_model_metrics"] is None
    assert lexical_only["retrieval"]["recall_at_k"] == 1
    predictions.write_text(json.dumps([{"id": "sql1", "sql": "SELECT id FROM orders ORDER BY id",
                                        "finish_reason": "length", "cost_usd": 0.1}]))
    monkeypatch.setattr(sys, "argv", ["evaluate_quality", "--output", str(output),
                                      "--predictions", str(predictions)])
    module.main()
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["sql_model_metrics"]["error_counts"] == {
        "INCOMPLETE_COMPLETION": 1, "MISSING_PREDICTION": 3,
    }
    assert report["sql_model_metrics"]["reported_cost_usd"]["total"] == 0.1
    before = output.read_bytes()
    predictions.write_text('[{"id":"sql1","cost_usd":NaN}]')
    with pytest.raises(SystemExit) as invalid:
        module.main()
    assert invalid.value.code == 2
    assert output.read_bytes() == before
