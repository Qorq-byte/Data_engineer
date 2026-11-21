"""Collect bounded, independent HTTP responses for workflow evaluation."""

from __future__ import annotations

import asyncio
import json
import re
import time
from collections import Counter
from dataclasses import dataclass
from typing import Any

import httpx

from app.evaluation.acceptance_data import BusinessDataset
from app.evaluation.acceptance_sql import selected, sql_self_check
from app.evaluation.artifacts import MAX_INPUT_BYTES, parse_json
from app.evaluation.plan_comparison import (
    CAPTURE_PROFILE,
    SINGLE_CAPTURE_PROFILE,
    TRIAL_PROFILES,
    checked_call_count,
    checked_response_id,
    primary_candidate,
)
from app.evaluation.predictions import export_cases

_BODY_LIMIT = 1024 * 1024
_RECORD_BYTES_LIMIT = MAX_INPUT_BYTES - 8192


@dataclass(frozen=True)
class CaptureSettings:
    tenant_id: int
    split: str
    configuration_sha256: str
    max_requests: int
    timeout_seconds: float


class CaptureRequestError(ValueError):
    """Fixed transport or protocol failure safe to include in a report."""


async def _request(
    client: httpx.AsyncClient, method: str, path: str, timeout: float,
    body: dict[str, Any] | None = None,
) -> tuple[int, dict[str, Any] | None]:
    raw = bytearray()
    try:
        async with asyncio.timeout(timeout), client.stream(
            method, path, json=body, follow_redirects=False,
            headers={"Accept": "application/json", "Accept-Encoding": "identity"},
        ) as response:
            status = response.status_code
            if status != 200:
                return status, None
            content_type = response.headers.get("content-type", "").split(";", 1)[0].strip()
            if (
                content_type != "application/json"
                or response.headers.get("content-encoding", "identity").lower() != "identity"
            ):
                raise CaptureRequestError("UNEXPECTED_CONTENT")
            async for chunk in response.aiter_raw():
                raw.extend(chunk)
                if len(raw) > _BODY_LIMIT:
                    raise CaptureRequestError("RESPONSE_TOO_LARGE")
    except (TimeoutError, httpx.TimeoutException):
        raise CaptureRequestError("CLIENT_TIMEOUT") from None
    except httpx.HTTPError:
        raise CaptureRequestError("HTTP_TRANSPORT_FAILED") from None
    try:
        payload = parse_json(bytes(raw))
    except ValueError:
        raise CaptureRequestError("INVALID_RESPONSE") from None
    if not isinstance(payload, dict):
        raise CaptureRequestError("INVALID_RESPONSE")
    return status, payload


async def _preflight(client: httpx.AsyncClient, tenant_id: int, db_id: str) -> None:
    status, identity = await _request(client, "GET", "/api/v1/auth/workspace", 10)
    if identity is None:
        raise CaptureRequestError(f"PREFLIGHT_HTTP_{status}")
    if type(identity.get("tenant_id")) is not int or identity["tenant_id"] != tenant_id:
        raise CaptureRequestError("TENANT_MISMATCH")
    if identity.get("access") not in ("tenant", "system"):
        raise CaptureRequestError("OPERATOR_REQUIRED")
    status, targets = await _request(client, "GET", "/api/v1/databases", 10)
    if targets is None:
        raise CaptureRequestError(f"PREFLIGHT_HTTP_{status}")
    databases = targets.get("databases")
    if not isinstance(databases, list) or not any(
        isinstance(entry, dict) and entry.get("db_id") == db_id
        and entry.get("db_type") == "sqlite" and entry.get("status") == "connected"
        for entry in databases
    ):
        raise CaptureRequestError("SQLITE_TARGET_NOT_CONNECTED")


async def capture_query_plans(
    client: httpx.AsyncClient, dataset: BusinessDataset, settings: CaptureSettings,
    *, baseline_only: bool = False, trial_plan: str = "reflection",
) -> dict[str, Any]:
    if (
        settings.split not in ("dev", "holdout")
        or not re.fullmatch(r"[0-9a-f]{64}", settings.configuration_sha256)
        or not 1 <= settings.tenant_id <= 2**31 - 1
        or not 1 <= settings.max_requests <= 2000
        or not 0.05 <= settings.timeout_seconds <= 120
        or trial_plan not in TRIAL_PROFILES
        or (baseline_only and trial_plan != "reflection")
    ):
        raise ValueError("Capture settings are invalid")
    cases = selected(export_cases(dataset.sql), settings.split)
    plans = ("request_query",) if baseline_only else ("request_query", trial_plan)
    planned = len(plans) * len(cases)
    if planned > settings.max_requests:
        raise ValueError("Workload exceeds its authorized HTTP request count")
    await asyncio.to_thread(sql_self_check, dataset.sql, snapshot=dataset.snapshot)
    db_id = cases[0]["db_id"]
    captures: dict[str, dict[str, Any]] = {
        plan: {
            "profile": CAPTURE_PROFILE, "manifest_sha256": dataset.hashes["manifest"],
            "configuration_sha256": settings.configuration_sha256,
            "split": settings.split, "plan_id": plan, "records": [],
        }
        for plan in plans
    }
    acquisition: dict[str, Any] = {
        "status": "stopped", "stop_reason": None, "tenant_id": settings.tenant_id,
        "planned_requests": planned, "attempted_requests": 0,
        "max_requests": settings.max_requests, "timeout_seconds": settings.timeout_seconds,
        "failure_counts": {},
    }
    bundle = {
        "profile": SINGLE_CAPTURE_PROFILE if baseline_only else TRIAL_PROFILES[trial_plan],
        "baseline": captures["request_query"], "acquisition": acquisition,
    }
    if not baseline_only:
        bundle[trial_plan] = captures[trial_plan]
    try:
        await _preflight(client, settings.tenant_id, db_id)
    except CaptureRequestError as exc:
        acquisition["stop_reason"] = str(exc)
        return bundle

    failure_counts: Counter[str] = Counter()
    acquisition["failure_counts"] = failure_counts
    record_bytes = 0
    query_ids: set[str] = set()
    for index, case in enumerate(cases):
        order = plans if index % 2 == 0 else plans[::-1]
        for plan in order:
            acquisition["attempted_requests"] += 1
            start = time.perf_counter()
            response = None
            failure = None
            stop = None
            try:
                status, response = await _request(
                    client, "POST", "/api/v1/query", settings.timeout_seconds,
                    {
                        "nl_text": case["question"], "database": db_id, "domain": case["domain"],
                        "execute": True, "auto_plan": False, "plan_id": plan, "num_candidates": 1,
                    },
                )
                if response is None:
                    failure = f"HTTP_{status}"
                    if status < 400 or status in (400, 401, 403, 404, 422):
                        stop = failure
                else:
                    query_id = checked_response_id(response, plan, case["question"])
                    checked_call_count(response)
                    if query_id in query_ids:
                        raise ValueError("Response reused a query ID")
                    if response["status"] == "completed":
                        primary_candidate(response, db_id)
                    else:
                        failure = "WORKFLOW_FAILED"
                    query_ids.add(query_id)
            except CaptureRequestError as exc:
                failure = stop = str(exc)
            except ValueError:
                failure = stop = "RESPONSE_BINDING_FAILED"
            record = {
                "id": case["id"], "latency_ms": (time.perf_counter() - start) * 1000,
                "response": response if stop is None else None,
            }
            try:
                serialized = json.dumps(record, ensure_ascii=False, allow_nan=False)
                size = len(serialized.encode("utf-8")) + 2
            except ValueError:
                failure = stop = "INVALID_RESPONSE"
                record["response"] = None
                size = len(json.dumps(record, ensure_ascii=False).encode("utf-8")) + 2
            if failure is not None:
                failure_counts[failure] += 1
            if record_bytes + size > _RECORD_BYTES_LIMIT:
                stop = "CAPTURE_SIZE_LIMIT"
            else:
                captures[plan]["records"].append(record)
                record_bytes += size
            if stop is not None:
                if stop != failure:
                    failure_counts[stop] += 1
                acquisition["stop_reason"] = stop
                return bundle
    acquisition["status"] = "completed"
    return bundle
