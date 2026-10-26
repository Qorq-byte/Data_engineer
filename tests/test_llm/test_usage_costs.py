"""Flat operator prices are estimates over observed attempts, never provider bills."""

import copy
import json
import os
import subprocess
import sys
from decimal import getcontext
from pathlib import Path

import pytest

from app.api import query
from app.audit.store import UsageAuditRecord, UsageAuditStore
from app.config.settings import settings
from app.llm.usage import UsageCollector, observe
from tests.test_api import test_query_contract as contracts
from tests.test_llm.test_sdk_boundary import native_router
from tests.test_rag.test_prediction_export import build

ROOT = Path(__file__).resolve().parents[2]
connected_query = contracts.connected_query


def prices():
    return {
        "version": "flat-token-prices-v1",
        "currency": "USD",
        "label": "synthetic-fixture-only",
        "as_of": "2026-10-25",
        "prices": [
            {
                "provider": "openai",
                "model": "fixture",
                "prompt_usd_per_million": "1",
                "completion_usd_per_million": "2",
            }
        ],
    }


def audit(attempts=None, *, query_id="q_000000000001", outcome="completed", dropped=0):
    c = UsageCollector()
    if attempts is None:
        attempts = [{"prompt_tokens": 3, "completion_tokens": 2}]
    for usage in attempts:
        with observe(c, "openai", "fixture", "transport_boundary"):
            if usage is not None:
                c.observe({"usage": usage}, usage_basis="sdk_normalized")
    c.dropped = dropped
    return {
        "schema_version": 1,
        "query_id": query_id,
        "outcome": outcome,
        "llm_calls": len(attempts) + dropped,
        "usage": c.summary(),
        "session_ref": "private session",
        "sql": "SELECT private_data",
    }


def estimate(records=None, book=None):
    from app.evaluation.usage_costs import estimate_usage_costs

    return estimate_usage_costs(
        [audit()] if records is None else records, prices() if book is None else book
    )


def test_exact_decimal_and_deterministic_allowlist():
    records, book = [audit()], prices()
    before = copy.deepcopy((records, book))
    report = estimate(records, book)
    assert report == estimate(records, book)
    assert (records, book) == before
    assert report["report_version"] == "usage-cost-estimate-v1"
    assert report["billing_verified"] is False
    assert report["summary"]["estimated_total_usd"] == "0.000007"
    assert report["queries"][0]["attempts"][0]["usage_basis"] == "sdk_normalized"
    assert "private" not in str(report) and "SELECT" not in str(report)
    assert len(report["price_book"]["sha256"]) == 64
    assert len(report["queries"][0]["audit_sha256"]) == 64


def test_decimal_context_is_local_and_large_token_formula_is_exact():
    previous = getcontext().prec
    try:
        getcontext().prec = 2
        source = audit([{"prompt_tokens": 9223372036854775807, "completion_tokens": 0}])
        book = prices()
        book["prices"][0]["prompt_usd_per_million"] = "0.000000000001"
        result = estimate([source], book)
        assert result["summary"]["estimated_total_usd"] == "9.223372036854775807"
        assert getcontext().prec == 2
    finally:
        getcontext().prec = previous


@pytest.mark.parametrize("zero", ["tokens", "rates"])
def test_valid_zero_is_not_unknown(zero):
    source, book = audit(), prices()
    if zero == "tokens":
        source = audit([{"prompt_tokens": 0, "completion_tokens": 0}])
    else:
        book["prices"][0].update(prompt_usd_per_million="0", completion_usd_per_million="0")
    report = estimate([source], book)
    assert report["summary"]["estimated_total_usd"] == "0"
    assert report["summary"]["known_subtotal_usd"] == "0"
    assert report["summary"]["priced_attempt_count"] == 1


@pytest.mark.parametrize(
    "missing", ["usage", "prompt", "completion", "price", "prompt_rate", "completion_rate"]
)
def test_missing_observations_or_prices_never_become_zero(missing):
    source, book = audit(), prices()
    if missing == "usage":
        source = audit([None])
    elif missing == "prompt":
        source = audit([{"completion_tokens": 2}])
    elif missing == "completion":
        source = audit([{"prompt_tokens": 3}])
    elif missing == "price":
        book["prices"] = []
    else:
        book["prices"][0][
            "prompt_usd_per_million" if missing == "prompt_rate" else "completion_usd_per_million"
        ] = None
    report = estimate([source], book)
    assert report["summary"]["estimated_total_usd"] is None
    assert report["summary"]["known_subtotal_usd"] is None
    assert report["queries"][0]["attempts"][0]["unavailable_reasons"]


def test_partial_retry_sum_is_not_total_and_failed_attempts_are_not_refunded():
    source = audit(
        [
            {"prompt_tokens": 3, "completion_tokens": 2},
            {"prompt_tokens": 5, "completion_tokens": 1},
            None,
        ]
    )
    source["usage"]["attempts"][0]["status"] = "failed"
    source["usage"]["attempts"][1]["status"] = "cancelled"
    report = estimate([source])
    assert report["summary"]["known_subtotal_usd"] == "0.000014"
    assert report["summary"]["estimated_total_usd"] is None
    assert report["summary"]["priced_attempt_count"] == 2
    assert report["summary"]["priced_attempt_coverage"] == pytest.approx(2 / 3)


@pytest.mark.parametrize("condition", ["dropped", "pending", "accounting", "empty", "no_usage"])
def test_incomplete_query_never_gets_full_total(condition):
    source = audit()
    if condition == "dropped":
        source = audit(dropped=1)
    elif condition == "pending":
        source["usage"]["attempts"][0]["status"] = "pending"
        source["usage"]["complete"] = False
    elif condition == "accounting":
        source["llm_calls"] += 1
    elif condition == "empty":
        source = audit([])
    else:
        source["usage"] = None
    result = estimate([source])
    assert result["summary"]["estimated_total_usd"] is None
    assert result["queries"][0]["unavailable_reasons"]


@pytest.mark.parametrize(
    "field,value",
    [
        ("provider", "redacted"),
        ("model", "unknown"),
        ("provider", "mock"),
        ("source", "adapter_boundary"),
        ("usage_basis", "unknown"),
    ],
)
def test_uncertain_identity_or_source_is_not_priced(field, value):
    source = audit()
    source["usage"]["attempts"][0][field] = value
    report = estimate([source])
    assert report["summary"]["estimated_total_usd"] is None
    assert report["queries"][0]["attempts"][0]["unavailable_reasons"]


def test_token_out_of_range_is_unknown_not_rounded_or_overflowed():
    source = audit([{"prompt_tokens": 2**63, "completion_tokens": 0}])
    assert estimate([source])["summary"]["estimated_total_usd"] is None


@pytest.mark.parametrize(
    "invalid",
    [
        "duplicate",
        "negative",
        "float",
        "bool",
        "nan",
        "exponent",
        "precision",
        "currency",
        "version",
        "extra",
        "date",
    ],
)
def test_pricebook_rejects_unsupported_or_ambiguous_inputs(invalid):
    book = prices()
    if invalid == "duplicate":
        book["prices"] *= 2
    elif invalid in ("negative", "float", "bool", "nan", "exponent", "precision"):
        book["prices"][0]["prompt_usd_per_million"] = {
            "negative": "-1",
            "float": 1.2,
            "bool": True,
            "nan": "NaN",
            "exponent": "1e-3",
            "precision": "0.0000000000001",
        }[invalid]
    elif invalid == "currency":
        book["currency"] = "CNY"
    elif invalid == "version":
        book["version"] = "future"
    elif invalid == "extra":
        book["prices"][0]["cached_rate"] = "0.1"
    else:
        book["as_of"] = "2026-02-30"
    with pytest.raises(ValueError):
        estimate(book=book)


@pytest.mark.parametrize(
    "invalid", ["duplicate", "version", "usage", "attempt_id", "empty", "bound_version"]
)
def test_records_reject_duplicates_and_corrupt_audit_metadata(invalid):
    source = audit()
    records = [source]
    if invalid == "duplicate":
        records *= 2
    elif invalid == "version":
        source["schema_version"] = 3
    elif invalid == "usage":
        source["usage"]["known_prompt_tokens"] = 99
    elif invalid == "attempt_id":
        source["usage"]["attempts"][0]["attempt"] = 2
    elif invalid == "empty":
        records = []
    else:
        source["schema_version"] = 2
        source["candidate_digest_version"] = "future"
        source["candidate_digests"] = []
    with pytest.raises(ValueError):
        estimate(records)


def test_batch_counts_each_query_once_and_accepts_audit_v2():
    first, second = audit(), audit(query_id="q_000000000002", outcome="failed")
    second.update(
        schema_version=2, candidate_digest_version="candidate-binding-v1", candidate_digests=[]
    )
    report = estimate([first, second])
    assert report["summary"]["query_count"] == 2
    assert report["summary"]["estimated_total_usd"] == "0.000014"


def test_cli_reads_audit_read_only_and_preserves_report_on_failure(tmp_path):
    path, output, book = tmp_path / "audit.db", tmp_path / "costs.json", tmp_path / "prices.json"
    source = audit()
    UsageAuditStore(str(path)).write(
        UsageAuditRecord(
            query_id=source["query_id"],
            session_id="private",
            outcome=source["outcome"],
            llm_calls=source["llm_calls"],
            usage=source["usage"],
            candidate_digests=[],
        )
    )
    book.write_text(json.dumps(prices()), encoding="utf-8")
    before = path.read_bytes()
    argv = [
        sys.executable,
        "-m",
        "scripts.estimate_usage_cost",
        "--audit-db",
        str(path),
        "--query-id",
        source["query_id"],
        "--prices",
        str(book),
        "--output",
        str(output),
    ]
    run = subprocess.run(argv, cwd=ROOT, capture_output=True, text=True, timeout=20)
    assert run.returncode == 0, run.stderr
    assert json.loads(output.read_text())["summary"]["estimated_total_usd"] == "0.000007"
    assert path.read_bytes() == before
    original = output.read_bytes()
    failed = subprocess.run(
        argv + ["--query-id", source["query_id"]],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert failed.returncode == 2 and output.read_bytes() == original
    missing = list(argv)
    missing[missing.index(source["query_id"])] = "q_000000000099"
    assert subprocess.run(missing, cwd=ROOT, capture_output=True, timeout=20).returncode == 2
    assert output.read_bytes() == original
    protected = list(argv)
    protected[protected.index(str(output))] = str(book)
    assert subprocess.run(protected, cwd=ROOT, capture_output=True, timeout=20).returncode == 2
    assert json.loads(book.read_text()) == prices()


def test_fresh_cost_import_has_no_model_database_or_network_clients():
    code = """
import importlib.abc
import sys
class Guard(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in {'litellm', 'mysql', 'httpx', 'requests'}:
            raise RuntimeError('forbidden dependency')
sys.meta_path.insert(0, Guard())
import scripts.estimate_usage_cost
"""
    environment = dict(os.environ)
    environment.pop("LITELLM_LOCAL_MODEL_COST_MAP", None)
    run = subprocess.run(
        [sys.executable, "-c", code],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert run.returncode == 0, run.stderr


@pytest.mark.parametrize("bound", [False, True])
async def test_real_sdk_multiple_candidates_are_estimated_once_per_query(
    connected_query,
    monkeypatch,
    tmp_path,
    bound,
):
    import litellm

    path = tmp_path / "audit.sqlite3"
    monkeypatch.setattr(settings, "llm_audit_path", str(path))
    monkeypatch.setattr(settings, "llm_audit_candidate_digests", bound)
    calls = []

    async def transport(**kwargs):
        calls.append(kwargs)
        return litellm.ModelResponse(
            model="fixture",
            usage={"prompt_tokens": 3, "completion_tokens": 2},
            choices=[
                {
                    "finish_reason": "stop",
                    "message": {
                        "role": "assistant",
                        "content": "SELECT 1",
                    },
                }
            ],
        )

    monkeypatch.setattr(litellm, "acompletion", transport)
    monkeypatch.setattr(query, "get_default_router", lambda: native_router(max_retries=0))
    source = connected_query.post(
        "/api/v1/query",
        json={
            "nl_text": "find all orders",
            "database": "contract",
            "num_candidates": 2,
        },
    ).json()
    assert source["status"] == "completed" and len(source["candidates"]) == 2
    assert len(calls) == source["llm_calls"] == source["llm_usage"]["attempt_count"] == 2
    output = tmp_path / "estimated.json"
    run = subprocess.run(
        [
            sys.executable,
            "-m",
            "scripts.estimate_usage_cost",
            "--audit-db",
            str(path),
            "--query-id",
            source["query_id"],
            "--prices",
            str(ROOT / "tests/fixtures/flat_token_prices.json"),
            "--output",
            str(output),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert run.returncode == 0, run.stderr
    report = json.loads(output.read_text())
    assert report["summary"]["query_count"] == 1
    assert report["summary"]["priced_attempt_count"] == 2
    assert report["summary"]["estimated_total_usd"] == "0.000014"
    assert "SELECT" not in str(report) and "find all orders" not in str(report)
    exported = build(
        responses=[source],
        bindings=[
            {
                "id": "one",
                "query_id": source["query_id"],
                "candidate_id": source["candidates"][0]["id"],
                "db_id": "contract",
            }
        ],
        audit_lookup=UsageAuditStore(str(path)).read,
    )
    assert exported[0]["cost_usd"] is None


@pytest.mark.parametrize("failure", ["replace", "hardlink", "sidecar"])
def test_cli_output_failures_preserve_inputs_and_previous_artifact(
    tmp_path, monkeypatch, capsys, failure
):
    from app.evaluation import artifacts
    from scripts import estimate_usage_cost as module

    path, book = tmp_path / "audit.db", tmp_path / "prices.json"
    source = audit()
    UsageAuditStore(str(path)).write(
        UsageAuditRecord(
            query_id=source["query_id"],
            session_id="private",
            outcome="completed",
            llm_calls=source["llm_calls"],
            usage=source["usage"],
        )
    )
    book.write_text(json.dumps(prices()), encoding="utf-8")
    output = tmp_path / "report.json"
    if failure == "hardlink":
        os.link(book, output)
    elif failure == "sidecar":
        output = Path(str(path) + "-wal")
        output.write_bytes(b"previous sidecar")
    else:
        output.write_bytes(b"previous report")

        def denied(*args):
            raise PermissionError("private filesystem failure")

        monkeypatch.setattr(artifacts.os, "replace", denied)
    before = output.read_bytes()
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "estimate_usage_cost",
            "--audit-db",
            str(path),
            "--query-id",
            source["query_id"],
            "--prices",
            str(book),
            "--output",
            str(output),
        ],
    )
    with pytest.raises(SystemExit) as failed:
        module.main()
    assert failed.value.code == 2 and output.read_bytes() == before
    assert json.loads(book.read_text()) == prices()
    assert not list(tmp_path.glob(".usage-cost-estimate-*.tmp"))
    assert "private filesystem" not in capsys.readouterr().err


def test_model_aliases_are_not_guessed():
    book = prices()
    book["prices"][0]["model"] = "fixture-alias"
    assert estimate(book=book)["summary"]["estimated_total_usd"] is None


def test_zero_tokens_with_unknown_price_stays_unknown():
    source = audit([{"prompt_tokens": 0, "completion_tokens": 0}])
    book = prices()
    book["prices"] = []
    report = estimate([source], book)
    assert report["summary"]["estimated_total_usd"] is None
    assert report["summary"]["known_subtotal_usd"] is None


def test_partial_batch_has_only_known_subtotal():
    first = audit()
    second = audit([None], query_id="q_000000000002", outcome="cancelled")
    report = estimate([first, second])
    assert report["summary"]["query_count"] == 2
    assert report["summary"]["known_subtotal_usd"] == "0.000007"
    assert report["summary"]["estimated_total_usd"] is None
    assert report["summary"]["priced_attempt_coverage"] == 0.5
