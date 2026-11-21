"""Evaluate frozen pilot limits and bind reviewed billing to captured query IDs."""

from __future__ import annotations

import re
from decimal import Decimal
from typing import Any

from app.evaluation.acceptance_data import fields, labels, objects
from app.evaluation.predictions import fingerprint
from app.evaluation.usage_costs import parse_usd

GATE_PROFILE = "query-plan-pilot-gate-v1"
BILLING_PROFILE = "query-plan-billing-v1"
_SHA256 = re.compile(r"[0-9a-f]{64}")


def _number(
    value: object, lower: float, upper: float, *, integer: bool = False,
) -> float:
    if (
        not isinstance(value, int | float) or isinstance(value, bool)
        or (integer and type(value) is not int) or not lower <= value <= upper
    ):
        raise ValueError("Pilot threshold is outside its allowed range")
    return value


def _query_ids(capture: dict[str, Any], trial: str) -> dict[str, dict[str, str]]:
    return {
        plan: {
            record["id"]: record["response"]["query_id"]
            for record in capture[arm]["records"] if record["response"] is not None
        }
        for plan, arm in (("request_query", "baseline"), (trial, trial))
    }


def attach_pilot_billing(
    report: dict[str, Any], capture: dict[str, Any], value: object,
) -> None:
    ledger = fields(value, "profile paired_capture_sha256 currency evidence_sha256 entries")
    evidence = ledger["evidence_sha256"]
    if (
        ledger["profile"] != BILLING_PROFILE or ledger["currency"] != "USD"
        or ledger["paired_capture_sha256"] != report["paired_capture_sha256"]
        or not isinstance(evidence, str) or not _SHA256.fullmatch(evidence)
    ):
        raise ValueError("Billing must identify this paired capture and its reviewed evidence")

    trial = report.get("trial_plan", "reflection")
    by_plan = _query_ids(capture, trial)
    known = {query_id for arm in by_plan.values() for query_id in arm.values()}
    rows = objects(ledger["entries"], len(known))
    ids = labels([row.get("query_id") for row in rows])
    if not set(ids) <= known:
        raise ValueError("Billing contains a query outside the paired capture")
    charges = {
        identity: parse_usd(fields(row, "query_id cost_usd")["cost_usd"])
        for identity, row in zip(ids, rows, strict=True)
    }
    arms = {}
    totals: dict[str, Decimal] = {}
    for plan, by_case in by_plan.items():
        amounts = [charges[identity] for identity in by_case.values() if identity in charges]
        subtotal = sum(amounts, Decimal(0))
        if len(amounts) == report["case_count"]:
            totals[plan] = subtotal
        arms[plan] = {
            "expected_requests": report["case_count"],
            "captured_requests": len(by_case),
            "covered_requests": len(amounts),
            "known_subtotal_usd": format(subtotal, "f") if amounts else None,
            "total_usd": format(totals[plan], "f") if plan in totals else None,
        }
    complete = len(totals) == 2
    report["billing"] = {
        "profile": BILLING_PROFILE, "currency": "USD", "source": "operator_reviewed_ledger",
        "ledger_sha256": fingerprint(ledger), "evidence_sha256": evidence,
        "arms": arms, "complete": complete,
        "model_cost_delta_usd": (
            format(totals[trial] - totals["request_query"], "f") if complete else None
        ),
    }
    if complete:
        report["actual_cost_usd"] = format(sum(totals.values(), Decimal(0)), "f")


def check_pilot_gate(
    report: dict[str, Any], capture: dict[str, Any], value: object,
) -> dict[str, Any]:
    trial = report.get("trial_plan", "reflection")
    keys = "profile manifest_sha256 configuration_sha256 limits"
    rules = fields(value, keys if trial == "reflection" else keys + " trial_plan")
    if (
        rules["profile"] != GATE_PROFILE or report["split"] != "holdout"
        or rules["manifest_sha256"] != report["manifest_sha256"]
        or rules["configuration_sha256"] != report["configuration_sha256"]
        or rules.get("trial_plan", "reflection") != trial
    ):
        raise ValueError("Pilot limits must match the frozen holdout and configuration")
    limits = fields(
        rules["limits"],
        "min_cases min_accuracy_delta max_regressed_cases max_p95_delta_ms "
        "max_llm_calls_delta max_model_cost_delta_usd",
    )
    lower_bounds = {
        "min_cases": _number(limits["min_cases"], 1, 1000, integer=True),
        "min_accuracy_delta": _number(limits["min_accuracy_delta"], -1, 1),
    }
    upper_bounds = {
        "max_regressed_cases": _number(limits["max_regressed_cases"], 0, 1000, integer=True),
        "max_p95_delta_ms": _number(limits["max_p95_delta_ms"], -3.6e6, 3.6e6),
        "max_llm_calls_delta": _number(
            limits["max_llm_calls_delta"], -(2**63 - 1), 2**63 - 1, integer=True,
        ),
    }
    cost_limit = parse_usd(limits["max_model_cost_delta_usd"], signed=True)
    count = report["case_count"]
    acquisition = capture["acquisition"]
    requested = 2 * count
    completed = (
        acquisition["status"] == "completed"
        and all(
            type(acquisition.get(key)) is int and acquisition[key] == requested
            for key in ("planned_requests", "attempted_requests")
        )
        and all(len(arm) == count for arm in _query_ids(capture, trial).values())
    )
    latency, calls = report["paired_latency"], report["paired_llm_calls"]
    billed = report.get("billing", {})
    cost_delta = billed.get("model_cost_delta_usd")
    values = {
        "min_cases": count, "min_accuracy_delta": report["accuracy_delta"],
        "max_regressed_cases": report["outcomes"]["regressed"],
        "max_p95_delta_ms": latency["p95_delta_ms"] if latency["count"] == count else None,
        "max_llm_calls_delta": calls["delta"] if calls["count"] == count else None,
    }
    checks = {
        "capture_complete": {"observed": completed, "passed": completed},
        "billing_complete": {
            "observed": billed.get("complete", False), "passed": billed.get("complete", False),
        },
        **{
            key: {"observed": values[key], "limit": bound, "passed": values[key] >= bound}
            for key, bound in lower_bounds.items()
        },
        **{
            key: {
                "observed": values[key], "limit": bound,
                "passed": values[key] is not None and values[key] <= bound,
            }
            for key, bound in upper_bounds.items()
        },
        "max_model_cost_delta_usd": {
            "observed": cost_delta, "limit": limits["max_model_cost_delta_usd"],
            "passed": cost_delta is not None and Decimal(cost_delta) <= cost_limit,
        },
    }
    return {
        "profile": GATE_PROFILE, "thresholds_sha256": fingerprint(rules),
        "passed": all(check["passed"] for check in checks.values()), "checks": checks,
    }
