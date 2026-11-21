"""Compare real workflow responses captured over the API on a frozen SQLite split."""

import time
from copy import deepcopy

import pytest

from app.db.connections import ConnectionFactory, DatabaseType
from app.evaluation.acceptance_data import load_business_dataset
from app.evaluation.artifacts import read_json, write_atomic
from app.evaluation.plan_comparison import CAPTURE_PROFILE, compare_query_plans
from app.evaluation.predictions import fingerprint
from scripts.compare_query_plans import main
from tests.test_api.test_plan_execution import ScriptedRouter
from tests.test_api.test_plan_execution import plan_env as plan_env
from tests.test_evaluation.test_business_acceptance import business_dataset as business_dataset


class ConfirmedRouter(ScriptedRouter):
    async def complete(self, messages, **kwargs):
        result = await super().complete(messages, **kwargs)
        result["choices"][0]["finish_reason"] = "stop"
        return result


@pytest.fixture
async def compared_captures(business_dataset, plan_env, tmp_path, monkeypatch):
    dataset = load_business_dataset(business_dataset)
    await ConnectionFactory.create(
        DatabaseType.SQLITE, {"path": str(business_dataset.parent / "snapshot.sqlite3")}, "billing",
    )
    monkeypatch.setenv("DE_TRACE_DIR", str(tmp_path / "comparison-traces"))
    cases = [case for case in dataset.sql["sql_cases"] if case["split"] == "holdout"]

    def capture(plan_id, replies):
        observations = []
        for case, scripted in zip(cases, replies, strict=True):
            client = plan_env(ConfirmedRouter(scripted))
            started = time.perf_counter()
            result = client.post("/api/v1/query", json={
                "nl_text": case["question"], "database": case["db_id"], "domain": case["domain"],
                "execute": True, "auto_plan": False, "plan_id": plan_id, "num_candidates": 1,
            })
            elapsed = (time.perf_counter() - started) * 1000
            assert result.status_code == 200, result.text
            response = result.json()
            assert response["status"] == "completed" and response["execution"], response
            observations.append({"id": case["id"], "latency_ms": elapsed, "response": response})
        return {
            "profile": CAPTURE_PROFILE, "manifest_sha256": dataset.hashes["manifest"],
            "configuration_sha256": fingerprint({"transport": "scripted", "target": "billing"}),
            "split": "holdout", "plan_id": plan_id, "records": observations,
        }

    bad = "SELECT min(amount) AS total FROM invoices"
    try:
        baseline = capture("request_query", [[bad], ["SELECT max(amount) AS total FROM invoices"]])
        trial = capture("reflection", [
            ["SELECT missing_column FROM invoices", "SELECT sum(amount) AS total FROM invoices"],
            [bad],
        ])
        yield dataset, baseline, trial
    finally:
        await ConnectionFactory.aclose("billing")


def test_plan_comparison_binds_response_and_compares_improvement_and_regression(
    business_dataset, compared_captures, tmp_path,
):
    _, before, after = compared_captures
    paths = [tmp_path / name for name in ("baseline.json", "trial.json", "comparison.json")]
    write_atomic(paths[0], before)
    write_atomic(paths[1], after)
    args = [
        "--dataset-manifest", str(business_dataset), "--baseline", str(paths[0]),
        "--reflection", str(paths[1]), "--output", str(paths[2]),
    ]
    assert main(args) == 0
    output = paths[2].read_bytes()
    assert main(args) == 0 and paths[2].read_bytes() == output
    report = read_json(paths[2])
    assert report["case_count"] == 2
    assert report["outcomes"] == {
        "both_correct": 0, "improved": 1, "regressed": 1, "both_incorrect": 0,
    }
    assert report["accuracy"] == {"request_query": 0.5, "reflection": 0.5}
    assert report["accuracy_delta"] == 0 and report["paired_latency"]["count"] == 2
    assert report["paired_llm_calls"] == {
        "count": 2, "request_query_total": 2, "reflection_total": 3, "delta": 1,
    }
    assert report["actual_cost_usd"] is None and b"SELECT" not in output

    for key, wrong in (("configuration_sha256", "a" * 64), ("split", "dev"),
                       ("manifest_sha256", "b" * 64)):
        write_atomic(paths[1], {**after, key: wrong})
        assert main(args) == 2 and paths[2].read_bytes() == output


def test_same_sql_with_wrong_live_result_or_missing_capture_never_scores_as_correct(
    compared_captures,
):
    dataset, before, captured = compared_captures
    after = deepcopy(captured)
    after["records"].pop()
    response = after["records"][0]["response"]
    response["execution"]["rows"] = [[999]]
    response["candidates"][0]["execution"]["rows"] = [[999]]
    report = compare_query_plans(dataset, before, after)
    assert report["case_count"] == 2
    assert report["accuracy"]["reflection"] == 0
    assert report["details"][0]["reflection"]["error"] == "CAPTURED_RESULT_MISMATCH"
    assert report["details"][1]["reflection"]["error"] == "MISSING_RESPONSE"
    assert report["paired_latency"]["count"] == report["paired_llm_calls"]["count"] == 1

    response["candidates"][0]["db_id"] = "foreign-source"
    with pytest.raises(ValueError, match="frozen SQLite"):
        compare_query_plans(dataset, before, after)
