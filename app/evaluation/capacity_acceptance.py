"""Bounded, authenticated HTTP capacity observations for the fixed query plan."""

from __future__ import annotations

import asyncio
import hashlib
import time
from collections import Counter
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from app.audit.store import validated_usage
from app.evaluation.acceptance_data import fields, labels, objects
from app.evaluation.artifacts import parse_json, read_bounded_bytes
from app.evaluation.inputs import text
from app.evaluation.scale_probe import latency_summary

PROFILE = "capacity-http-v1"
MULTI_TENANT_PROFILE = "capacity-http-v2"
_RESPONSE_BYTES = 2 * 1024 * 1024


@dataclass(frozen=True)
class WorkloadCase:
    tenant_id: int
    transport: str
    body: dict[str, Any]


@dataclass(frozen=True)
class Phase:
    id: str
    requests: int
    concurrency: int
    timeout_seconds: float
    min_success_rate: float
    max_success_p95_ms: float
    max_sse_first_event_p95_ms: float | None
    min_success_rate_by_tenant: dict[int, float]
    max_success_p99_ms: float | None = None
    min_successful_rps: float | None = None
    max_total_llm_tokens: int | None = None


@dataclass(frozen=True)
class CapacityPlan:
    profile: str
    sha256: str
    tenant_ids: tuple[int, ...]
    workload: tuple[WorkloadCase, ...]
    phases: tuple[Phase, ...]


@dataclass
class Observation:
    tenant_id: int
    transport: str
    elapsed_ms: float = 0
    first_event_ms: float | None = None
    status: int | None = None
    error: str | None = None
    llm_calls: int | None = None
    usage: dict[str, Any] | None = None


class CapacityProbeError(ValueError):
    """Failure code that can be archived without raw response content."""


def _bound_int(value: Any, minimum: int, maximum: int) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError("Capacity plan integer is outside its allowed range")
    return value


def _bound_number(value: Any, minimum: float, maximum: float) -> float:
    if type(value) not in (int, float) or not minimum <= value <= maximum:
        raise ValueError("Capacity plan number is outside its allowed range")
    return float(value)


def load_capacity_plan(path: Path) -> CapacityPlan:
    raw = read_bounded_bytes(path, 512 * 1024)
    value = parse_json(raw)
    profile = value.get("profile") if isinstance(value, dict) else None
    tenant_ids: tuple[int, ...]
    if profile == PROFILE:
        plan = fields(value, "profile tenant_id workload phases")
        tenant_ids = (_bound_int(plan["tenant_id"], 1, 2**31 - 1),)
    elif profile == MULTI_TENANT_PROFILE:
        plan = fields(value, "profile tenant_ids workload phases")
        declared = plan["tenant_ids"]
        if not isinstance(declared, list) or not 2 <= len(declared) <= 16:
            raise ValueError("Multi-tenant capacity plans require between 2 and 16 tenants")
        tenant_ids = tuple(_bound_int(item, 1, 2**31 - 1) for item in declared)
        if len(set(tenant_ids)) != len(tenant_ids):
            raise ValueError("Capacity plan tenant IDs must be unique")
    else:
        raise ValueError("Unknown capacity profile")

    workloads = objects(plan["workload"], 32)
    labels([item.get("id") for item in workloads], nonempty=True)
    workload = []
    for item in workloads:
        case_fields = "id transport database domain nl_text"
        fields(item, case_fields if profile == PROFILE else f"{case_fields} tenant_id")
        tenant_id = (
            tenant_ids[0] if profile == PROFILE else _bound_int(item["tenant_id"], 1, 2**31 - 1)
        )
        if item["transport"] not in ("json", "sse"):
            raise ValueError("Capacity workload requires JSON or SSE")
        workload.append(WorkloadCase(tenant_id, item["transport"], {
            "database": text(item["database"], 128), "domain": text(item["domain"], 128),
            "nl_text": text(item["nl_text"], 10000), "plan_id": "request_query",
            "auto_plan": False, "num_candidates": 1, "execute": True, "validate": True,
        }))
    if {case.tenant_id for case in workload} != set(tenant_ids):
        raise ValueError("The workload must cover exactly the declared tenants")

    phase_rows = objects(plan["phases"], 8)
    phase_ids = labels([item.get("id") for item in phase_rows], nonempty=True)
    phases = []
    for identity, item in zip(phase_ids, phase_rows, strict=True):
        fields(item, "id requests concurrency timeout_seconds slo")
        requests = _bound_int(item["requests"], len(workload), 256)
        slo_fields = "min_success_rate max_success_p95_ms max_sse_first_event_p95_ms"
        raw_slo = item["slo"]
        optional_slo: tuple[str, ...] = (
            "max_success_p99_ms", "min_successful_rps", "max_total_llm_tokens",
        )
        if profile == MULTI_TENANT_PROFILE:
            optional_slo += ("min_success_rate_by_tenant",)
        if isinstance(raw_slo, dict):
            slo_fields += "".join(f" {key}" for key in optional_slo if key in raw_slo)
        slo = fields(raw_slo, slo_fields)
        rates = slo.get("min_success_rate_by_tenant", {})
        if not isinstance(rates, dict) or not set(rates) <= {str(tenant) for tenant in tenant_ids}:
            raise ValueError("Tenant success-rate thresholds require declared tenant IDs")
        first_event = slo["max_sse_first_event_p95_ms"]
        p99 = slo.get("max_success_p99_ms")
        throughput = slo.get("min_successful_rps")
        tokens = slo.get("max_total_llm_tokens")
        if first_event is not None and all(case.transport != "sse" for case in workload):
            raise ValueError("A first-event SLO requires an SSE workload")
        phases.append(Phase(
            id=identity,
            requests=requests,
            concurrency=_bound_int(item["concurrency"], 1, min(32, requests)),
            timeout_seconds=_bound_number(item["timeout_seconds"], 0.05, 120),
            min_success_rate=_bound_number(slo["min_success_rate"], 0, 1),
            max_success_p95_ms=_bound_number(slo["max_success_p95_ms"], 0.01, 120000),
            max_sse_first_event_p95_ms=(
                None if first_event is None else _bound_number(first_event, 0.01, 120000)
            ),
            min_success_rate_by_tenant={
                int(tenant): _bound_number(rate, 0, 1) for tenant, rate in rates.items()
            },
            max_success_p99_ms=None if p99 is None else _bound_number(p99, 0.01, 120000),
            min_successful_rps=(
                None if throughput is None else _bound_number(throughput, 0.001, 1_000_000)
            ),
            max_total_llm_tokens=(None if tokens is None else _bound_int(tokens, 0, 2**63 - 1)),
        ))
    return CapacityPlan(
        profile, hashlib.sha256(raw).hexdigest(), tenant_ids, tuple(workload), tuple(phases),
    )


def _elapsed_ms(start: float) -> float:
    return (time.perf_counter() - start) * 1000


async def _chunks(response: httpx.Response) -> AsyncIterator[bytes]:
    if response.headers.get("Content-Encoding", "identity").lower() != "identity":
        raise CapacityProbeError("UNEXPECTED_CONTENT_ENCODING")
    size = 0
    async for chunk in response.aiter_raw():
        size += len(chunk)
        if size > _RESPONSE_BYTES:
            raise CapacityProbeError("RESPONSE_TOO_LARGE")
        yield chunk


def _json(raw: bytes) -> dict[str, Any]:
    value = parse_json(raw)
    if not isinstance(value, dict):
        raise CapacityProbeError("INVALID_RESPONSE")
    return value


async def _sse_result(
    response: httpx.Response, observation: Observation, start: float,
) -> dict[str, Any]:
    pending = bytearray()
    kind = b""
    data = []
    terminal = None
    async for chunk in _chunks(response):
        pending.extend(chunk)
        lines = pending.split(b"\n")
        pending = lines.pop()
        for raw_line in lines:
            line = bytes(raw_line).rstrip(b"\r")
            if line:
                key, _, value = line.partition(b":")
                if key == b"event":
                    kind = value.removeprefix(b" ")
                elif key == b"data":
                    data.append(value.removeprefix(b" "))
                continue
            if data and observation.first_event_ms is None:
                observation.first_event_ms = _elapsed_ms(start)
            if kind in (b"done", b"error"):
                if terminal is not None:
                    raise CapacityProbeError("SSE_MULTIPLE_TERMINALS")
                terminal = (kind, _json(b"\n".join(data)))
            kind, data = b"", []
    if terminal is None:
        raise CapacityProbeError("SSE_INCOMPLETE")
    if terminal[0] == b"error":
        code = terminal[1].get("code")
        if code in ("QUERY_TIMEOUT", "QUERY_CAPACITY", "SHARED_STATE_UNAVAILABLE"):
            raise CapacityProbeError(f"SSE_{code}")
        raise CapacityProbeError("SSE_ERROR")
    return terminal[1]


def _validate_result(result: dict[str, Any], observation: Observation) -> None:
    calls = result.get("llm_calls")
    if type(calls) is int and 0 <= calls <= 2**63 - 1:
        observation.llm_calls = calls
    try:
        observation.usage = validated_usage(result.get("llm_usage"))
    except ValueError:
        observation.usage = None
    if result.get("status") != "completed" or result.get("plan_id") != "request_query":
        raise CapacityProbeError("WORKFLOW_FAILED")
    validation, execution = result.get("validation"), result.get("execution")
    if not isinstance(validation, dict) or validation.get("passed") is not True:
        raise CapacityProbeError("VALIDATION_FAILED")
    if not isinstance(execution, dict) or execution.get("truncated") is not False:
        raise CapacityProbeError("INCOMPLETE_EXECUTION")
    rows, count = execution.get("rows"), execution.get("row_count")
    if not isinstance(rows, list) or type(count) is not int or count != len(rows):
        raise CapacityProbeError("INCOMPLETE_EXECUTION")


async def _observe(
    client: httpx.AsyncClient, case: WorkloadCase, timeout: float,
) -> Observation:
    start = time.perf_counter()
    observation = Observation(case.tenant_id, case.transport)
    streaming = case.transport == "sse"
    path = "/api/v1/query/stream" if streaming else "/api/v1/query"
    content_type = "text/event-stream" if streaming else "application/json"
    try:
        async with asyncio.timeout(timeout), client.stream(
            "POST", path, json=case.body, follow_redirects=False,
            headers={"Accept": content_type, "Accept-Encoding": "identity"},
        ) as response:
            observation.status = response.status_code
            if response.status_code != 200:
                raise CapacityProbeError(f"HTTP_{response.status_code}")
            actual_type = response.headers.get("Content-Type", "").split(";", 1)[0].strip()
            if actual_type != content_type:
                raise CapacityProbeError("UNEXPECTED_CONTENT_TYPE")
            result = (
                await _sse_result(response, observation, start) if streaming
                else _json(b"".join([chunk async for chunk in _chunks(response)]))
            )
            _validate_result(result, observation)
    except CapacityProbeError as exc:
        observation.error = str(exc)
    except (TimeoutError, httpx.TimeoutException):
        observation.error = "CLIENT_TIMEOUT"
    except httpx.HTTPError:
        observation.error = "SSE_DISCONNECTED" if streaming and observation.status else (
            "HTTP_TRANSPORT_FAILED"
        )
    except ValueError:
        observation.error = "INVALID_RESPONSE"
    observation.elapsed_ms = _elapsed_ms(start)
    return observation


def _metrics(observations: list[Observation]) -> dict[str, Any]:
    good = [row for row in observations if row.error is None]
    seen_usage = [row.usage for row in observations if row.usage is not None]
    seen_calls = [row.llm_calls for row in observations if row.llm_calls is not None]
    accounted_tokens = [
        row.usage["known_prompt_tokens"] + row.usage["known_completion_tokens"]
        for row in observations if row.usage is not None and row.usage["complete"]
        and (row.usage["attempt_count"] > 0 or row.llm_calls == 0)
    ]
    first_events = [row.first_event_ms for row in observations if row.first_event_ms is not None]
    good_first_events = [row.first_event_ms for row in good if row.first_event_ms is not None]
    first_event_latency = latency_summary(first_events, good_first_events)
    first_event_latency["policy"] = "nearest-rank over SSE requests that produced a data event"
    return {
        "requests": len(observations), "successful": len(good),
        "success_rate": len(good) / len(observations),
        "http_status_counts": dict(Counter(row.status for row in observations if row.status)),
        "failure_counts": dict(Counter(row.error for row in observations if row.error)),
        "latency": latency_summary(
            [row.elapsed_ms for row in observations], [row.elapsed_ms for row in good],
        ),
        "sse_first_event_latency": first_event_latency,
        "sse_first_event_missing": sum(row.transport == "sse" for row in observations)
        - len(first_events),
        "llm_usage": {
            "requests_with_call_count": len(seen_calls),
            "known_calls": sum(seen_calls) if seen_calls else None,
            "requests_with_usage": len(seen_usage),
            "requests_with_complete_usage": sum(item["complete"] for item in seen_usage),
            "requests_with_accounted_tokens": len(accounted_tokens),
            "total_llm_tokens": (
                sum(accounted_tokens) if len(accounted_tokens) == len(observations) else None
            ),
            "known_prompt_tokens": sum(item["known_prompt_tokens"] for item in seen_usage)
            if seen_usage else None,
            "known_completion_tokens": sum(item["known_completion_tokens"] for item in seen_usage)
            if seen_usage else None,
        },
    }


async def _run_phase(
    clients: Mapping[int, httpx.AsyncClient], phase: Phase, plan: CapacityPlan,
) -> dict[str, Any]:
    slots = asyncio.Semaphore(phase.concurrency)

    async def one(index: int) -> Observation:
        async with slots:
            case = plan.workload[index % len(plan.workload)]
            return await _observe(clients[case.tenant_id], case, phase.timeout_seconds)

    started_at = datetime.now(UTC)
    started = time.perf_counter()
    rows = await asyncio.gather(*(one(index) for index in range(phase.requests)))
    elapsed = time.perf_counter() - started
    finished_at = datetime.now(UTC)
    metrics = _metrics(rows)
    successful_rps = metrics["successful"] / elapsed
    p95 = metrics["latency"]["success"]["p95_ms"]
    p99 = metrics["latency"]["success"]["p99_ms"]
    first_p95 = metrics["sse_first_event_latency"]["success"]["p95_ms"]
    total_llm_tokens = metrics["llm_usage"]["total_llm_tokens"]
    thresholds = {
        "min_success_rate": phase.min_success_rate,
        "max_success_p95_ms": phase.max_success_p95_ms,
        "max_sse_first_event_p95_ms": phase.max_sse_first_event_p95_ms,
    }
    slo = {
        "min_success_rate": metrics["success_rate"] >= phase.min_success_rate,
        "max_success_p95_ms": p95 is not None and p95 <= phase.max_success_p95_ms,
    }
    if phase.max_sse_first_event_p95_ms is not None:
        slo["max_sse_first_event_p95_ms"] = (
            first_p95 is not None and first_p95 <= phase.max_sse_first_event_p95_ms
        )
    if phase.max_success_p99_ms is not None:
        thresholds["max_success_p99_ms"] = phase.max_success_p99_ms
        slo["max_success_p99_ms"] = p99 is not None and p99 <= phase.max_success_p99_ms
    if phase.min_successful_rps is not None:
        thresholds["min_successful_rps"] = phase.min_successful_rps
        slo["min_successful_rps"] = successful_rps >= phase.min_successful_rps
    if phase.max_total_llm_tokens is not None:
        thresholds["max_total_llm_tokens"] = phase.max_total_llm_tokens
        slo["max_total_llm_tokens"] = (
            total_llm_tokens is not None and total_llm_tokens <= phase.max_total_llm_tokens
        )
    by_transport = {}
    for transport in sorted({row.transport for row in rows}):
        by_transport[transport] = _metrics([row for row in rows if row.transport == transport])
    report = {
        "id": phase.id, "concurrency": phase.concurrency, "elapsed_seconds": elapsed,
        "started_at_utc": started_at.isoformat(), "finished_at_utc": finished_at.isoformat(),
        "request_timeout_seconds": phase.timeout_seconds,
        "attempt_rps": len(rows) / elapsed, "successful_rps": successful_rps, "slo": thresholds,
        "slo_checks": slo, "passed": all(slo.values()), "by_transport": by_transport,
        **metrics,
    }
    if plan.profile == MULTI_TENANT_PROFILE:
        by_tenant = {
            str(tenant): _metrics([row for row in rows if row.tenant_id == tenant])
            for tenant in plan.tenant_ids
        }
        tenant_slo = {
            str(tenant): by_tenant[str(tenant)]["success_rate"] >= minimum
            for tenant, minimum in phase.min_success_rate_by_tenant.items()
        }
        report["by_tenant"] = by_tenant
        report["tenant_slo_checks"] = tenant_slo
        report["slo"]["min_success_rate_by_tenant"] = {
            str(tenant): minimum for tenant, minimum in phase.min_success_rate_by_tenant.items()
        }
        report["passed"] = report["passed"] and all(tenant_slo.values())
    return report


async def _preflight(client: httpx.AsyncClient, tenant_id: int) -> str | None:
    try:
        async with asyncio.timeout(10), client.stream(
            "GET", "/api/v1/auth/workspace", follow_redirects=False,
            headers={"Accept-Encoding": "identity"},
        ) as response:
            if response.status_code != 200:
                raise CapacityProbeError(f"HTTP_{response.status_code}")
            who = _json(b"".join([chunk async for chunk in _chunks(response)]))
            tenant = who.get("tenant_id")
            if type(tenant) is not int or tenant != tenant_id:
                raise CapacityProbeError("TENANT_MISMATCH")
            if who.get("access") not in ("system", "tenant"):
                raise CapacityProbeError("OPERATOR_REQUIRED")
    except CapacityProbeError as exc:
        return str(exc)
    except (ValueError, TimeoutError, httpx.HTTPError):
        return "PREFLIGHT_FAILED"
    return None


async def run_capacity_plan(
    client: httpx.AsyncClient | Mapping[int, httpx.AsyncClient], plan: CapacityPlan,
) -> dict[str, Any]:
    clients = {plan.tenant_ids[0]: client} if isinstance(client, httpx.AsyncClient) else client
    if set(clients) != set(plan.tenant_ids):
        raise ValueError("Capacity clients must match the declared tenants")
    report: dict[str, Any] = {
        "profile": plan.profile, "manifest_sha256": plan.sha256,
        "phases": [], "passed": False, "actual_cost_usd": None,
    }
    preflight = {
        str(tenant): await _preflight(clients[tenant], tenant) for tenant in plan.tenant_ids
    }
    if plan.profile == PROFILE:
        report["tenant_id"] = plan.tenant_ids[0]
    else:
        report["tenant_ids"] = list(plan.tenant_ids)
        report["tenant_preflight"] = {
            tenant: {"passed": error is None, "error": error}
            for tenant, error in preflight.items()
        }
    errors = [error for error in preflight.values() if error is not None]
    if errors:
        report["preflight_error"] = (
            errors[0] if plan.profile == PROFILE else "TENANT_PREFLIGHT_FAILED"
        )
        return report
    report["phases"] = [await _run_phase(clients, phase, plan) for phase in plan.phases]
    report["passed"] = all(phase["passed"] for phase in report["phases"])
    return report
