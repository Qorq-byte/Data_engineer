"""Replay captured query outcomes against a frozen SQLite snapshot."""

from __future__ import annotations

import re
from collections import Counter
from typing import Any

from app.evaluation.acceptance_data import BusinessDataset, fields, labels, objects
from app.evaluation.acceptance_sql import evaluate_sql, matches, selected, sql_self_check
from app.evaluation.predictions import export_predictions, fingerprint
from app.evaluation.scale_probe import percentile
from app.evaluation.sqlite_query import QueryResult

CAPTURE_PROFILE = "query-plan-capture-v1"
PAIR_CAPTURE_PROFILE = "query-plan-pair-capture-v1"
REVIEW_CAPTURE_PROFILE = "candidate-review-pair-capture-v1"
TRIAL_PROFILES = {"reflection": PAIR_CAPTURE_PROFILE, "candidate_review": REVIEW_CAPTURE_PROFILE}
SINGLE_CAPTURE_PROFILE = "query-plan-single-capture-v1"
REPORT_PROFILE = "query-plan-comparison-v1"
_SHA256 = re.compile(r"[0-9a-f]{64}")
_QUERY_ID = re.compile(r"q_[0-9a-f]{12}")


def _read_capture(
    value: object, *, dataset: BusinessDataset, plan_id: str, split: str,
) -> dict[str, Any]:
    capture = fields(value, "profile manifest_sha256 configuration_sha256 plan_id split records")
    configuration = capture["configuration_sha256"]
    if (
        capture["profile"] != CAPTURE_PROFILE or capture["plan_id"] != plan_id
        or capture["split"] != split or capture["manifest_sha256"] != dataset.hashes["manifest"]
        or not isinstance(configuration, str) or not _SHA256.fullmatch(configuration)
    ):
        raise ValueError("Capture must match the frozen dataset, split, plan and configuration")
    return capture


def _captured_execution(
    response: dict[str, Any], candidate: dict[str, Any],
) -> QueryResult | None:
    execution = response.get("execution")
    if not isinstance(execution, dict) or execution.get("truncated") is not False:
        return None
    attached = candidate.get("execution")
    if attached is None or fingerprint(execution) != fingerprint(attached):
        return None
    rows, columns = execution.get("rows"), execution.get("columns")
    count = execution.get("row_count")
    if (not isinstance(rows, list) or not isinstance(columns, list)
            or type(count) is not int or count != len(rows)
            or not all(isinstance(column, str) for column in columns)):
        return None
    if not all(
        isinstance(row, list) and len(row) == len(columns)
        and all(type(cell) in (int, float, str, bool, type(None)) for cell in row)
        for row in rows
    ):
        return None
    return QueryResult(tuple(columns), [tuple(row) for row in rows])


def checked_response_id(response: dict[str, Any], plan_id: str, question: str) -> str:
    if (
        response.get("plan_id") != plan_id or response.get("nl_text") != question
        or response.get("plan_selection") or response.get("status") not in ("completed", "failed")
    ):
        raise ValueError("Capture response does not match the requested plan and case")
    query_id = response.get("query_id")
    if not isinstance(query_id, str) or not _QUERY_ID.fullmatch(query_id):
        raise ValueError("Capture requires a server query ID")
    return query_id


def primary_candidate(response: dict[str, Any], db_id: str) -> dict[str, Any] | None:
    candidates = objects(response.get("candidates"), 1000)
    if not candidates or not response.get("primary_sql"):
        return None
    candidate = candidates[0]
    if (
        candidate.get("db_id") != db_id or candidate.get("dialect") != "sqlite"
        or candidate.get("sql_text") != response["primary_sql"] or not candidate.get("id")
    ):
        raise ValueError("Primary SQL is not bound to the frozen SQLite query target")
    return candidate


def checked_call_count(response: dict[str, Any]) -> int | None:
    calls = response.get("llm_calls")
    if calls is not None and (type(calls) is not int or not 0 <= calls <= 2**63 - 1):
        raise ValueError("Invalid captured call count")
    return calls


def _evaluate_capture(
    capture: dict[str, Any], dataset: BusinessDataset, cases: list[dict[str, Any]],
) -> tuple[dict[str, dict[str, Any]], set[str], dict[str, Any]]:
    records = objects(capture["records"], len(cases))
    ids = labels([record.get("id") for record in records])
    cases_by_id = {case["id"]: case for case in cases}
    if not set(ids) <= set(cases_by_id):
        raise ValueError("Capture contains a case outside the selected split")
    query_ids: set[str] = set()
    responses, bindings = [], []
    captured_results = {}
    observations: dict[str, dict[str, Any]] = {}
    for identity, record in zip(ids, records, strict=True):
        fields(record, "id latency_ms response")
        latency = record["latency_ms"]
        if latency is not None and (type(latency) not in (float, int) or not 0 <= latency <= 3.6e6):
            raise ValueError("Invalid captured end-to-end latency")
        observation = {"latency_ms": latency, "llm_calls": None, "error": "MISSING_RESPONSE"}
        observations[identity] = observation
        response = record["response"]
        if response is None:
            continue
        case = cases_by_id[identity]
        if not isinstance(response, dict):
            raise ValueError("Capture response must be a workflow object")
        query_id = checked_response_id(response, capture["plan_id"], case["question"])
        if query_id in query_ids:
            raise ValueError("Capture requires unique query IDs")
        query_ids.add(query_id)
        observation["llm_calls"] = checked_call_count(response)
        if response["status"] == "failed":
            observation["error"] = "WORKFLOW_FAILED"
            continue
        candidate = primary_candidate(response, case["db_id"])
        if candidate is None:
            observation["error"] = "PRIMARY_CANDIDATE_MISSING"
            continue
        if candidate.get("generation_source") == "prepared":
            observation["error"] = "NON_MODEL_CANDIDATE"
            continue
        bindings.append({
            "id": identity, "query_id": query_id, "candidate_id": candidate["id"],
            "db_id": case["db_id"],
        })
        responses.append(response)
        captured_results[identity] = _captured_execution(response, candidate)
        observation["error"] = None

    predictions = export_predictions(dataset.sql, responses, bindings) if bindings else []
    replay = evaluate_sql(dataset.sql, predictions, capture["split"], snapshot=dataset.snapshot)
    by_id = {row["id"]: row for row in predictions}
    for detail in replay["details"]:
        identity = detail["id"]
        result = captured_results.get(identity)
        detail["observed_execution_success"] = result is not None
        item = observations.setdefault(identity, {
            "latency_ms": None, "llm_calls": None, "error": "MISSING_RESPONSE",
        })
        if item["error"] is not None:
            continue
        if result is None:
            item["error"] = "CAPTURED_EXECUTION_INCOMPLETE"
        elif not detail["contract_match"]:
            item["error"] = detail["error_code"]
        elif not matches(cases_by_id[identity], "base", result, by_id[identity]["sql"]):
            item["error"] = "CAPTURED_RESULT_MISMATCH"
    return observations, query_ids, replay


def evaluate_query_capture(
    dataset: BusinessDataset, value: object, split: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    bundle = fields(value, "profile baseline acquisition")
    if split not in ("dev", "holdout") or bundle["profile"] != SINGLE_CAPTURE_PROFILE:
        raise ValueError("Select a single request_query capture and one split")
    capture = _read_capture(
        bundle["baseline"], dataset=dataset, plan_id="request_query", split=split,
    )
    cases = selected(dataset.sql["sql_cases"], split)
    records = objects(capture["records"], len(cases))
    acquisition = bundle["acquisition"]
    if not isinstance(acquisition, dict):
        raise ValueError("Invalid single-plan acquisition metadata")
    status, attempted = acquisition.get("status"), acquisition.get("attempted_requests")
    planned = acquisition.get("planned_requests")
    if (
        status not in ("completed", "stopped") or type(planned) is not int or planned != len(cases)
        or type(attempted) is not int or not len(records) <= attempted <= planned
        or attempted - len(records) > 1
        or (status == "completed" and len(records) != planned)
        or [record.get("id") for record in records] != [case["id"] for case in cases[:len(records)]]
    ):
        raise ValueError("Capture records and request counts must match the selected cases")

    observations, query_ids, result = _evaluate_capture(capture, dataset, cases)
    for detail in result["details"]:
        error = observations[detail["id"]]["error"]
        detail["execution_success"] = detail.pop("observed_execution_success")
        detail["contract_match"] = error is None
        detail["error_code"] = error
    details = result["details"]
    matched = sum(detail["contract_match"] for detail in details)
    result.update({
        "execution_success_count": sum(detail["execution_success"] for detail in details),
        "contract_match_count": matched,
        "robust_match_count": matched,
        "robust_match_rate": matched / len(cases),
        "error_counts": dict(Counter(detail["error_code"] for detail in details
                                     if detail["error_code"])),
        "measurement_scope": "Captured request_query execution and frozen SQLite replay",
    })
    return result, {
        "profile": SINGLE_CAPTURE_PROFILE, "plan_id": capture["plan_id"],
        "configuration_sha256": capture["configuration_sha256"],
        "capture_sha256": fingerprint(bundle), "status": status,
        "planned_requests": planned, "attempted_requests": attempted,
        "response_count": len(query_ids),
    }


def compare_query_plan_capture(
    dataset: BusinessDataset, capture: object, split: str,
) -> dict[str, Any]:
    profile = capture.get("profile") if isinstance(capture, dict) else None
    trial = next((name for name, kind in TRIAL_PROFILES.items() if kind == profile), None)
    if trial is None:
        raise ValueError("Invalid paired capture profile")
    bundle = fields(capture, f"profile baseline {trial} acquisition")
    acquisition = bundle["acquisition"]
    if (
        not isinstance(acquisition, dict)
        or acquisition.get("status") not in ("completed", "stopped")
    ):
        raise ValueError("Invalid paired capture")
    report = compare_query_plans(
        dataset, bundle["baseline"], bundle[trial], split, trial_plan=trial,
    )
    report["capture_status"] = acquisition["status"]
    report["paired_capture_sha256"] = fingerprint(bundle)
    return report


def compare_query_plans(
    dataset: BusinessDataset, baseline: object, trial: object, split: str = "holdout",
    *, trial_plan: str = "reflection",
) -> dict[str, Any]:
    if split not in ("dev", "holdout") or trial_plan not in TRIAL_PROFILES:
        raise ValueError("Select one split for a paired comparison")
    fixed = _read_capture(baseline, dataset=dataset, plan_id="request_query", split=split)
    alternate = _read_capture(trial, dataset=dataset, plan_id=trial_plan, split=split)
    if fixed["configuration_sha256"] != alternate["configuration_sha256"]:
        raise ValueError("Both captures must have matching environment configuration hashes")
    cases = selected(dataset.sql["sql_cases"], split)
    gold = sql_self_check(dataset.sql, snapshot=dataset.snapshot)
    left, left_ids, _ = _evaluate_capture(fixed, dataset, cases)
    right, right_ids, _ = _evaluate_capture(alternate, dataset, cases)
    if left_ids & right_ids:
        raise ValueError("Query IDs must differ between trial and baseline")

    details = []
    outcomes: Counter[str] = Counter()
    arms = {"request_query": left, trial_plan: right}
    for case in cases:
        identity = case["id"]
        before, after = left[identity]["error"] is None, right[identity]["error"] is None
        outcome = {
            (True, True): "both_correct", (False, True): "improved",
            (True, False): "regressed", (False, False): "both_incorrect",
        }[before, after]
        outcomes[outcome] += 1
        details.append({
            "id": identity, "outcome": outcome,
            **{name: arm[identity] for name, arm in arms.items()},
        })

    def paired(key: str) -> tuple[list[Any], list[Any]]:
        pairs = [(left[id_][key], right[id_][key]) for id_ in left
                 if left[id_][key] is not None and right[id_][key] is not None]
        return ([pair[0] for pair in pairs], [pair[1] for pair in pairs])

    before_lat, after_lat = paired("latency_ms")
    before_calls, after_calls = paired("llm_calls")
    left_p95, right_p95 = percentile(before_lat, 0.95), percentile(after_lat, 0.95)
    return {
        "profile": REPORT_PROFILE, "split": split, "case_count": len(cases),
        **({"trial_plan": trial_plan} if trial_plan != "reflection" else {}),
        "manifest_sha256": dataset.hashes["manifest"],
        "configuration_sha256": fixed["configuration_sha256"],
        "capture_sha256": {
            "request_query": fingerprint(fixed), trial_plan: fingerprint(alternate),
        },
        "gold_check_count": gold["gold_check_count"],
        "outcomes": {key: outcomes[key]
                     for key in ("both_correct", "improved", "regressed", "both_incorrect")},
        "accuracy": {
            plan: sum(row["error"] is None for row in arm.values()) / len(cases)
            for plan, arm in arms.items()
        },
        "accuracy_delta": (outcomes["improved"] - outcomes["regressed"]) / len(cases),
        "paired_latency": {
            "count": len(before_lat), "request_query_p95_ms": left_p95,
            f"{trial_plan}_p95_ms": right_p95,
            "p95_delta_ms": right_p95 - left_p95
            if left_p95 is not None and right_p95 is not None else None,
        },
        "paired_llm_calls": {
            "count": len(before_calls),
            "request_query_total": sum(before_calls) if before_calls else None,
            f"{trial_plan}_total": sum(after_calls) if after_calls else None,
            "delta": sum(after_calls) - sum(before_calls) if before_calls else None,
        },
        "actual_cost_usd": None,
        "details": details,
    }
