"""Evaluate HTTP load results alongside reviewed deployment observations."""

from __future__ import annotations

import re
from decimal import Decimal
from typing import Any

from app.evaluation.acceptance_data import fields, labels, objects
from app.evaluation.capacity_acceptance import CapacityPlan
from app.evaluation.usage_costs import parse_usd

LIMITS_PROFILE = "capacity-review-limits-v1"
EVIDENCE_PROFILE = "capacity-deployment-evidence-v1"
REPORT_PROFILE = "capacity-deployment-review-v1"
_RESOURCE_LIMITS = {
    "cpu_percent": (100, False),
    "rss_mib": (2**53 - 1, False),
    "db_active_connections": (2**31 - 1, True),
}


def _resource(value: object, name: str) -> int | float:
    upper, integer = _RESOURCE_LIMITS[name]
    if (
        isinstance(value, bool) or not isinstance(value, int | float)
        or (integer and type(value) is not int) or not 0 <= value <= upper
    ):
        raise ValueError("Resource value is invalid")
    return value


def _phase_rows(value: object, phase_ids: set[str]) -> dict[str, dict[str, Any]]:
    rows = objects(value, len(phase_ids))
    ids = labels([row.get("id") for row in rows])
    if not set(ids) <= phase_ids:
        raise ValueError("Unknown capacity phase")
    return dict(zip(ids, rows, strict=True))


def review_capacity(
    plan: CapacityPlan, http_report: object, http_report_sha256: str, limits_value: object,
    evidence_value: object = None,
) -> dict[str, Any]:
    limits = fields(limits_value, "profile manifest_sha256 max_cpu_percent max_rss_mib "
                                  "max_db_active_connections max_total_cost_usd")
    if (
        limits["profile"] != LIMITS_PROFILE or limits["manifest_sha256"] != plan.sha256
        or not isinstance(http_report, dict) or http_report.get("profile") != plan.profile
        or http_report.get("manifest_sha256") != plan.sha256
        or type(http_report.get("passed")) is not bool
    ):
        raise ValueError("HTTP report and limits must match the frozen workload")
    resource_limits = {name: _resource(limits[f"max_{name}"], name) for name in _RESOURCE_LIMITS}
    max_cost = parse_usd(limits["max_total_cost_usd"])
    phase_ids = {phase.id for phase in plan.phases}
    http_rows = _phase_rows(http_report.get("phases"), phase_ids)
    for phase in plan.phases:
        if phase.id in http_rows:
            row = http_rows[phase.id]
            if type(row.get("requests")) is not int or row["requests"] != phase.requests:
                raise ValueError("HTTP observation count must match the workload")
            if type(row.get("passed")) is not bool:
                raise ValueError("Invalid phase pass status")
    if http_report["passed"] != (
        set(http_rows) == phase_ids and all(row["passed"] for row in http_rows.values())
    ):
        raise ValueError("Inconsistent HTTP run status")

    source_sha256 = None
    evidence_rows = {}
    if evidence_value is not None:
        evidence = fields(evidence_value, "profile http_report_sha256 source_sha256 phases")
        source_sha256 = evidence["source_sha256"]
        if (
            evidence["profile"] != EVIDENCE_PROFILE
            or evidence["http_report_sha256"] != http_report_sha256
            or not isinstance(source_sha256, str)
            or not re.fullmatch(r"[0-9a-f]{64}", source_sha256)
        ):
            raise ValueError("Deployment evidence must match the exact HTTP report")
        evidence_rows = _phase_rows(evidence["phases"], set(http_rows))

    details = []
    charges: list[Decimal] = []
    resources_passed = True
    for phase in plan.phases:
        http = http_rows.get(phase.id, {})
        measured = evidence_rows.get(phase.id)
        if measured is not None:
            fields(measured, "id peak_cpu_percent peak_rss_mib peak_db_active_connections "
                             "actual_cost_usd")
        resources = {}
        for name, limit in resource_limits.items():
            value = None if measured is None else measured[f"peak_{name}"]
            observed = None if value is None else _resource(value, name)
            resources[name] = {"observed": observed, "limit": limit,
                               "passed": observed is not None and observed <= limit}
        resources_passed = resources_passed and all(check["passed"] for check in resources.values())
        cost_value = None if measured is None else measured["actual_cost_usd"]
        cost = None if cost_value is None else parse_usd(cost_value)
        if cost is not None:
            charges.append(cost)
        details.append({
            "id": phase.id, "started_at_utc": http.get("started_at_utc"),
            "finished_at_utc": http.get("finished_at_utc"),
            "http_passed": http.get("passed"), "resources": resources,
            "actual_cost_usd": None if cost is None else format(cost, "f"),
        })

    subtotal = sum(charges, Decimal(0))
    cost_complete = len(charges) == len(plan.phases)
    cost_passed = cost_complete and subtotal <= max_cost
    return {
        "profile": REPORT_PROFILE, "manifest_sha256": plan.sha256,
        "http_report_sha256": http_report_sha256, "source_sha256": source_sha256,
        "http_passed": http_report["passed"], "resources_passed": resources_passed,
        "phases": details,
        "cost": {"currency": "USD", "covered_phases": len(charges),
                 "expected_phases": len(plan.phases), "known_subtotal_usd": format(subtotal, "f"),
                 "total_usd": format(subtotal, "f") if cost_complete else None,
                 "max_total_usd": format(max_cost, "f"), "passed": cost_passed},
        "passed": http_report["passed"] and resources_passed and cost_passed,
    }
