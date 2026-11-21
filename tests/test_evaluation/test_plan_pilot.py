"""Pilot gates bind complete holdout captures and reviewed per-query cost evidence."""

from copy import deepcopy

import pytest

from app.evaluation.artifacts import read_json, write_atomic
from app.evaluation.plan_comparison import PAIR_CAPTURE_PROFILE
from app.evaluation.plan_pilot import BILLING_PROFILE, GATE_PROFILE
from app.evaluation.predictions import fingerprint
from scripts.compare_query_plans import main
from tests.test_api.test_plan_execution import plan_env as plan_env
from tests.test_evaluation.test_business_acceptance import business_dataset as business_dataset
from tests.test_evaluation.test_plan_comparison import compared_captures as compared_captures


@pytest.fixture
def pilot_files(compared_captures, tmp_path):
    dataset, baseline, reflection = compared_captures
    capture = {
        "profile": PAIR_CAPTURE_PROFILE, "baseline": baseline, "reflection": reflection,
        "acquisition": {"status": "completed", "planned_requests": 4, "attempted_requests": 4},
    }
    thresholds = {
        "profile": GATE_PROFILE, "manifest_sha256": dataset.hashes["manifest"],
        "configuration_sha256": baseline["configuration_sha256"],
        "limits": {
            "min_cases": 2, "min_accuracy_delta": 0, "max_regressed_cases": 1,
            "max_p95_delta_ms": 3.6e6, "max_llm_calls_delta": 1,
            "max_model_cost_delta_usd": "0.003",
        },
    }
    responses = [row["response"] for capture_arm in (baseline, reflection)
                 for row in capture_arm["records"]]
    charges = ("0.010", "0.010", "0.012", "0.011")
    billing = {
        "profile": BILLING_PROFILE, "paired_capture_sha256": fingerprint(capture),
        "currency": "USD", "evidence_sha256": "b" * 64,
        "entries": [
            {"query_id": response["query_id"], "cost_usd": amount}
            for response, amount in zip(responses, charges, strict=True)
        ],
    }
    names = ("capture", "thresholds", "billing", "out")
    files = {name: tmp_path / f"{name}.json" for name in names}
    for name, value in (("capture", capture), ("thresholds", thresholds), ("billing", billing)):
        write_atomic(files[name], value)
    return files, capture, thresholds, billing


def _arguments(files, manifest, *, include_billing=True):
    args = [
        "--dataset-manifest", str(manifest), "--capture", str(files["capture"]),
        "--thresholds", str(files["thresholds"]), "--output", str(files["out"]),
    ]
    return [*args, "--billing", str(files["billing"])] if include_billing else args


def test_pilot_gate_uses_exact_cost_delta_and_records_an_unmet_limit(pilot_files, business_dataset):
    files, _, thresholds, _ = pilot_files
    args = _arguments(files, business_dataset)
    assert main(args) == 0
    passed = read_json(files["out"])
    assert passed["pilot_gate"]["passed"] is True
    assert passed["billing"]["model_cost_delta_usd"] == "0.003"
    assert passed["billing"]["arms"]["reflection"]["total_usd"] == "0.023"
    assert passed["actual_cost_usd"] == "0.043"

    thresholds["limits"]["max_model_cost_delta_usd"] = "0.002999999999"
    write_atomic(files["thresholds"], thresholds)
    assert main(args) == 1
    report = read_json(files["out"])
    failed = [key for key, check in report["pilot_gate"]["checks"].items() if not check["passed"]]
    assert failed == ["max_model_cost_delta_usd"]


def test_incomplete_capture_and_missing_charges_cannot_pass(pilot_files, business_dataset):
    files, capture, _, billing = pilot_files
    billing["entries"].pop()
    write_atomic(files["billing"], billing)
    assert main(_arguments(files, business_dataset)) == 1
    report = read_json(files["out"])
    assert report["billing"]["arms"]["reflection"]["covered_requests"] == 1
    assert report["billing"]["arms"]["reflection"]["total_usd"] is None
    assert report["actual_cost_usd"] is None
    assert not report["pilot_gate"]["checks"]["billing_complete"]["passed"]

    capture["acquisition"].update(status="stopped", attempted_requests=3)
    capture["reflection"]["records"].pop()
    write_atomic(files["capture"], capture)
    assert main(_arguments(files, business_dataset, include_billing=False)) == 1
    report = read_json(files["out"])
    assert report["case_count"] == 2
    checks = report["pilot_gate"]["checks"]
    assert not any(checks[key]["passed"] for key in (
        "capture_complete", "billing_complete", "max_p95_delta_ms", "max_llm_calls_delta",
    ))


def test_billing_from_another_capture_and_stale_limits_preserve_existing_report(
    pilot_files, business_dataset,
):
    files, _, thresholds, billing = pilot_files
    args = _arguments(files, business_dataset)
    assert main(args) == 0
    previous = files["out"].read_bytes()
    no_match = deepcopy(billing)
    no_match["entries"][0]["query_id"] = "q_000000000000"
    write_atomic(files["billing"], no_match)
    assert main(args) == 2 and files["out"].read_bytes() == previous
    write_atomic(files["billing"], {**billing, "paired_capture_sha256": "0" * 64})
    assert main(args) == 2 and files["out"].read_bytes() == previous
    write_atomic(files["billing"], billing)
    write_atomic(files["thresholds"], {**thresholds, "configuration_sha256": "0" * 64})
    assert main(args) == 2 and files["out"].read_bytes() == previous
