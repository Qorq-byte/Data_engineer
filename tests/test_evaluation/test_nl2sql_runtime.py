"""Synthetic SDK controls exercise the actual30-case Harness, audit and SQL scorer."""

import asyncio
import json
import subprocess
import sys

import pytest

from app.evaluation.nl2sql_batch import load_suite, prepare
from app.evaluation.nl2sql_runtime import CallJournal, approved_guard, run_batch
from app.llm.router import ProviderConfig


@pytest.fixture
def isolated_worker(tmp_path, monkeypatch):
    from app.config.settings import settings
    from app.db.connections import ConnectionFactory
    monkeypatch.setattr(ConnectionFactory, "_connections", {})
    monkeypatch.setattr(ConnectionFactory, "_infos", {})
    monkeypatch.setattr(ConnectionFactory, "_offload_connections", {})
    monkeypatch.setattr(settings, "llm_audit_path", str(tmp_path / "usage.sqlite3"))
    monkeypatch.setattr(settings, "llm_audit_candidate_digests", True)
    monkeypatch.setenv("DE_TRACE_DIR", str(tmp_path / "traces"))
    monkeypatch.chdir(tmp_path)
    return tmp_path


@pytest.mark.parametrize("mixed", [False, True])
async def test_actual_native_harness_audit_and_full_denominators(
        isolated_worker, monkeypatch, mixed):
    import litellm

    from app.db.connections import ConnectionFactory
    from app.rag import engine
    suite = load_suite()
    calls = []
    async def transport(**kwargs):
        i = len(calls)
        calls.append(kwargs)
        assert kwargs["num_retries"] == 0 and kwargs["n"] == 1
        assert kwargs["max_tokens"] == 1024
        assert kwargs["api_base"] == "https://api.deepseek.com/v1"
        prompt = json.dumps(kwargs["messages"], ensure_ascii=False)
        assert "expected_rows" not in prompt and "counterexamples" not in prompt
        assert all(case["gold_sql"] not in prompt for case in suite["sql_cases"])
        mode = i % 6 if mixed else 0
        if mode == 3:
            raise RuntimeError("private-exception-never-persist")
        choice = {"index": 0, "message": {"role": "assistant",
            "content": "SELECT 1" if mode == 4 else suite["sql_cases"][i]["gold_sql"]},
            "finish_reason": "length" if mode == 1 else "stop"}
        if mode == 2:
            choice["message"]["refusal"] = "private-refusal-never-persist"
        return litellm.ModelResponse(choices=[choice], usage=None if mode == 5 else
            {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120})
    monkeypatch.setattr(litellm, "acompletion", transport)
    monkeypatch.setattr(engine, "get_schema_rag", lambda: pytest.fail("embedding/RAG accessed"))
    report = await run_batch(prepare("control-thirty"), "synthetic-private-key", isolated_worker)
    assert report["status"] == "finished"
    assert len(calls) == report["physical_calls_reserved"] == report["native_budget_calls"] == 30
    assert report["evaluation"]["case_count"] == 30
    assert report["evaluation"]["check_count"] == 90
    assert report["by_split"]["dev"]["case_count"] == 20
    assert report["by_split"]["holdout"]["case_count"] == 10
    assert report["latency"]["sample_count"] == 30
    assert report["actual_cost_usd"] is None
    assert all(s["node_order"][:3] == ["parse", "retrieve", "generate"]
               for s in report["cases"])
    assert ConnectionFactory.list_all() == []
    predictions = json.loads((isolated_worker / "predictions.json").read_text(encoding="utf-8"))
    assert len(predictions) == 30
    assert all(p["provenance"]["audit_match"] == "candidate_match"
               for p in predictions if p["status"] == "completed")
    if mixed:
        assert report["case_error_counts"]["TRUNCATED_COMPLETION"] == 5
        assert report["case_error_counts"]["REFUSAL_OR_TOOL_COMPLETION"] == 5
        assert report["evaluation"]["robust_match_count"] < 30
        assert report["usage"]["usage_coverage"] < 1
        assert report["usage"]["prompt_tokens"] is None
    else:
        assert report["evaluation"]["robust_match_count"] == 30
        assert report["evaluation"]["contract_match_count"] == 90
        assert report["generated_count"] == report["validation_passed_count"] == 30
        assert report["usage"]["prompt_tokens"] == 3000
        assert report["usage"]["completion_tokens"] == 600
    for path in isolated_worker.rglob("*.json"):
        value = path.read_text(encoding="utf-8")
        assert "synthetic-private-key" not in value
        assert "private-exception-never-persist" not in value
        assert "private-refusal-never-persist" not in value


@pytest.mark.parametrize("defect", ["model", "n", "output", "stream", "overrides", "size"])
def test_guard_rejects_unapproved_calls_before_reserving(tmp_path, defect):
    provider = ProviderConfig("deepseek", "DEEPSEEK_API_KEY")
    journal = CallJournal(tmp_path)
    journal.case_id = "sql01"
    args = [provider, "deepseek-v4-flash", [{"role": "user", "content": "fixture"}],
            1, 1024, False, {}]
    if defect == "model":
        args[1] = "other"
    elif defect == "n":
        args[3] = 2
    elif defect == "output":
        args[4] = 1025
    elif defect == "stream":
        args[5] = True
    elif defect == "overrides":
        args[6] = {"api_base": "https://unapproved.test"}
    else:
        args[2][0]["content"] = "x" * 32769
    with pytest.raises(RuntimeError):
        approved_guard(provider, journal)(*args)
    assert journal.records == []


def test_duplicate_case_and_batch_overflow_never_reserve(tmp_path):
    journal = CallJournal(tmp_path)
    for i in range(30):
        journal.case_id = f"sql{i+1:02d}"
        journal.reserve([])
    with pytest.raises(RuntimeError):
        journal.reserve([])
    journal.case_id = "sql31"
    with pytest.raises(RuntimeError):
        journal.reserve([])
    assert len(journal.records) == 30


async def test_cancellation_preserves_full_denominator_and_closes_connection(
        isolated_worker, monkeypatch):
    import litellm

    from app.db.connections import ConnectionFactory
    entered = asyncio.Event()
    async def transport(**kwargs):
        entered.set()
        await asyncio.Event().wait()
    monkeypatch.setattr(litellm, "acompletion", transport)
    task = asyncio.create_task(run_batch(prepare("cancel-control"),
                                         "synthetic-key", isolated_worker))
    await asyncio.wait_for(entered.wait(), 10)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    report = json.loads((isolated_worker / "report.json").read_text(encoding="utf-8"))
    assert report["status"] == "cancelled"
    assert report["physical_calls_reserved"] == 1
    assert report["evaluation"]["case_count"] == 30
    assert report["evaluation"]["check_count"] == 90
    assert report["evaluation"]["error_counts"]["MISSING_PREDICTION"] == 90
    assert report["usage"]["prompt_tokens"] is None
    assert ConnectionFactory.list_all() == []


def test_prepare_and_cli_import_no_sdk_or_runtime_in_fresh_process(tmp_path):
    code = """
import sys
from app.evaluation.nl2sql_batch import prepare
from scripts.run_nl2sql_acceptance import main
value=prepare('no-sdk-control')
assert len(value['cases'])==30
assert not any(name in sys.modules for name in ('litellm','openai','httpx',
 'app.evaluation.nl2sql_runtime','app.evaluation.embedding_runtime'))
"""
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr.decode(errors="replace")


async def test_failed_reservation_aborts_batch_before_sdk(isolated_worker, monkeypatch):
    import litellm

    from app.evaluation import nl2sql_runtime as runtime
    original = runtime.write_atomic
    async def forbidden(**kwargs):
        pytest.fail("SDK reached after journal failure")
    def fail(path, rows):
        if path.name == "requests.json" and rows:
            raise OSError("private-disk-failure")
        original(path, rows)
    monkeypatch.setattr(litellm, "acompletion", forbidden)
    monkeypatch.setattr(runtime, "write_atomic", fail)
    with pytest.raises(RuntimeError, match="JOURNAL_PUBLICATION_FAILED"):
        await run_batch(prepare("disk-failure"), "synthetic-key", isolated_worker)


def test_prompt_drift_rejects_valid_call_before_sdk(tmp_path):
    provider = ProviderConfig("deepseek", "DEEPSEEK_API_KEY")
    journal = CallJournal(tmp_path)
    journal.case_id = "sql01"
    with pytest.raises(RuntimeError, match="PROMPT_IDENTITY_MISMATCH"):
        approved_guard(provider, journal)(provider, "deepseek-v4-flash",
            [{"role": "user", "content": "changed approved question"}], 1, 1024, False, {})
    assert journal.records == []


@pytest.mark.parametrize("mode", ["start", "timeout", "bad_report"])
def test_parent_supervision_fails_closed_with_full_denominator(tmp_path, monkeypatch, mode):
    from scripts import run_nl2sql_acceptance as cli
    destination = tmp_path / "one-shot"
    monkeypatch.setattr(cli, "run_directory", lambda name: destination)
    killed = []
    class Child:
        def wait(self, timeout):
            if mode == "timeout" and not killed:
                raise subprocess.TimeoutExpired("control", timeout)
            return 0
        def kill(self):
            killed.append(True)
    def spawn(*args, **kwargs):
        assert kwargs["env"]["DEEPSEEK_API_KEY"] == "synthetic-key"
        assert "OPENAI_API_KEY" not in kwargs["env"]
        assert "synthetic-key" not in json.dumps(args)
        assert kwargs["cwd"] == destination / "workspace"
        if mode == "start":
            raise OSError("private-start-error")
        return Child()
    monkeypatch.setattr(cli.subprocess, "Popen", spawn)
    report = cli.supervise(prepare("supervised-control"), "synthetic-key")
    assert report["status"] == "failed"
    assert report["evaluation"]["case_count"] == 30
    assert report["evaluation"]["check_count"] == 90
    assert bool(killed) == (mode == "timeout")
    with pytest.raises(FileExistsError):
        cli.supervise(prepare("supervised-control"), "synthetic-key")


def test_fresh_worker_uses_frozen_prompts_without_host_connections(tmp_path):
    import os

    from app.evaluation.nl2sql_batch import ROOT
    code = r"""
import os,json,asyncio
from pathlib import Path
root=Path(os.environ['CONTROL_ROOT'])
from app.evaluation.nl2sql_batch import prepare,load_suite,manifest_digest
from app.evaluation.artifacts import write_atomic
manifest=prepare('fresh-worker-control')
cases=load_suite()['sql_cases']
run=root/'run';run.mkdir()
workspace=run/'workspace';workspace.mkdir()
owner='a'*32
write_atomic(run/'run-state.json',{'owner':owner,'state':'starting',
 'manifest_sha256':manifest_digest(manifest)})
os.chdir(workspace)
os.environ.update(LLM_AUDIT_PATH=str(run/'usage.sqlite3'),
 LLM_AUDIT_CANDIDATE_DIGESTS='true',DE_TRACE_DIR=str(run/'traces'),LLM_MOCK_MODE='0',
 DEEPSEEK_API_KEY='synthetic-key')
import litellm
litellm.telemetry=False
calls=[]
async def transport(**kwargs):
 calls.append(kwargs)
 assert kwargs['api_key']=='synthetic-key'
 return litellm.ModelResponse(choices=[{'index':0,
  'message':{'role':'assistant','content':cases[len(calls)-1]['gold_sql']},
  'finish_reason':'stop'}],usage={'prompt_tokens':100,'completion_tokens':20,'total_tokens':120})
litellm.acompletion=transport
import scripts.run_nl2sql_acceptance as cli
cli.run_directory=lambda name:run
report=asyncio.run(cli.worker(manifest,owner))
assert report['status']=='finished',report
assert len(calls)==30,report
assert report['evaluation']['robust_match_count']==30,report
assert report['evaluation']['contract_match_count']==90,report
assert report['generated_count']==30,report
assert report['usage']['usage_coverage']==1,report
try:asyncio.run(cli.worker(manifest,owner))
except ValueError:pass
else:raise AssertionError('worker replay allowed')
"""
    keep = {k: v for k, v in os.environ.items() if k.upper() in
            {"PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "USERNAME", "USER",
             "LOGNAME", "LNAME", "HOME", "USERPROFILE"}}
    env = dict(keep, CONTROL_ROOT=str(tmp_path), PYTHONPATH=str(ROOT), PYTHONUTF8="1",
               LITELLM_LOCAL_MODEL_COST_MAP="True", RAG_EMBEDDING_PROVIDER="mock")
    result = subprocess.run([sys.executable, "-c", code], cwd=tmp_path, env=env,
                            capture_output=True, timeout=120)
    assert result.returncode == 0, result.stderr.decode(errors="replace")
