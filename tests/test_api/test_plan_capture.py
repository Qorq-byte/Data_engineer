"""Collect HTTP workflow pairs and replay them with the offline comparison command."""

import asyncio
import json
from copy import deepcopy
from dataclasses import replace

import httpx
import pytest

from app.config.settings import settings
from app.db.connections import ConnectionFactory, DatabaseType
from app.evaluation.acceptance_data import load_business_dataset
from app.evaluation.artifacts import read_json, write_atomic
from app.evaluation.business_quality import METRICS, THRESHOLD_VERSION
from app.evaluation.plan_capture import CaptureSettings, capture_query_plans
from app.evaluation.predictions import fingerprint
from scripts.capture_document_rankings import main as capture_rankings
from scripts.capture_query_plans import main
from scripts.compare_query_plans import main as compare
from scripts.evaluate_acceptance import main as evaluate
from tests.test_api.test_capacity_acceptance import _serve
from tests.test_api.test_plan_execution import plan_env as plan_env
from tests.test_evaluation.test_business_acceptance import business_dataset as business_dataset
from tests.test_evaluation.test_business_acceptance import freeze
from tests.test_evaluation.test_plan_comparison import ConfirmedRouter


def _arguments(origin, manifest, capture_path, *, tenant=1, max_requests=4):
    return [
        "--base-url", origin, "--tenant-id", str(tenant), "--dataset-manifest", str(manifest),
        "--configuration-sha256", fingerprint({"runtime": "scripted SQLite"}),
        "--max-requests", str(max_requests), "--output", str(capture_path),
    ]


async def test_http_capture_alternates_plans_and_replays_the_same_holdout(
    business_dataset, plan_env, tmp_path, monkeypatch,
):
    snapshot = business_dataset.parent / "snapshot.sqlite3"
    await ConnectionFactory.create(DatabaseType.SQLITE, {"path": str(snapshot)}, "billing")
    bad = "SELECT min(amount) AS total FROM invoices"
    router = ConfirmedRouter([
        bad, "SELECT missing_column FROM invoices", "SELECT sum(amount) AS total FROM invoices",
        bad, "SELECT max(amount) AS total FROM invoices",
    ])
    fixture = plan_env(router)
    token = fixture.headers["Authorization"][7:]
    monkeypatch.setenv("DE_PLAN_CAPTURE_TOKEN", token)
    monkeypatch.setenv("DE_TRACE_DIR", str(tmp_path / "capture-traces"))
    capture_path, report_path = tmp_path / "paired.json", tmp_path / "report.json"
    try:
        async with _serve(fixture.app) as origin:
            args = _arguments(origin, business_dataset, capture_path)
            capture_path.write_bytes(b"previous")
            assert await asyncio.to_thread(
                main, _arguments(origin, business_dataset, capture_path, max_requests=3),
            ) == 2
            assert capture_path.read_bytes() == b"previous" and router.calls == []
            assert await asyncio.to_thread(main, args) == 0
        capture = read_json(capture_path)
        assert token not in capture_path.read_text(encoding="utf-8")
        assert capture["acquisition"]["status"] == "completed"
        assert capture["acquisition"]["attempted_requests"] == 4
        assert capture["acquisition"]["failure_counts"] == {}
        rows = [row for arm in ("baseline", "reflection") for row in capture[arm]["records"]]
        assert len({row["response"]["session_id"] for row in rows}) == 4
        assert compare([
            "--dataset-manifest", str(business_dataset), "--capture", str(capture_path),
            "--output", str(report_path),
        ]) == 0
        report = read_json(report_path)
        assert report["outcomes"]["improved"] == report["outcomes"]["regressed"] == 1
        assert report["capture_status"] == "completed"
        assert report["paired_latency"]["count"] == 2
        assert report["paired_llm_calls"]["delta"] == 1
    finally:
        await ConnectionFactory.aclose("billing")


async def test_single_capture_scores_http_results_and_saved_rankings(
    business_dataset, plan_env, tmp_path, monkeypatch,
):
    corpus_path = tmp_path / "retrieval.json"
    corpus = read_json(corpus_path)
    corpus["queries"][2]["domain"] = "inventory"
    corpus["documents"].append({
        "id": "other-db", "db_id": "warehouse", "domain": "inventory", "text": "Stock levels",
    })
    write_atomic(corpus_path, corpus)
    freeze(tmp_path)
    snapshot = business_dataset.parent / "snapshot.sqlite3"
    await ConnectionFactory.create(DatabaseType.SQLITE, {"path": str(snapshot)}, "billing")
    router = ConfirmedRouter([
        "SELECT sum(amount) AS total FROM invoices", "SELECT max(amount) AS total FROM invoices",
    ])
    fixture = plan_env(router)
    token = fixture.headers["Authorization"][7:]
    monkeypatch.setenv("DE_PLAN_CAPTURE_TOKEN", token)
    monkeypatch.setenv("DE_TRACE_DIR", str(tmp_path / "q2-traces"))
    capture_path, ranks_path, report_path, limits = (
        tmp_path / name for name in ("single.json", "ranks.json", "report.json", "limits.json")
    )
    dataset_args = ["--dataset-manifest", str(business_dataset), "--split", "holdout"]
    try:
        async with _serve(fixture.app) as origin:
            args = _arguments(origin, business_dataset, capture_path, max_requests=2)
            assert await asyncio.to_thread(main, [*args, "--baseline-only"]) == 0
        assert len(router.calls) == 2
    finally:
        await ConnectionFactory.aclose("billing")

    capture = read_json(capture_path)
    assert token not in capture_path.read_text(encoding="utf-8")
    assert await asyncio.to_thread(
        capture_rankings, [*dataset_args, "--output", str(ranks_path)],
    ) == 0
    write_atomic(limits, {
        "version": THRESHOLD_VERSION, "manifest_sha256": capture["baseline"]["manifest_sha256"],
        "k": 3, "metrics": {metric: {"min_score": 0.5, "min_count": 1} for metric in METRICS},
    })
    scoring = [*dataset_args, "--query-capture", str(capture_path), "--rankings", str(ranks_path),
               "--thresholds", str(limits), "--output", str(report_path)]
    assert evaluate(scoring) == 0
    report = read_json(report_path)
    assert report["query_capture"]["response_count"] == 2
    assert report["sql"]["contract_match_count"] == report["sql"]["execution_success_count"] == 2
    assert report["business_quality"]["thresholds"]["capture_complete"] is True

    wrong_rows = deepcopy(capture)
    response = wrong_rows["baseline"]["records"][0]["response"]
    response["execution"]["rows"] = [[999]]
    response["candidates"][0]["execution"]["rows"] = [[999]]
    write_atomic(capture_path, wrong_rows)
    assert evaluate(scoring) == 0  # The configured minimum allows one incorrect response.
    report = read_json(report_path)
    assert report["sql"]["error_counts"] == {"CAPTURED_RESULT_MISMATCH": 1}
    assert report["sql"]["execution_success_count"] == 2
    assert report["sql"]["contract_match_count"] == 1

    truncated = deepcopy(capture)
    response = truncated["baseline"]["records"][0]["response"]
    response["execution"]["truncated"] = True
    response["candidates"][0]["execution"]["truncated"] = True
    write_atomic(capture_path, truncated)
    assert evaluate(scoring) == 0
    report = read_json(report_path)
    assert report["sql"]["error_counts"] == {"CAPTURED_EXECUTION_INCOMPLETE": 1}
    assert report["sql"]["execution_success_count"] == report["sql"]["contract_match_count"] == 1

    stopped = deepcopy(capture)
    stopped["acquisition"].update(status="stopped", attempted_requests=1)
    stopped["baseline"]["records"].pop()
    write_atomic(capture_path, stopped)
    assert evaluate(scoring) == 1
    report = read_json(report_path)
    assert report["sql"]["case_count"] == 2
    assert report["sql"]["error_counts"] == {"MISSING_RESPONSE": 1}
    gate = report["business_quality"]["thresholds"]
    assert all(metric["passed"] for metric in gate["metrics"].values())
    assert gate["capture_complete"] is False

    before = report_path.read_bytes()
    stopped["acquisition"]["status"] = "completed"
    write_atomic(capture_path, stopped)
    assert evaluate(scoring) == 2 and report_path.read_bytes() == before
    capture["baseline"]["manifest_sha256"] = "b" * 64
    write_atomic(capture_path, capture)
    assert evaluate(scoring) == 2 and report_path.read_bytes() == before


async def test_candidate_review_pair_records_rejection_as_a_regression(
    business_dataset, plan_env, tmp_path, monkeypatch,
):
    snapshot = business_dataset.parent / "snapshot.sqlite3"
    await ConnectionFactory.create(DatabaseType.SQLITE, {"path": str(snapshot)}, "billing")
    total = "SELECT sum(amount) AS total FROM invoices"
    maximum = "SELECT max(amount) AS total FROM invoices"
    model = ConfirmedRouter([total, total, '{"approved":true}', maximum,
                             '{"approved":false}', maximum])
    fixture = plan_env(model)
    monkeypatch.setattr(settings, "candidate_review_enabled", True)
    monkeypatch.setenv("DE_PLAN_CAPTURE_TOKEN", fixture.headers["Authorization"][7:])
    monkeypatch.setenv("DE_TRACE_DIR", str(tmp_path / "review-traces"))
    capture_path, report_path = tmp_path / "paired-review.json", tmp_path / "review-report.json"
    try:
        async with _serve(fixture.app) as origin:
            args = _arguments(origin, business_dataset, capture_path)
            assert await asyncio.to_thread(main, [*args, "--trial-plan", "candidate_review"]) == 0
        assert compare([
            "--dataset-manifest", str(business_dataset), "--capture", str(capture_path),
            "--output", str(report_path),
        ]) == 0
        report = read_json(report_path)
        assert report["trial_plan"] == "candidate_review"
        assert report["accuracy"] == {"request_query": 1, "candidate_review": 0.5}
        assert report["outcomes"]["regressed"] == 1
        assert report["paired_llm_calls"]["candidate_review_total"] == 4
    finally:
        await ConnectionFactory.aclose("billing")


@pytest.mark.parametrize("failure", ["tenant", "timeout", "binding"])
async def test_bad_target_or_uncertain_request_stops_without_retry(business_dataset, failure):
    dataset = load_business_dataset(business_dataset)
    requests: list[str] = []

    def handler(request: httpx.Request):
        requests.append(request.url.path)
        if request.url.path == "/api/v1/auth/workspace":
            return httpx.Response(200, json={
                "tenant_id": 2 if failure == "tenant" else 1, "access": "system",
            })
        if request.url.path == "/api/v1/databases":
            return httpx.Response(200, json={
                "databases": [{"db_id": "billing", "db_type": "sqlite", "status": "connected"}],
            })
        if failure == "timeout":
            if requests.count("/api/v1/query") == 1:
                return httpx.Response(503)
            raise httpx.ReadTimeout("in-flight query timed out")
        submitted = json.loads(request.content)
        assert submitted["plan_id"] == "request_query"
        return httpx.Response(200, json={
            "status": "completed", "plan_id": "reflection", "nl_text": submitted["nl_text"],
            "query_id": "q_123456789abc",
        })

    # MockTransport's content is buffered. A response stream exercises the same reader as HTTP.
    class Content(httpx.AsyncByteStream):
        def __init__(self, raw):
            self.raw = raw

        async def __aiter__(self):
            yield self.raw

    def transport(request):
        response = handler(request)
        return httpx.Response(
            response.status_code, headers=response.headers, stream=Content(response.content),
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(transport), base_url="http://127.0.0.1",
    ) as client:
        bundle = await capture_query_plans(
            client, dataset, CaptureSettings(1, "holdout", "a" * 64, 4, 5),
        )
    acquisition = bundle["acquisition"]
    assert acquisition["status"] == "stopped"
    if failure == "tenant":
        assert acquisition["stop_reason"] == "TENANT_MISMATCH"
        assert acquisition["attempted_requests"] == 0
        assert requests == ["/api/v1/auth/workspace"]
        return
    if failure == "timeout":
        assert acquisition["stop_reason"] == "CLIENT_TIMEOUT"
        assert acquisition["failure_counts"] == {"HTTP_503": 1, "CLIENT_TIMEOUT": 1}
        assert acquisition["attempted_requests"] == requests.count("/api/v1/query") == 2
        assert bundle["reflection"]["records"][0]["response"] is None
    else:
        assert acquisition["stop_reason"] == "RESPONSE_BINDING_FAILED"
        assert acquisition["attempted_requests"] == requests.count("/api/v1/query") == 1
    assert bundle["baseline"]["records"][0]["response"] is None


async def test_capture_rejects_unreplayable_gold_before_any_http_request(business_dataset):
    dataset = load_business_dataset(business_dataset)
    sql = deepcopy(dataset.sql)
    sql["sql_cases"][1]["expected_rows"]["base"] = [[999]]

    def no_http(_request):
        pytest.fail("An invalid gold contract must fail before HTTP traffic")

    async with httpx.AsyncClient(transport=httpx.MockTransport(no_http)) as client:
        with pytest.raises(ValueError, match="labels disagree"):
            await capture_query_plans(
                client, replace(dataset, sql=sql), CaptureSettings(1, "holdout", "a" * 64, 4, 5),
            )
