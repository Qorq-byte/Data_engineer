"""No SDK before approval; four native attempts may feed twelve local checks."""

import copy
import hashlib
import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from app.evaluation.contract_suite import evaluate_suite, load_suite, suite_paths
from app.evaluation.model_batch import V2_PROFILE, manifest_digest, prepare, validate
from scripts.evaluate_sql_contracts import main


def test_v2_manifest_contains_all_fixture_hashes_but_no_labels():
    manifest = prepare("v2-manifest", profile=V2_PROFILE)
    assert validate(manifest, manifest_digest(manifest)) == manifest
    assert manifest["evaluation_scenarios"] == ["base", "edge", "empty"]
    assert manifest["limits"]["max_calls"] == 4
    for path in suite_paths():
        assert path.name in " ".join(manifest["sources"])
    raw = json.dumps(manifest)
    assert "gold_sql" not in raw and "expected_rows" not in raw


def test_v2_annotation_failure_never_imports_sdk(monkeypatch):
    import app.evaluation.contract_suite as suite_module

    invalid = copy.deepcopy(load_suite())
    invalid["sql_cases"][0]["expected_rows"]["edge"] = []
    monkeypatch.setattr(suite_module, "read_json", lambda _: invalid)
    with pytest.raises(ValueError, match="labels disagree"):
        prepare("invalid-labels", profile=V2_PROFILE)


def test_unknown_profile_is_rejected():
    with pytest.raises(ValueError):
        prepare("unknown-profile", profile="private-business")


@pytest.mark.parametrize("path", suite_paths())
def test_cli_cannot_overwrite_any_suite_input(path):
    original = path.read_bytes()
    assert main(["--output", str(path)]) == 2
    assert path.read_bytes() == original


@pytest.mark.parametrize("raw", ['[{"id":"one","id":"two"}]', '[NaN]', '[1]', '{}'])
def test_cli_invalid_predictions_preserve_previous_output(tmp_path, raw, capsys):
    predictions, output = tmp_path / "private.json", tmp_path / "report.json"
    predictions.write_text(raw, encoding="utf-8")
    output.write_bytes(b"previous report")
    assert main(["--predictions", str(predictions), "--output", str(output)]) == 2
    assert output.read_bytes() == b"previous report"
    assert "private" not in capsys.readouterr().err


def test_cli_without_predictions_reports_all_missing(tmp_path):
    output = tmp_path / "report.json"
    assert main(["--output", str(output)]) == 0
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["case_count"] == 4 and report["check_count"] == 12
    assert report["error_counts"] == {"MISSING_PREDICTION": 12}


def test_cli_parser_warnings_cannot_echo_sql(tmp_path, caplog, capsys):
    import logging

    suite = load_suite()
    rows = [{"id": c["id"], "sql": c["gold_sql"], "finish_reason": "stop"}
            for c in suite["sql_cases"]]
    rows[0]["sql"] = "UNSUPPORTED_PRIVATE_SQL_MARKER some private text"
    source, output = tmp_path / "predictions.json", tmp_path / "result.json"
    source.write_text(json.dumps(rows), encoding="utf-8")
    previous = logging.root.manager.disable
    assert main(["--predictions", str(source), "--output", str(output)]) == 0
    assert logging.root.manager.disable == previous
    captured = capsys.readouterr()
    assert "PRIVATE_SQL_MARKER" not in captured.out + captured.err + caplog.text
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["robust_match_count"] == 3


def test_fresh_v2_cli_four_native_calls_twelve_checks_and_bound_exports(tmp_path):
    code = textwrap.dedent("""
        import os,json,sys
        from pathlib import Path
        import scripts.run_model_evaluation as cli
        from app.evaluation.model_batch import manifest_digest
        from app.evaluation.contract_suite import load_suite
        root = Path(os.environ['CONTRACT_RUN_ROOT'])
        source = root / 'manifest.json'
        assert cli.main(['--prepare','--profile','synthetic-sqlite-model-v2',
                         '--manifest',str(source),'--run-name','contract-process']) == 0
        assert 'litellm' not in sys.modules
        import litellm
        suite = load_suite()
        calls = []
        async def transport(**kwargs):
            prompt = json.dumps(kwargs['messages'],ensure_ascii=False)
            assert all(c['gold_sql'] not in prompt for c in suite['sql_cases'])
            assert 'expected_rows' not in prompt
            calls.append(1)
            return litellm.ModelResponse(choices=[{
                'index':0, 'message':{'role':'assistant',
                    'content':suite['sql_cases'][len(calls)-1]['gold_sql']},
                'finish_reason':'stop',
            }], usage={'prompt_tokens':100,'completion_tokens':20,'total_tokens':120})
        litellm.acompletion = transport
        os.environ['LLM_MOCK_MODE']='0'
        os.environ['DEEPSEEK_API_KEY']='synthetic-not-a-real-key'
        os.environ['MYSQL_HOST']='BUSINESS-DO-NOT-CONNECT'
        manifest=json.loads(source.read_text(encoding='utf-8'))
        target=root/'run'
        cli.run_directory=lambda _: target
        assert cli.main(['--execute','--manifest',str(source),
                         '--confirm-manifest-sha256',manifest_digest(manifest)])==0
        report=json.loads((target/'report.json').read_text(encoding='utf-8'))
        assert len(calls)==report['physical_calls_reserved']==4
        assert report['evaluation']['check_count']==12
        assert report['evaluation']['robust_match_count']==4
        assert report['actual_cost_usd'] is None
        predictions=json.loads((target/'predictions.json').read_text(encoding='utf-8'))
        assert all(r['provenance']['audit_match']=='candidate_match' for r in predictions)
        assert sum(r['provenance']['llm_calls'] for r in predictions)==4
        from app.config.settings import settings
        from app.db.connections import ConnectionFactory
        assert settings.mysql_host!='BUSINESS-DO-NOT-CONNECT'
        assert ConnectionFactory.list_all()==[]
    """)
    result = subprocess.run([sys.executable, "-c", code],
                            env=dict(os.environ, CONTRACT_RUN_ROOT=str(tmp_path)),
                            capture_output=True, check=True)
    assert b"synthetic-not-a-real-key" not in result.stdout + result.stderr
    rows = json.loads((tmp_path / "run/predictions.json").read_text(encoding="utf-8"))
    rows[0]["sql"] = "SELECT 999 AS id"
    with pytest.raises(ValueError, match="hash mismatch"):
        evaluate_suite(load_suite(), rows)


def test_legacy_fixture_bytes_are_unchanged_from_stage38():
    hashes = {
        "test_schema.sql": "e453667017c6185fd9cefdd40b2fe783314cd83637e2d4ca3b03cdd98ef84b60",
        "retrieval_eval.json": "5fa0ec23519804ee5bde3e0a12357a3e9a494a41dfe31901401359094e17a3a1",
    }
    for name, expected in hashes.items():
        raw = (Path("tests/fixtures") / name).read_bytes().replace(b"\r\n", b"\n")
        assert hashlib.sha256(raw).hexdigest() == expected
