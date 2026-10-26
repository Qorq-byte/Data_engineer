"""Explicit operator bindings; real file/audit/SQLite evaluator round trips."""

import copy
import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from app.api import query
from app.config.settings import settings
from scripts.evaluate_quality import evaluate, evaluate_sql
from tests.test_api import test_query_contract as contracts
from tests.test_llm.test_sdk_boundary import native_router

connected_query = contracts.connected_query
ROOT = Path(__file__).resolve().parents[2]


def dataset():
    return {
        "version": "fixture-v1",
        "sql_cases": [
            {"id": "one", "gold_sql": "SELECT 1"},
            {"id": "two", "gold_sql": "SELECT 2"},
        ],
    }


def usage():
    from app.llm.usage import UsageCollector, observe

    c = UsageCollector()
    with observe(c, "openai", "fixture", "transport_boundary"):
        c.observe(
            {
                "usage": {"prompt_tokens": 5, "completion_tokens": 0},
                "choices": [{"finish_reason": "stop"}],
            },
            usage_basis="sdk_normalized",
        )
    return c.summary()


def response():
    return {
        "query_id": "q_000000000001",
        "status": "completed",
        "nl_text": "private prompt",
        "llm_calls": 1,
        "llm_usage": usage(),
        "errors": ["private error"],
        "candidates": [
            {
                "id": "a",
                "db_id": "db1",
                "dialect": "sqlite",
                "sql_text": "SELECT 0",
                "latency_ms": 123,
            },
            {
                "id": "b",
                "db_id": "db2",
                "dialect": "sqlite",
                "sql_text": "SELECT 1",
                "latency_ms": 456,
            },
        ],
    }


def binding():
    return {"id": "one", "query_id": "q_000000000001", "candidate_id": "b", "db_id": "db2"}


def build(data=None, responses=None, bindings=None, **kwargs):
    from app.evaluation.predictions import export_predictions

    return export_predictions(
        dataset() if data is None else data,
        [response()] if responses is None else responses,
        [binding()] if bindings is None else bindings,
        **kwargs,
    )


def test_explicit_selection_unknown_telemetry_and_whitelist():
    rows = build()
    assert len(rows) == 1
    row = rows[0]
    assert row["sql"] == "SELECT 1"
    assert row["finish_reason"] is None
    assert row["latency_ms"] is row["cost_usd"] is None
    assert row["provenance"]["llm_usage"]["known_prompt_tokens"] == 5
    assert row["provenance"]["completion_source"] == "unavailable"
    assert row["provenance"]["audit_match"] == "not_requested"
    assert "private" not in str(rows)
    report = evaluate_sql(dataset()["sql_cases"], rows)
    assert report["case_count"] == 2 and report["result_correct_count"] == 1
    assert report["error_counts"] == {"MISSING_PREDICTION": 1}
    assert report["completion_counts"]["legacy_unknown"] == 1
    assert report["reported_cost_usd"]["total"] is None
    assert report["reported_generation_latency_ms"]["sample_count"] == 0


@pytest.mark.parametrize(
    "change", ["case", "query", "candidate", "db", "dialect", "duplicate_case", "duplicate_query"]
)
def test_bad_or_ambiguous_bindings_rejected(change):
    b, r = binding(), response()
    bindings = [b]
    if change == "case":
        b["id"] = "unknown"
    elif change == "query":
        b["query_id"] = "q_000000000999"
    elif change == "candidate":
        b["candidate_id"] = "unknown"
    elif change == "db":
        b["db_id"] = "wrong"
    elif change == "dialect":
        r["candidates"][1]["dialect"] = "postgresql"
    elif change == "duplicate_case":
        bindings.append(dict(b))
    elif change == "duplicate_query":
        bindings.append({**b, "id": "two"})
    with pytest.raises(ValueError):
        build(responses=[r], bindings=bindings)


def test_failed_request_and_missing_case_stay_in_denominator():
    r = response()
    r["status"] = "failed"
    rows = build(responses=[r])
    assert rows[0]["status"] == "failed"
    assert not rows[0].get("sql")
    report = evaluate_sql(dataset()["sql_cases"], rows)
    assert report["error_counts"] == {"GENERATION_FAILED": 1, "MISSING_PREDICTION": 1}
    assert report["execution_success_count"] == 0


@pytest.mark.parametrize(
    "field,value,expected",
    [
        ("finish_reason", "length", "INCOMPLETE_COMPLETION"),
        ("finish_reason", "private reason", "INCOMPLETE_COMPLETION"),
        ("refusal", "private refusal", "INCOMPLETE_COMPLETION"),
        ("tool_calls", [{"private": "args"}], "INCOMPLETE_COMPLETION"),
        ("finish_reason", "stop", None),
    ],
)
def test_only_selected_candidate_completion_fields_used(field, value, expected):
    r = response()
    r["candidates"][1][field] = value
    rows = build(responses=[r])
    assert rows[0]["provenance"]["completion_source"] == "candidate_fields"
    assert "private" not in str(rows)
    report = evaluate_sql(dataset()["sql_cases"], rows)
    assert report["details"][0]["error_code"] == expected


def test_request_sibling_finish_does_not_certify_selected_candidate():
    r = response()
    r["llm_usage"]["attempts"][0]["finish_status"] = "length"
    r["llm_usage"]["attempts"][0]["status"] = "incomplete"
    rows = build(responses=[r])
    assert rows[0]["finish_reason"] is None
    assert rows[0]["provenance"]["llm_usage"]["attempt_count"] == 1


def test_missing_usage_is_unknown_not_zero():
    r = response()
    del r["llm_usage"]
    del r["llm_calls"]
    rows = build(responses=[r])
    assert rows[0]["provenance"]["llm_usage"] is None
    assert rows[0]["provenance"]["llm_calls"] is None


@pytest.mark.parametrize("change", ["sql", "status", "provenance", "cases"])
def test_evaluator_rejects_changed_export_or_case_set(change):
    rows = build()
    cases = dataset()["sql_cases"]
    if change == "sql":
        rows[0]["sql"] = "SELECT 2"
    elif change == "status":
        rows[0]["status"] = "failed"
    elif change == "provenance":
        rows[0]["provenance"]["llm_calls"] = 99
    else:
        cases[0]["gold_sql"] = "SELECT 2"
    with pytest.raises(ValueError):
        evaluate_sql(cases, rows)


def test_audit_metadata_match_is_read_only_and_not_sql_authentication(tmp_path):
    from app.audit.store import UsageAuditRecord, UsageAuditStore

    path = tmp_path / "audit.db"
    s = UsageAuditStore(str(path), 10)
    r = response()
    s.write(
        UsageAuditRecord(
            query_id=r["query_id"],
            session_id="private",
            outcome=r["status"],
            llm_calls=r["llm_calls"],
            usage=r["llm_usage"],
        )
    )
    before = path.read_bytes()
    rows = build(responses=[r], audit_lookup=s.read)
    assert rows[0]["provenance"]["audit_match"] == "metadata_match"
    assert rows[0]["provenance"]["sql_binding"] == "caller_supplied_not_verified_by_audit"
    assert path.read_bytes() == before
    r["candidates"][1]["sql_text"] = "SELECT 99"
    # Audit v1 has no SQL digest, so it cannot detect SQL edits in caller-supplied files.
    assert build(responses=[r], audit_lookup=s.read)[0]["sql"] == "SELECT 99"


@pytest.mark.parametrize("change", ["missing", "outcome", "usage", "calls"])
def test_requested_audit_missing_or_mismatching_rejected(tmp_path, change):
    from app.audit.store import UsageAuditRecord, UsageAuditStore

    s = UsageAuditStore(str(tmp_path / "audit.db"), 10)
    r = response()
    if change != "missing":
        s.write(
            UsageAuditRecord(
                query_id=r["query_id"],
                session_id="fixture",
                outcome=r["status"],
                llm_calls=r["llm_calls"],
                usage=r["llm_usage"],
            )
        )
    if change == "outcome":
        r["status"] = "failed"
    elif change == "usage":
        r["llm_usage"]["attempts"][0]["completion_tokens"] = 10
        r["llm_usage"]["known_completion_tokens"] = 10
    elif change == "calls":
        r["llm_calls"] = 2
    with pytest.raises(ValueError):
        build(responses=[r], audit_lookup=s.read)


def test_question_binding_exact_when_available():
    data = dataset()
    data["sql_cases"][0]["question"] = "private prompt"
    assert build(data=data)
    data["sql_cases"][0]["question"] = "different"
    with pytest.raises(ValueError):
        build(data=data)


def test_export_does_not_execute_sql(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("SQL execution attempted")

    monkeypatch.setattr(sqlite3, "connect", forbidden)
    assert build()[0]["sql"] == "SELECT 1"


def test_legacy_predictions_remain_accepted():
    report = evaluate_sql(dataset()["sql_cases"], [{"id": "one", "sql": "SELECT 1"}])
    assert report["result_correct_count"] == 1


def test_cli_atomic_output_and_input_protection(tmp_path):
    data, responses, bindings, output = [
        tmp_path / n for n in ("data.json", "responses.json", "bindings.json", "out.json")
    ]
    for path, value in [(data, dataset()), (responses, [response()]), (bindings, [binding()])]:
        path.write_text(json.dumps(value), encoding="utf-8")
    original_responses = responses.read_bytes()
    command = [
        sys.executable,
        "-m",
        "scripts.export_predictions",
        "--dataset",
        str(data),
        "--responses",
        str(responses),
        "--bindings",
        str(bindings),
        "--output",
        str(output),
    ]
    run = subprocess.run(command, capture_output=True, text=True, timeout=30)
    assert run.returncode == 0, run.stderr
    assert "SELECT" not in run.stdout and "private" not in run.stdout
    previous = output.read_bytes()
    bindings.write_text(json.dumps([{**binding(), "candidate_id": "missing"}]), encoding="utf-8")
    run = subprocess.run(command, capture_output=True, text=True, timeout=30)
    assert run.returncode != 0
    assert output.read_bytes() == previous
    assert "private" not in run.stderr
    bindings.write_text(json.dumps([binding()]), encoding="utf-8")
    run = subprocess.run(command[:-1] + [str(responses)], capture_output=True, timeout=30)
    assert run.returncode != 0
    assert responses.read_bytes() == original_responses
    assert not list(tmp_path.glob(".prediction-export-*.tmp"))


async def test_real_query_audit_export_and_sqlite_evaluation(
    connected_query, tmp_path, monkeypatch
):
    import litellm

    from app.audit.store import UsageAuditStore

    path = tmp_path / "audit.db"
    monkeypatch.setattr(settings, "llm_audit_path", str(path))

    async def transport(**options):
        return litellm.ModelResponse(
            model="fixture",
            usage={"prompt_tokens": 4, "completion_tokens": 2},
            choices=[
                {
                    "finish_reason": "stop",
                    "message": {
                        "role": "assistant",
                        "content": "SELECT id FROM orders ORDER BY id",
                    },
                }
            ],
        )

    monkeypatch.setattr(litellm, "acompletion", transport)
    monkeypatch.setattr(query, "get_default_router", lambda: native_router(max_retries=0))
    data = json.loads((ROOT / "tests/fixtures/retrieval_eval.json").read_text(encoding="utf-8"))
    question = data["sql_cases"][0]["question"]
    r = connected_query.post(
        "/api/v1/query",
        json={"nl_text": question, "database": "contract", "execute": True, "num_candidates": 1},
    ).json()
    assert r["llm_audit"]["status"] == "persisted"
    assert r["candidates"][0]["generation_source"] == "model_response"
    assert r["candidates"][0]["finish_reason"] == "stop"
    rows = build(
        data=data,
        responses=[r],
        bindings=[
            {
                "id": "sql1",
                "query_id": r["query_id"],
                "candidate_id": r["candidates"][0]["id"],
                "db_id": "contract",
            }
        ],
        audit_lookup=UsageAuditStore(str(path)).read,
    )
    report = evaluate(data, 3, rows)
    sql = report["sql_model_metrics"]
    assert sql["case_count"] == 4 and sql["prediction_count"] == 1
    assert sql["result_correct_count"] == 1
    assert rows[0]["finish_reason"] == "stop"
    assert rows[0]["provenance"]["completion_source"] == "candidate_fields"
    assert sql["completion_counts"]["stop"] == 1
    assert sql["reported_cost_usd"]["total"] is None
    assert rows[0]["provenance"]["llm_usage"]["known_prompt_tokens"] == 4


@pytest.mark.parametrize(
    "mode",
    ["missing_candidate", "ambiguous_candidate", "running", "invalid_calls", "invalid_usage"],
)
def test_source_shape_failures(mode):
    r, b = response(), binding()
    if mode == "missing_candidate":
        del b["candidate_id"]
    elif mode == "ambiguous_candidate":
        r["candidates"].append(dict(r["candidates"][1]))
    elif mode == "running":
        r["status"] = "running"
    elif mode == "invalid_calls":
        r["llm_calls"] = True
    else:
        r["llm_usage"]["known_prompt_tokens"] = 999
    with pytest.raises(ValueError):
        build(responses=[r], bindings=[b])


def test_failed_without_candidate_does_not_invent_sql():
    r, b = response(), binding()
    r["status"], r["candidates"] = "failed", []
    del b["candidate_id"]
    rows = build(responses=[r], bindings=[b])
    assert rows[0]["sql"] is None
    assert rows[0]["provenance"]["candidate_id"] is None


@pytest.mark.parametrize("mode", ["remove_version", "unknown_version", "duplicate_query"])
def test_consumer_rejects_malformed_export_headers(mode):
    rows = build()
    if mode == "remove_version":
        del rows[0]["export_version"]
    elif mode == "unknown_version":
        rows[0]["export_version"] = "future-export"
    else:
        from app.evaluation.predictions import fingerprint

        second = copy.deepcopy(rows[0])
        second["id"] = "two"
        second["export_sha256"] = fingerprint(
            {k: v for k, v in second.items() if k != "export_sha256"}
        )
        rows.append(second)
    with pytest.raises(ValueError):
        evaluate_sql(dataset()["sql_cases"], rows)


def test_full_dataset_mismatch_rejected_before_bm25(monkeypatch):
    from scripts import evaluate_quality as module

    data = {**dataset(), "documents": [], "queries": []}
    rows = build(data=data)
    changed = {**data, "version": "different"}

    def forbidden():
        raise AssertionError("BM25 started before validating provenance")

    monkeypatch.setattr(module, "BM25Index", forbidden)
    with pytest.raises(ValueError, match="dataset mismatch"):
        module.evaluate(changed, 3, rows)


def test_exported_write_sql_still_hits_read_only_evaluator_policy():
    r = response()
    r["candidates"][1]["sql_text"] = "DELETE FROM orders"
    rows = build(responses=[r])
    report = evaluate_sql(dataset()["sql_cases"], rows)
    assert report["details"][0]["error_code"] == "SQL_POLICY_REJECTED"
    assert report["execution_success_count"] == 0


def test_sources_unchanged_and_hashes_ignore_dictionary_key_order():
    from app.evaluation.predictions import export_predictions

    data, rs, bs = dataset(), [response()], [binding()]
    before = copy.deepcopy((data, rs, bs))
    first = export_predictions(data, rs, bs)
    second = export_predictions(
        dict(reversed(list(data.items()))), [dict(reversed(list(rs[0].items())))], bs
    )
    assert first == second
    assert (data, rs, bs) == before


@pytest.mark.parametrize(
    "raw", ['{"id":1,"id":2}', '{"x":NaN}', '{"x":Infinity}', '{"x":-Infinity}', '{"x":1e999}']
)
def test_file_reader_rejects_duplicate_keys_and_nonfinite(tmp_path, raw):
    from scripts.export_predictions import read_json

    path = tmp_path / "bad.json"
    path.write_text(raw, encoding="utf-8")
    with pytest.raises(ValueError):
        read_json(path)


def test_file_size_limit(tmp_path, monkeypatch):
    from scripts import export_predictions as module

    monkeypatch.setattr(module, "MAX_INPUT_BYTES", 64)
    path = tmp_path / "large.json"
    path.write_bytes(b" " * 65)
    with pytest.raises(ValueError):
        module.read_json(path)


def test_atomic_replace_failure_preserves_output_and_cleans_temp(tmp_path, monkeypatch, capsys):
    from scripts import export_predictions as module

    files = {
        name: tmp_path / f"{name}.json" for name in ("dataset", "responses", "bindings", "output")
    }
    for name, value in [
        ("dataset", dataset()),
        ("responses", [response()]),
        ("bindings", [binding()]),
    ]:
        files[name].write_text(json.dumps(value), encoding="utf-8")
    files["output"].write_bytes(b"old report")
    argv = ["export_predictions"]
    for name, path in files.items():
        argv.extend([f"--{name}", str(path)])
    monkeypatch.setattr(sys, "argv", argv)

    def denied(*args):
        raise PermissionError("private path")

    monkeypatch.setattr(module.os, "replace", denied)
    with pytest.raises(SystemExit) as failure:
        module.main()
    assert failure.value.code == 2
    assert files["output"].read_bytes() == b"old report"
    assert not list(tmp_path.glob(".prediction-export-*.tmp"))
    assert "private path" not in capsys.readouterr().err


def test_output_hardlink_to_input_is_rejected(tmp_path):
    import os

    from scripts.export_predictions import _protect_inputs

    source, output = tmp_path / "source.json", tmp_path / "out.json"
    source.write_bytes(b"input")
    os.link(source, output)
    with pytest.raises(ValueError):
        _protect_inputs(output, [source])
    assert source.read_bytes() == b"input"


def test_fresh_export_import_has_no_sdk_or_mysql_dependency():
    import os

    environment = dict(os.environ)
    environment.pop("LITELLM_LOCAL_MODEL_COST_MAP", None)
    code = """
import importlib.abc
import sys
class Guard(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split(".")[0] in ("litellm", "aiomysql"):
            raise AssertionError("offline export imported an external driver")
sys.meta_path.insert(0, Guard())
from scripts.export_predictions import read_json
from app.evaluation.predictions import export_predictions
assert "litellm" not in sys.modules
"""
    subprocess.run([sys.executable, "-c", code], env=environment, check=True, timeout=20)


def test_sql_limit_counts_utf8_bytes():
    r = response()
    r["candidates"][1]["sql_text"] = "选" * 400000
    with pytest.raises(ValueError, match="byte limit"):
        build(responses=[r])


def test_cli_export_then_existing_evaluator_cli(tmp_path):
    data = json.loads((ROOT / "tests/fixtures/retrieval_eval.json").read_text(encoding="utf-8"))
    r = response()
    r["nl_text"] = data["sql_cases"][0]["question"]
    r["candidates"][1]["sql_text"] = "SELECT id FROM orders ORDER BY id"
    b = {**binding(), "id": "sql1"}
    paths = {
        name: tmp_path / f"{name}.json"
        for name in ("dataset", "responses", "bindings", "predictions", "result")
    }
    for name, value in [("dataset", data), ("responses", [r]), ("bindings", [b])]:
        paths[name].write_text(json.dumps(value), encoding="utf-8")
    subprocess.run(
        [
            sys.executable,
            "-m",
            "scripts.export_predictions",
            "--dataset",
            str(paths["dataset"]),
            "--responses",
            str(paths["responses"]),
            "--bindings",
            str(paths["bindings"]),
            "--output",
            str(paths["predictions"]),
        ],
        check=True,
        capture_output=True,
        timeout=30,
    )
    subprocess.run(
        [
            sys.executable,
            "-m",
            "scripts.evaluate_quality",
            "--dataset",
            str(paths["dataset"]),
            "--predictions",
            str(paths["predictions"]),
            "--output",
            str(paths["result"]),
        ],
        check=True,
        capture_output=True,
        timeout=30,
    )
    result = json.loads(paths["result"].read_text(encoding="utf-8"))["sql_model_metrics"]
    assert result["case_count"] == 4 and result["result_correct_count"] == 1
    assert result["prediction_count"] == 1
    assert result["completion_counts"]["legacy_unknown"] == 1
    assert result["reported_cost_usd"]["total"] is None


def test_excessive_json_nesting_is_a_safe_input_error(tmp_path):
    from scripts.export_predictions import read_json
    path = tmp_path / "deep.json"
    path.write_text("[" * 2000 + "0" + "]" * 2000, encoding="utf-8")
    with pytest.raises(ValueError, match="nesting limit"):
        read_json(path)
