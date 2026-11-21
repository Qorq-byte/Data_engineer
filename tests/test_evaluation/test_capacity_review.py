"""Verify offline deployment evidence through the capacity review CLI."""

import hashlib

from app.evaluation.artifacts import read_json, write_atomic
from app.evaluation.capacity_acceptance import load_capacity_plan
from app.evaluation.capacity_review import EVIDENCE_PROFILE, LIMITS_PROFILE
from scripts.review_capacity_acceptance import main
from tests.test_api.test_capacity_acceptance import _plan


def test_capacity_review_binds_evidence_and_scores_complete_phases(tmp_path):
    manifest, http, limits, evidence, output = (
        tmp_path / f"{name}.json" for name in ("manifest", "http", "limits", "evidence", "review")
    )
    workload = _plan()
    workload["phases"].pop()
    write_atomic(manifest, workload)
    plan = load_capacity_plan(manifest)
    http_report = {
        "profile": plan.profile, "manifest_sha256": plan.sha256, "passed": True,
        "phases": [{"id": p.id, "requests": p.requests, "passed": True} for p in plan.phases],
    }
    write_atomic(http, http_report)
    bounds = {
        "profile": LIMITS_PROFILE, "manifest_sha256": plan.sha256,
        "max_cpu_percent": 80, "max_rss_mib": 256, "max_db_active_connections": 8,
        "max_total_cost_usd": "0.003",
    }
    write_atomic(limits, bounds)
    ledger = {
        "profile": EVIDENCE_PROFILE,
        "http_report_sha256": hashlib.sha256(http.read_bytes()).hexdigest(),
        "source_sha256": "a" * 64,
        "phases": [
            {"id": name, "peak_cpu_percent": 80, "peak_rss_mib": 256,
             "peak_db_active_connections": 8, "actual_cost_usd": amount}
            for name, amount in (("steady", "0.001"), ("peak", "0.002"))
        ],
    }
    write_atomic(evidence, ledger)
    args = ["--manifest", str(manifest), "--http-report", str(http), "--limits", str(limits),
            "--output", str(output)]
    with_evidence = [*args, "--evidence", str(evidence)]
    assert main(with_evidence) == 0
    assert read_json(output)["cost"]["total_usd"] == "0.003"
    assert main(args) == 1 and read_json(output)["cost"]["covered_phases"] == 0

    write_atomic(limits, {**bounds, "max_total_cost_usd": "0.002999999999"})
    assert main(with_evidence) == 1
    result = read_json(output)
    assert result["http_passed"] and result["resources_passed"] and not result["cost"]["passed"]
    write_atomic(limits, bounds)

    measured = ledger["phases"][1]
    http_report["passed"] = http_report["phases"][1]["passed"] = False
    write_atomic(http, http_report)
    ledger["http_report_sha256"] = hashlib.sha256(http.read_bytes()).hexdigest()
    write_atomic(evidence, ledger)
    assert main(with_evidence) == 1
    result = read_json(output)
    assert not result["http_passed"] and result["resources_passed"] and result["cost"]["passed"]

    measured.update(peak_rss_mib=257, peak_db_active_connections=None, actual_cost_usd=None)
    write_atomic(evidence, ledger)
    assert main(with_evidence) == 1
    result = read_json(output)
    assert result["cost"]["total_usd"] is None
    assert result["cost"]["known_subtotal_usd"] == "0.001"
    assert result["phases"][1]["resources"]["rss_mib"] == {
        "observed": 257, "limit": 256, "passed": False,
    }
    saved = output.read_bytes()
    assert main([*with_evidence, "--output", str(evidence)]) == 2
    assert read_json(evidence) == ledger
    http.write_bytes(http.read_bytes() + b"\n")
    assert main(with_evidence) == 2 and output.read_bytes() == saved

    http_report.update(passed=False, phases=[])
    write_atomic(http, http_report)
    assert main(args) == 1
    result = read_json(output)
    assert not result["http_passed"] and len(result["phases"]) == 2
