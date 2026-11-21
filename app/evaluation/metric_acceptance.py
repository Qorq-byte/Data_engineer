"""Check a frozen metric catalog against independent REST and MCP results."""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import httpx

from app.evaluation.acceptance_data import fields, labels, objects
from app.evaluation.artifacts import parse_json, read_bounded_bytes
from app.evaluation.inputs import text
from app.evaluation.predictions import fingerprint
from app.knowledge.metric_catalog import MetricSelection, load_catalog

if TYPE_CHECKING:
    from fastmcp import Client
    from fastmcp.client.transports import StreamableHttpTransport

PROFILE = "metric-acceptance-v1"
_NODES = ["metric_resolve", "validate_sql", "execute_sql"]
_RESPONSE_LIMIT = 2 * 1024 * 1024


@dataclass(frozen=True)
class MetricCase:
    id: str
    selection: MetricSelection
    expected_rows: list[list[Any]]

    @property
    def names(self) -> list[str]:
        return [metric.name for metric in self.selection.metrics]

    @property
    def columns(self) -> list[str]:
        return list(self.selection.output_columns)

    def metric_arguments(self, *, execute: bool) -> dict[str, Any]:
        selection = self.selection
        return {
            "execute": execute, "domain": selection.domain,
            "metric_catalog_version": selection.version, "metrics": self.names,
            "metric_dimensions": list(selection.dimensions),
            "metric_filters": dict(selection.filters),
            **({"metric_grain": selection.grain} if selection.grain else {}),
        }

    def request(self, *, execute: bool) -> dict[str, Any]:
        return {
            "nl_text": "Verify approved metrics", "database": self.selection.database,
            "plan_id": "metric_query", "auto_plan": False, "num_candidates": 1,
            "link_schema": True, "validate": True, **self.metric_arguments(execute=execute),
        }


@dataclass(frozen=True)
class MetricAcceptance:
    tenant_id: int
    cases: tuple[MetricCase, ...]
    cases_sha256: str


class MetricCheckError(ValueError):
    """A fixed check code suitable for recording without response content."""


def load_metric_acceptance(catalog_path: Path, cases_path: Path) -> MetricAcceptance:
    catalog = load_catalog(str(catalog_path))
    raw = read_bounded_bytes(cases_path, 1024 * 1024)
    bundle = fields(parse_json(raw), "profile catalog_sha256 tenant_id domain database cases")
    if bundle["profile"] != PROFILE or bundle["catalog_sha256"] != catalog.sha256:
        raise ValueError("Acceptance cases must match the approved metric catalog SHA-256")
    tenant_id = bundle["tenant_id"]
    if type(tenant_id) is not int or not 1 <= tenant_id <= 2**31 - 1:
        raise ValueError("Metric acceptance requires a valid tenant ID")
    domain, database = text(bundle["domain"], 128), text(bundle["database"], 128)
    cases = objects(bundle["cases"], 32)
    ids = labels([case.get("id") for case in cases], nonempty=True)
    selected = []
    for identity, case in zip(ids, cases, strict=True):
        case_fields = "id metrics dimensions filters expected_rows"
        fields(case, case_fields + " grain" if "grain" in case else case_fields)
        grain = case.get("grain", "")
        if not isinstance(grain, str):
            raise ValueError("Invalid metric grain")
        selection = catalog.select(
            version=catalog.version, domain=domain, database=database, tenant_id=tenant_id,
            metrics=case["metrics"], dimensions=case["dimensions"], filters=case["filters"],
            grain=grain,
        )
        expected = case["expected_rows"]
        width = len(selection.output_columns)
        if not isinstance(expected, list) or len(expected) > 100 or any(
            not isinstance(row, list) or len(row) != width or any(
                type(cell) not in (str, int, float, bool, type(None)) for cell in row
            )
            for row in expected
        ):
            raise ValueError("Expected rows must contain up to 100 ordered rows of JSON scalars")
        selected.append(MetricCase(identity, selection, expected))
    return MetricAcceptance(tenant_id, tuple(selected), hashlib.sha256(raw).hexdigest())


async def _response(
    client: httpx.AsyncClient, method: str, path: str, *, body: dict[str, Any] | None = None,
    params: dict[str, str] | None = None, stream: bool = False, status: int = 200,
) -> dict[str, Any]:
    raw = bytearray()
    try:
        async with (
            asyncio.timeout(30),
            client.stream(
                method, path, params=params, json=body, follow_redirects=False,
                headers={"Accept-Encoding": "identity"},
            ) as response,
        ):
            if response.status_code != status:
                raise MetricCheckError(f"UNEXPECTED_HTTP_{response.status_code}")
            content_type = response.headers.get("content-type", "").split(";", 1)[0].strip()
            if content_type != ("text/event-stream" if stream else "application/json"):
                raise MetricCheckError("UNEXPECTED_CONTENT_TYPE")
            if response.headers.get("content-encoding", "identity").lower() != "identity":
                raise MetricCheckError("UNEXPECTED_CONTENT_ENCODING")
            async for chunk in response.aiter_raw(chunk_size=65536):
                raw.extend(chunk)
                if len(raw) > _RESPONSE_LIMIT:
                    raise MetricCheckError("RESPONSE_TOO_LARGE")
    except (TimeoutError, httpx.HTTPError):
        raise MetricCheckError("HTTP_TRANSPORT_FAILED") from None

    try:
        return _stream_result(raw) if stream else _object(parse_json(bytes(raw)))
    except MetricCheckError:
        raise
    except ValueError:
        raise MetricCheckError("INVALID_RESPONSE") from None


def _object(value: object) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise MetricCheckError("INVALID_RESPONSE")
    return value


def _stream_result(raw: bytearray) -> dict[str, Any]:
    frames = raw.decode("utf-8").replace("\r\n", "\n").replace("\r", "\n").split("\n\n")
    terminal = []
    for frame in frames[:-1]:
        event, data = "", []
        for line in frame.split("\n"):
            field, _, value = line.partition(":")
            if field == "event":
                event = value.removeprefix(" ")
            elif field == "data":
                data.append(value.removeprefix(" "))
        if event in {"done", "error"}:
            terminal.append((event, "\n".join(data)))
    if len(terminal) != 1 or terminal[0][0] != "done":
        raise MetricCheckError("SSE_TERMINAL_EVENT_INVALID")
    return _object(parse_json(terminal[0][1].encode("utf-8")))


def _catalog_identity(case: MetricCase) -> dict[str, str]:
    selection = case.selection
    return {
        "version": selection.version, "sha256": selection.catalog_sha256,
        "domain": selection.domain, "database": selection.database,
    }


def _workflow(result: dict[str, Any], case: MetricCase) -> None:
    if result.get("status") != "completed" or result.get("plan_id") != "metric_query":
        raise MetricCheckError("METRIC_WORKFLOW_FAILED")
    if type(result.get("llm_calls")) is not int or result["llm_calls"] != 0:
        raise MetricCheckError("MODEL_CALL_OBSERVED")
    plan = _object(result.get("plan_output"))
    catalog = _object(plan.get("metric_catalog"))
    expected = {**_catalog_identity(case), "metrics": case.names}
    if case.selection.grain:
        expected["grain"] = case.selection.grain
    if any(catalog.get(key) != value for key, value in expected.items()):
        raise MetricCheckError("CATALOG_MISMATCH")
    if (
        plan.get("node_names") != _NODES
        or _object(result.get("validation")).get("passed") is not True
    ):
        raise MetricCheckError("VALIDATION_REQUIRED")
    candidates = result.get("candidates")
    if (not isinstance(candidates, list) or len(candidates) != 1
            or _object(candidates[0]).get("generation_source") != "prepared"):
        raise MetricCheckError("PREPARED_CANDIDATE_REQUIRED")


async def _preflight(client: httpx.AsyncClient, acceptance: MetricAcceptance) -> None:
    case = acceptance.cases[0]
    who = await _response(client, "GET", "/api/v1/auth/workspace")
    if type(who.get("tenant_id")) is not int or who["tenant_id"] != acceptance.tenant_id:
        raise MetricCheckError("TENANT_MISMATCH")
    await _discover(client, acceptance)
    body = case.request(execute=False)
    denied = await _response(client, "POST", "/api/v1/query", status=400, body={
        **body, "metric_catalog_version": _stale_version(case),
    })
    if _object(denied.get("detail")).get("code") != "INVALID_REQUEST":
        raise MetricCheckError("STALE_VERSION_NOT_REJECTED")
    shape = await _response(client, "POST", "/api/v1/query", body=body)
    _workflow(shape, case)
    if "execution" not in shape or shape["execution"] is not None:
        raise MetricCheckError("EXECUTION_OPT_OUT_FAILED")


def _stale_version(case: MetricCase) -> str:
    return "acceptance-old" if case.selection.version == "acceptance-stale" else "acceptance-stale"


async def _discover(client: httpx.AsyncClient, acceptance: MetricAcceptance) -> None:
    expected = _catalog_identity(acceptance.cases[0])
    result = await _response(client, "GET", "/api/v1/metrics", params={
        "domain": expected["domain"], "database": expected["database"],
    })
    _check_discovery(result, acceptance)


def _check_discovery(result: dict[str, Any], acceptance: MetricAcceptance) -> None:
    expected = _catalog_identity(acceptance.cases[0])
    if any(result.get(key) != value for key, value in expected.items()):
        raise MetricCheckError("DISCOVERY_CATALOG_MISMATCH")
    metrics = result.get("metrics")
    if not isinstance(metrics, list) or len(metrics) > 100:
        raise MetricCheckError("INVALID_RESPONSE")
    names = [_object(metric).get("name") for metric in metrics]
    if any(name not in names for case in acceptance.cases for name in case.names):
        raise MetricCheckError("DISCOVERY_METRIC_MISSING")


async def _verify_cases(
    acceptance: MetricAcceptance, checks: list[dict[str, Any]], transports: tuple[str, ...],
    execute: Callable[[MetricCase, str], Awaitable[dict[str, Any]]],
) -> bool:
    for case in acceptance.cases:
        expected = fingerprint(case.expected_rows)
        for transport in transports:
            check: dict[str, Any] = {
                "case": case.id, "transport": transport, "expected_sha256": expected,
            }
            checks.append(check)
            try:
                result = await execute(case, transport)
                _workflow(result, case)
                if transport == "mcp" and result.get("executed") is not True:
                    raise MetricCheckError("EXECUTION_REQUIRED")
                execution = result if transport == "mcp" else _object(result.get("execution"))
                if execution.get("truncated") is not False:
                    raise MetricCheckError("RESULT_TRUNCATED")
                if execution.get("columns") != case.columns:
                    raise MetricCheckError("COLUMN_MISMATCH")
                check["observed_sha256"] = fingerprint(execution.get("rows"))
                if (check["observed_sha256"] != expected
                        or type(execution.get("row_count")) is not int
                        or execution["row_count"] != len(case.expected_rows)):
                    raise MetricCheckError("ROW_MISMATCH")
            except MetricCheckError as exc:
                check.update(passed=False, error=str(exc))
                if str(exc) == "CATALOG_MISMATCH":
                    return False
            except ValueError:
                check.update(passed=False, error="INVALID_RESPONSE")
            else:
                check.update(passed=True, row_count=len(case.expected_rows))
    return True


async def _mcp_tool(
    mcp: Client[StreamableHttpTransport], name: str, arguments: dict[str, Any],
) -> dict[str, Any]:
    try:
        response = await mcp.call_tool(name, arguments, raise_on_error=False)
    except Exception:
        raise MetricCheckError("MCP_CALL_FAILED") from None
    if response.is_error:
        raise MetricCheckError("MCP_TOOL_ERROR")
    return _object(response.structured_content)


async def _mcp_discover(mcp: Client[StreamableHttpTransport], acceptance: MetricAcceptance) -> None:
    selection = acceptance.cases[0].selection
    result = await _mcp_tool(mcp, "list_metrics", {
        "domain": selection.domain, "db_id": selection.database,
    })
    if result.get("status") != "completed":
        raise MetricCheckError("MCP_DISCOVERY_FAILED")
    _check_discovery(result, acceptance)


async def _verify_mcp(
    mcp: Client[StreamableHttpTransport], acceptance: MetricAcceptance,
    checks: list[dict[str, Any]],
) -> None:
    async def execute(case: MetricCase, _transport: str) -> dict[str, Any]:
        return await _mcp_tool(mcp, "metric_query", {
            "db_id": case.selection.database, **case.metric_arguments(execute=True),
        })

    phase = "mcp_preflight"
    try:
        async with mcp:
            await _mcp_discover(mcp, acceptance)
            case = acceptance.cases[0]
            arguments = {"db_id": case.selection.database, **case.metric_arguments(execute=False)}
            denied = await _mcp_tool(mcp, "metric_query", {
                **arguments, "metric_catalog_version": _stale_version(case),
            })
            if (denied.get("status"), denied.get("error_code"), denied.get("http_status")) != (
                "failed", "INVALID_REQUEST", 400,
            ):
                raise MetricCheckError("STALE_VERSION_NOT_REJECTED")
            shape = await _mcp_tool(mcp, "metric_query", arguments)
            _workflow(shape, case)
            if shape.get("executed") is not False or shape.get("rows") != []:
                raise MetricCheckError("EXECUTION_OPT_OUT_FAILED")
            checks.append({"check": phase, "passed": True})
            phase = "mcp_cases"
            if not await _verify_cases(acceptance, checks, ("mcp",), execute):
                return
            phase = "mcp_final_discovery"
            await _mcp_discover(mcp, acceptance)
        checks.append({"check": phase, "passed": True})
    except MetricCheckError as exc:
        checks.append({"check": phase, "passed": False, "error": str(exc)})
    except Exception:
        # SDK session setup and teardown may raise a task-group exception.
        checks.append({"check": phase, "passed": False, "error": "MCP_TRANSPORT_FAILED"})


async def verify_metric_acceptance(
    client: httpx.AsyncClient, acceptance: MetricAcceptance, *,
    mcp: Client[StreamableHttpTransport] | None = None,
) -> dict[str, Any]:
    async def execute(case: MetricCase, transport: str) -> dict[str, Any]:
        path = "/api/v1/query/stream" if transport == "sse" else "/api/v1/query"
        return await _response(
            client, "POST", path, body=case.request(execute=True), stream=transport == "sse",
        )

    checks: list[dict[str, Any]] = []
    report = {
        "profile": PROFILE, "tenant_id": acceptance.tenant_id,
        "cases_sha256": acceptance.cases_sha256, "catalog": _catalog_identity(acceptance.cases[0]),
        "checks": checks, "passed": False,
    }
    try:
        await _preflight(client, acceptance)
    except MetricCheckError as exc:
        checks.append({"check": "preflight", "passed": False, "error": str(exc)})
        return report
    checks.append({"check": "preflight", "passed": True})
    if not await _verify_cases(acceptance, checks, ("json", "sse"), execute):
        return report
    try:
        await _discover(client, acceptance)
    except MetricCheckError as exc:
        checks.append({"check": "final_discovery", "passed": False, "error": str(exc)})
    else:
        checks.append({"check": "final_discovery", "passed": True})
        if mcp is not None:
            await _verify_mcp(mcp, acceptance, checks)
    report["passed"] = all(check["passed"] for check in checks)
    return report
