"""Offline evaluation must not overwrite inputs or begin work on malformed artifacts."""

import importlib
import json
import os
import sqlite3
import sys
from pathlib import Path

import pytest

from app.evaluation import artifacts

ROOT = Path(__file__).resolve().parents[2]
KINDS = ("quality", "schema_linking", "sql_validation")
FILES = {
    "quality": "retrieval_eval.json",
    "schema_linking": "schema_linking_eval.json",
    "sql_validation": "sql_validation_eval.json",
}


def setup_cli(kind, tmp_path, monkeypatch):
    module = importlib.import_module(f"scripts.evaluate_{kind}")
    root = tmp_path / "repo"
    directory = root / "tests/fixtures"
    directory.mkdir(parents=True)
    source = directory / FILES[kind]
    source.write_bytes((ROOT / "tests/fixtures" / FILES[kind]).read_bytes())
    (directory / "test_schema.sql").write_bytes(
        (ROOT / "tests/fixtures/test_schema.sql").read_bytes()
    )
    monkeypatch.setattr(module, "ROOT", root)
    output = tmp_path / "report.json"
    output.write_bytes(b"previous report")
    return module, source, output


def invoke(module, monkeypatch, output, *extra):
    monkeypatch.setattr(sys, "argv", ["evaluation", "--output", str(output), *map(str, extra)])
    try:
        module.main()
    except SystemExit as exc:
        return exc.code
    return 0


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("alias", ["same", "hardlink"])
def test_output_must_not_replace_dataset(kind, alias, tmp_path, monkeypatch):
    module, source, output = setup_cli(kind, tmp_path, monkeypatch)
    if alias == "same":
        output = source
    else:
        output.unlink()
        os.link(source, output)
    before = source.read_bytes()
    assert invoke(module, monkeypatch, output) == 2
    assert source.read_bytes() == before and output.read_bytes() == before


@pytest.mark.parametrize("kind", KINDS)
def test_atomic_replace_failure_preserves_report(kind, tmp_path, monkeypatch, capsys):
    module, source, output = setup_cli(kind, tmp_path, monkeypatch)
    before = source.read_bytes()

    def denied(*args):
        raise PermissionError("private storage location")

    monkeypatch.setattr(artifacts.os, "replace", denied)
    assert invoke(module, monkeypatch, output) == 2
    assert output.read_bytes() == b"previous report" and source.read_bytes() == before
    assert not list(tmp_path.glob(".evaluation-report-*.tmp"))
    assert "private storage location" not in capsys.readouterr().err


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("invalid", ["duplicate", "nonfinite", "deep", "large", "shape"])
def test_invalid_dataset_is_fixed_error_and_preserves_old_report(
    kind,
    invalid,
    tmp_path,
    monkeypatch,
    capsys,
):
    module, source, output = setup_cli(kind, tmp_path, monkeypatch)
    raw = source.read_text(encoding="utf-8").rstrip()
    if invalid == "duplicate":
        raw = raw[:-1] + ',"version":"private replacement"}'
    elif invalid == "nonfinite":
        raw = raw[:-1] + ',"private":NaN}'
    elif invalid == "deep":
        raw = raw[:-1] + ',"extra":' + "[" * 65 + "0" + "]" * 65 + "}"
    elif invalid == "large":
        raw += " " * (16 * 1024 * 1024 + 1)
    else:
        raw = '{"version":"private shape"}'
    source.write_text(raw, encoding="utf-8")
    before = source.read_bytes()
    assert invoke(module, monkeypatch, output) == 2
    assert output.read_bytes() == b"previous report" and source.read_bytes() == before
    captured = capsys.readouterr()
    assert "private" not in captured.err and "Traceback" not in captured.err


@pytest.mark.parametrize("kind", KINDS)
def test_bom_default_smoke_and_stdout_summary(kind, tmp_path, monkeypatch, capsys):
    module, source, output = setup_cli(kind, tmp_path, monkeypatch)
    source.write_bytes(b"\xef\xbb\xbf" + source.read_bytes())
    assert invoke(module, monkeypatch, output) == 0
    report = json.loads(output.read_text(encoding="utf-8"))
    summary = json.loads(capsys.readouterr().out)
    assert len(summary["report_sha256"]) == 64 and "details" not in summary
    assert summary["dataset_sha256"] == report["dataset_sha256"]
    if kind == "quality":
        assert report["query_count"] == 7 and report["sql_model_metrics"] is None
    else:
        assert report["passed"] == report["case_count"] > 0


@pytest.mark.parametrize("invalid", ["documents", "queries", "empty", "domain", "k", "telemetry"])
def test_quality_preflight_happens_before_bm25(invalid, monkeypatch):
    from scripts import evaluate_quality as module

    dataset = json.loads((ROOT / "tests/fixtures/retrieval_eval.json").read_text(encoding="utf-8"))
    k, predictions = 3, None
    if invalid == "documents":
        dataset["documents"].append(dict(dataset["documents"][0]))
    elif invalid == "queries":
        dataset["queries"].append(dict(dataset["queries"][0]))
    elif invalid == "empty":
        dataset["queries"] = []
    elif invalid == "domain":
        dataset["queries"][0]["domain"] = []
    elif invalid == "k":
        k = True
    else:
        predictions = [{"id": "sql1", "cost_usd": -1}]

    def forbidden():
        raise AssertionError("BM25 started before validation")

    monkeypatch.setattr(module, "BM25Index", forbidden)
    with pytest.raises(ValueError):
        module.evaluate(dataset, k, predictions)


@pytest.mark.parametrize("invalid", ["id", "flag", "columns", "hit"])
async def test_schema_preflight_happens_before_node(invalid, monkeypatch):
    from scripts import evaluate_schema_linking as module

    data = json.loads(
        (ROOT / "tests/fixtures/schema_linking_eval.json").read_text(encoding="utf-8")
    )
    if invalid == "id":
        data["cases"].append(dict(data["cases"][0]))
    elif invalid == "flag":
        data["cases"][0]["fk_expand"] = "false"
    elif invalid == "columns":
        table = next(iter(data["tables"].values()))
        table["columns"].append(table["columns"][0])
    else:
        data["cases"][0]["hits"][0]["unexpected"] = True

    def forbidden(*args):
        raise AssertionError("Node started before validation")

    monkeypatch.setattr(module, "SchemaLinkingNode", forbidden)
    with pytest.raises(ValueError):
        await module.evaluate(data)


@pytest.mark.parametrize("invalid", ["id", "expected", "policy"])
async def test_sql_validator_preflight_happens_before_fixture_database(invalid, monkeypatch):
    from scripts import evaluate_sql_validation as module

    data = json.loads(
        (ROOT / "tests/fixtures/sql_validation_eval.json").read_text(encoding="utf-8")
    )
    if invalid == "id":
        data["cases"].append(dict(data["cases"][0]))
    else:
        data["cases"][0][invalid] = "private invalid bool"

    def forbidden(*args):
        raise AssertionError("Database started before validation")

    monkeypatch.setattr(module.sqlite3, "connect", forbidden)
    with pytest.raises(ValueError):
        await module.evaluate(data)


async def test_fixture_connection_closes_when_schema_initialization_fails(monkeypatch):
    from scripts import evaluate_sql_validation as module

    data = json.loads(
        (ROOT / "tests/fixtures/sql_validation_eval.json").read_text(encoding="utf-8")
    )
    connections = []
    original = sqlite3.connect

    def connect(*args, **kwargs):
        conn = original(*args, **kwargs)
        connections.append(conn)
        return conn

    def fail(*args):
        raise ValueError("synthetic init failure")

    monkeypatch.setattr(module.sqlite3, "connect", connect)
    monkeypatch.setattr(module, "fixture_schema", fail)
    with pytest.raises(ValueError, match="init failure"):
        await module.evaluate(data)
    assert len(connections) == 1
    with pytest.raises(sqlite3.ProgrammingError):
        connections[0].execute("SELECT 1")


def test_baseline_loading_is_after_file_protection(tmp_path, monkeypatch):
    module, source, _ = setup_cli("sql_validation", tmp_path, monkeypatch)

    def forbidden(*args, **kwargs):
        raise AssertionError("Baseline loaded before output protection")

    monkeypatch.setattr(module.subprocess, "check_output", forbidden)
    assert invoke(module, monkeypatch, source, "--baseline-stage10") == 2


def test_prediction_input_is_also_protected(tmp_path, monkeypatch):
    module, _, _ = setup_cli("quality", tmp_path, monkeypatch)
    predictions = tmp_path / "predictions.json"
    predictions.write_text("[]", encoding="utf-8")
    assert invoke(module, monkeypatch, predictions, "--predictions", predictions) == 2
    assert predictions.read_text() == "[]"


def test_malformed_sql_still_counts_as_a_prediction_failure():
    from scripts.evaluate_quality import evaluate_sql

    result = evaluate_sql(
        [{"id": "one", "gold_sql": "SELECT 1"}], [{"id": "one", "sql": "SELECT\x00 1"}]
    )
    assert result["case_count"] == result["prediction_count"] == 1
    assert result["error_counts"] == {"SQL_POLICY_REJECTED": 1}


@pytest.mark.parametrize("kind", ["schema_linking", "sql_validation"])
def test_valid_decision_mismatch_writes_report_and_keeps_exit_one(kind, tmp_path, monkeypatch):
    module, source, output = setup_cli(kind, tmp_path, monkeypatch)
    dataset = json.loads(source.read_text(encoding="utf-8"))
    if kind == "schema_linking":
        dataset["cases"][0]["expected"] = ["fixture_expected_mismatch"]
    else:
        dataset["cases"][0]["expected"] = not dataset["cases"][0]["expected"]
    source.write_text(json.dumps(dataset), encoding="utf-8")
    assert invoke(module, monkeypatch, output) == 1
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["passed"] < report["case_count"]


@pytest.mark.parametrize("limit", ["k", "cases", "documents", "tables", "sql_bytes"])
def test_evaluation_resource_limits_are_enforced(limit):
    from app.evaluation.inputs import (
        quality_dataset,
        schema_linking_dataset,
        sql_validation_dataset,
    )

    if limit in ("k", "documents"):
        data = json.loads((ROOT / "tests/fixtures/retrieval_eval.json").read_text(encoding="utf-8"))
        if limit == "documents":
            data["documents"] = data["documents"][:1] * 10001
        with pytest.raises(ValueError):
            quality_dataset(data, 1001 if limit == "k" else 3)
    elif limit == "tables":
        data = json.loads(
            (ROOT / "tests/fixtures/schema_linking_eval.json").read_text(encoding="utf-8")
        )
        data["tables"] = {str(i): {"columns": [], "references": []} for i in range(1001)}
        with pytest.raises(ValueError):
            schema_linking_dataset(data)
    else:
        data = json.loads(
            (ROOT / "tests/fixtures/sql_validation_eval.json").read_text(encoding="utf-8")
        )
        if limit == "cases":
            data["cases"] = data["cases"][:1] * 10001
        else:
            data["cases"][0]["sql"] = "é" * 524289
        with pytest.raises(ValueError):
            sql_validation_dataset(data)


def test_empty_retrieval_index_is_allowed_without_fabricating_success():
    from scripts.evaluate_quality import evaluate

    report = evaluate(
        {
            "version": "empty-index",
            "documents": [],
            "queries": [
                {"id": "one", "query": "find rows", "domain": "fixture", "relevant": []},
            ],
        },
        3,
        None,
    )
    assert report["document_count"] == 0 and report["query_count"] == 1
    assert report["retrieval"]["recall_at_k"] == 0
    assert report["sql_model_metrics"] is None


def test_sql_validation_accepts_explicit_dataset(tmp_path, monkeypatch):
    module, _, output = setup_cli("sql_validation", tmp_path, monkeypatch)
    source = tmp_path / "custom.json"
    source.write_text(
        json.dumps(
            {
                "version": "custom",
                "scope": "synthetic fixture",
                "cases": [
                    {"id": "one", "sql": "SELECT 1", "expected": True},
                ],
            }
        ),
        encoding="utf-8",
    )
    assert invoke(module, monkeypatch, output, "--dataset", source) == 0
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["passed"] == report["case_count"] == 1


@pytest.mark.parametrize("kind", ["quality", "sql_validation"])
def test_sql_fixture_is_protected_as_an_input(kind, tmp_path, monkeypatch):
    module, _, _ = setup_cli(kind, tmp_path, monkeypatch)
    fixture = module.ROOT / "tests/fixtures/test_schema.sql"
    before = fixture.read_bytes()
    assert invoke(module, monkeypatch, fixture) == 2
    assert fixture.read_bytes() == before
