"""Controlled model batches must fail closed before spending calls."""

import asyncio
import json
import subprocess
import sys

import pytest

from app.evaluation.model_batch import manifest_digest, prepare, validate
from app.harness.constraint import ConstraintEnforcer, ConstraintLimits
from app.llm.budget import active_budget, active_call_limits, reserve_llm_call


def budget(count):
    return ConstraintEnforcer(ConstraintLimits(max_llm_calls_per_query=count))


def test_budget_group_reserves_atomically_and_deduplicates():
    query, batch, exhausted = budget(2), budget(4), budget(0)
    token = active_budget.set(query)
    extra = active_call_limits.set((batch, exhausted, query))
    try:
        with pytest.raises(RuntimeError, match="budget"):
            reserve_llm_call()
        assert query.llm_calls_used == batch.llm_calls_used == 0
        active_call_limits.set((batch, query, batch))
        reserve_llm_call()
        assert query.llm_calls_used == batch.llm_calls_used == 1
    finally:
        active_call_limits.reset(extra)
        active_budget.reset(token)


async def test_parallel_caller_limits_are_isolated():
    async def request():
        own = budget(1)
        token = active_call_limits.set((own,))
        try:
            await asyncio.sleep(0)
            reserve_llm_call()
            with pytest.raises(RuntimeError):
                reserve_llm_call()
            return own.llm_calls_used
        finally:
            active_call_limits.reset(token)
    assert await asyncio.gather(request(), request()) == [1, 1]


async def test_shared_batch_limit_cannot_be_oversubscribed_by_child_tasks():
    shared = budget(1)

    async def request():
        own = budget(1)
        token = active_call_limits.set((shared, own))
        try:
            await asyncio.sleep(0)
            try:
                reserve_llm_call()
            except RuntimeError:
                return own.llm_calls_used
            return own.llm_calls_used
        finally:
            active_call_limits.reset(token)
    assert sorted(await asyncio.gather(request(), request())) == [0, 1]
    assert shared.llm_calls_used == 1


def test_prepare_is_stable_public_and_has_no_gold_or_credentials():
    manifest = prepare("regression")
    assert validate(manifest, manifest_digest(manifest)) == manifest
    assert len(manifest["cases"]) == 4
    assert "gold_sql" not in json.dumps(manifest)
    assert manifest["limits"]["max_calls"] == 4
    assert manifest["limits"]["max_retries"] == 0


@pytest.mark.parametrize("field,value", [("model", "other"), ("run_name", "../escape")])
def test_manifest_mutation_rejected(field, value):
    manifest = prepare("regression")
    digest = manifest_digest(manifest)
    manifest[field] = value
    with pytest.raises(ValueError):
        validate(manifest, digest)


def test_self_hashed_resource_expansion_is_rejected():
    manifest = prepare("regression")
    manifest["limits"]["max_calls"] = 100
    with pytest.raises(ValueError):
        validate(manifest, manifest_digest(manifest))


def test_prepare_in_fresh_process_does_not_import_sdk(tmp_path):
    code = (
        "import sys; from app.evaluation.model_batch import prepare; prepare('no-sdk'); "
        "assert 'litellm' not in sys.modules; assert 'app.config.settings' not in sys.modules"
    )
    subprocess.run([sys.executable, "-c", code], check=True, capture_output=True)


def test_stale_source_rejected(monkeypatch):
    import app.evaluation.model_batch as module

    manifest = prepare("stale")
    monkeypatch.setattr(module, "source_hashes", lambda: {"modified": "digest"})
    with pytest.raises(ValueError, match="changed"):
        validate(manifest, manifest_digest(manifest))


@pytest.mark.parametrize("name", ["../escape", "a/b", "", ".", "a" * 65])
def test_run_name_is_a_single_safe_component(name):
    with pytest.raises(ValueError):
        prepare(name)


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


async def test_four_cases_use_native_accounting_audit_binding_and_full_denominator(
    isolated_worker, monkeypatch,
):
    import litellm

    from app.db.connections import ConnectionFactory
    from app.evaluation.model_batch import DATASET
    from app.evaluation.model_runtime import run_batch

    dataset = json.loads(DATASET.read_text(encoding="utf-8"))
    calls = []

    async def transport(**kwargs):
        calls.append(kwargs)
        assert kwargs["n"] == 1 and kwargs["num_retries"] == 0
        assert kwargs["max_tokens"] == 1024
        prompt = json.dumps(kwargs["messages"], ensure_ascii=False)
        assert all(c["gold_sql"] not in prompt for c in dataset["sql_cases"])
        sql = dataset["sql_cases"][len(calls) - 1]["gold_sql"]
        return litellm.ModelResponse(model="deepseek-v4-flash", choices=[{
            "index": 0, "message": {"role": "assistant", "content": sql},
            "finish_reason": "stop",
        }], usage={"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120})

    monkeypatch.setattr(litellm, "acompletion", transport)
    report = await run_batch(prepare("four"), "synthetic-key", isolated_worker)
    assert report["status"] == "finished"
    assert len(calls) == report["physical_calls_reserved"] == 4
    assert report["actual_cost_usd"] is None
    assert report["evaluation"]["case_count"] == 4
    assert report["evaluation"]["result_correct_count"] == 4
    rows = json.loads((isolated_worker / "predictions.json").read_text(encoding="utf-8"))
    assert all(r["provenance"]["audit_match"] == "candidate_match" for r in rows)
    assert all(r["provenance"]["llm_calls"] == 1 for r in rows)
    assert ConnectionFactory.list_all() == []


@pytest.mark.parametrize("change", ["provider", "model", "n", "output", "stream", "size", "kwargs"])
async def test_guard_rejects_before_reservation_or_sdk(change, monkeypatch):
    import litellm

    from app.evaluation.model_runtime import ControlledRouter, approved_guard
    from app.llm.budget import active_call_guard
    from app.llm.router import ProviderConfig, RouterConfig

    provider = ProviderConfig("deepseek", "KEY", ["model"], api_key="not-a-real-key")
    other = ProviderConfig("deepseek", "KEY", ["model"], api_key="other-account")
    config = RouterConfig(providers={"deepseek": provider}, default_model="model",
                          fallback_chain=[], max_retries=0, max_output_tokens=1024)
    router = ControlledRouter(config)
    arguments = [provider, "model", [{"role": "user", "content": "hello"}], 0.0, 1, False]
    if change == "provider":
        arguments[0] = other
    elif change == "model":
        arguments[1] = "another"
    elif change == "n":
        arguments[4] = 2
    elif change == "output":
        config.max_output_tokens = 2048
    elif change == "stream":
        arguments[5] = True
    elif change == "size":
        arguments[2] = [{"role": "user", "content": "x" * 32768}]

    async def forbidden(**kwargs):
        pytest.fail("Rejected request reached SDK")
    monkeypatch.setattr(litellm, "acompletion", forbidden)
    own = budget(1)
    guard_token = active_call_guard.set(approved_guard(provider, "model"))
    limit_token = active_call_limits.set((own,))
    try:
        with pytest.raises(RuntimeError, match="Controlled model"):
            await router._limited_call(*arguments, **({"api_base": "https://other.invalid"}
                                                      if change == "kwargs" else {}))
        assert own.llm_calls_used == 0
    finally:
        active_call_guard.reset(guard_token)
        active_call_limits.reset(limit_token)


async def test_provider_error_is_redacted_and_failures_keep_denominator(
    isolated_worker, monkeypatch,
):
    import litellm

    from app.evaluation.model_runtime import run_batch

    async def transport(**kwargs):
        raise RuntimeError("PRIVATE-TOKEN-AND-PROVIDER-BODY")
    monkeypatch.setattr(litellm, "acompletion", transport)
    report = await run_batch(prepare("errors"), "synthetic-key", isolated_worker)
    assert report["physical_calls_reserved"] == 4
    assert report["evaluation"]["case_count"] == 4
    assert report["evaluation"]["result_correct_count"] == 0
    assert all(s["status"] == "failed" for s in report["cases"])
    for path in isolated_worker.rglob("*"):
        if path.is_file():
            assert b"PRIVATE-TOKEN-AND-PROVIDER-BODY" not in path.read_bytes()


async def test_cancellation_closes_transport_and_database(isolated_worker, monkeypatch):
    import litellm

    from app.db.connections import ConnectionFactory
    from app.evaluation.model_runtime import run_batch

    started, closed = asyncio.Event(), asyncio.Event()

    async def transport(**kwargs):
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            closed.set()
    monkeypatch.setattr(litellm, "acompletion", transport)
    task = asyncio.create_task(run_batch(prepare("cancel"), "synthetic-key", isolated_worker))
    await asyncio.wait_for(started.wait(), 10)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert closed.is_set() and ConnectionFactory.list_all() == []
    report = json.loads((isolated_worker / "report.json").read_text(encoding="utf-8"))
    assert report["status"] == "cancelled" and report["physical_calls_reserved"] == 1
    assert [s["status"] for s in report["cases"]] == ["cancelled"] + ["unattempted"] * 3
    assert active_call_limits.get() == ()


@pytest.mark.parametrize("failure", ["confirmation", "credential", "existing_directory"])
def test_execute_preflight_failures_never_import_sdk(tmp_path, failure):
    manifest = prepare("preflight")
    source = tmp_path / "manifest.json"
    source.write_text(json.dumps(manifest), encoding="utf-8")
    digest = "incorrect" if failure == "confirmation" else manifest_digest(manifest)
    code = (
        "import os,sys; from pathlib import Path; "
        "import scripts.run_model_evaluation as cli; "
        "os.environ['LLM_MOCK_MODE']='0'; os.environ.pop('DEEPSEEK_API_KEY',None); "
        f"cli.run_directory=lambda manifest: Path({str(tmp_path)!r}); "
    )
    if failure == "existing_directory":
        code += "os.environ['DEEPSEEK_API_KEY']='synthetic-not-a-real-key'; "
    code += (
        f"assert cli.main(['--execute','--manifest',{str(source)!r},"
        f"'--confirm-manifest-sha256',{digest!r}]) == 2; "
        "assert 'litellm' not in sys.modules"
    )
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, check=True)
    assert b"synthetic-not-a-real-key" not in result.stderr


@pytest.mark.parametrize("mode", ["timeout", "audit_failed"])
async def test_timeout_or_audit_failure_cannot_fabricate_bound_success(
    isolated_worker, monkeypatch, mode,
):
    import litellm

    from app.audit.store import UsageAuditStore
    from app.db.connections import ConnectionFactory
    from app.evaluation.model_batch import LIMITS
    from app.evaluation.model_runtime import run_batch

    calls, closed = [], []

    async def transport(**kwargs):
        calls.append(1)
        try:
            if mode == "timeout":
                await asyncio.Event().wait()
            return litellm.ModelResponse(choices=[{
                "index": 0, "message": {"role": "assistant", "content": "SELECT 1"},
                "finish_reason": "stop",
            }])
        finally:
            closed.append(1)

    def failed_write(self, record):
        raise OSError("synthetic audit failure")

    monkeypatch.setattr(litellm, "acompletion", transport)
    if mode == "timeout":
        monkeypatch.setitem(LIMITS, "case_seconds", 0.1)
    else:
        monkeypatch.setattr(UsageAuditStore, "write", failed_write)
    report = await run_batch(prepare("failure"), "synthetic-key", isolated_worker)
    assert len(calls) == len(closed) == report["physical_calls_reserved"] == 4
    assert [s["status"] for s in report["cases"]] == [mode] * 4
    assert report["evaluation"]["case_count"] == 4
    assert report["evaluation"]["prediction_count"] == 0
    assert report["evaluation"]["result_correct_count"] == 0
    assert ConnectionFactory.list_all() == []


def test_fresh_cli_worker_runs_without_preloaded_application(tmp_path):
    import textwrap

    script = textwrap.dedent("""
        import os, json
        from pathlib import Path
        import litellm
        import scripts.run_model_evaluation as cli
        from app.evaluation.model_batch import DATASET, prepare, manifest_digest
        root = Path(os.environ['SYNTHETIC_RUN_ROOT'])
        cases = json.loads(DATASET.read_text(encoding='utf-8'))['sql_cases']
        calls = []
        async def transport(**kwargs):
            calls.append(1)
            return litellm.ModelResponse(choices=[{
                'index': 0,
                'message': {'role': 'assistant', 'content': cases[len(calls)-1]['gold_sql']},
                'finish_reason': 'stop',
            }])
        litellm.acompletion = transport
        os.environ['LLM_MOCK_MODE'] = '0'
        os.environ['DEEPSEEK_API_KEY'] = 'synthetic-not-a-real-key'
        manifest = prepare('fresh-process')
        source = root / 'manifest.json'
        source.write_text(json.dumps(manifest), encoding='utf-8')
        destination = root / 'run'
        cli.run_directory = lambda _: destination
        result = cli.main(['--execute', '--manifest', str(source),
                           '--confirm-manifest-sha256', manifest_digest(manifest)])
        report = json.loads((destination/'report.json').read_text(encoding='utf-8'))
        assert result == 0, report
        assert len(calls) == 4
        assert report['evaluation']['result_correct_count'] == 4
    """)
    import os
    env = dict(os.environ, SYNTHETIC_RUN_ROOT=str(tmp_path))
    subprocess.run([sys.executable, '-c', script], env=env, check=True, capture_output=True)
