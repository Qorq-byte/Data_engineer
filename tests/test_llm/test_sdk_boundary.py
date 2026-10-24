"""Real SDK objects through a synthetic SDK transport; never a model request."""

import asyncio
from types import SimpleNamespace

import litellm
import pytest
from litellm.types.utils import ModelResponseStream, Usage

from app.core.sql_generator import SQLGenerator
from app.harness.constraint import ConstraintEnforcer, ConstraintLimits
from app.llm.budget import BudgetedRouter, active_budget
from app.llm.router import LiteLLMRouter, ProviderConfig, RouterConfig
from app.llm.usage import UsageCollector, active_usage
from app.models.query import SQR


def sdk_response(reason="stop", usage=None, **message):
    return litellm.ModelResponse(
        model="fixture",
        usage=usage,
        choices=[
            {
                "message": {"role": "assistant", "content": "SELECT id FROM orders", **message},
                "finish_reason": reason,
            }
        ],
    )


def native_router(**kwargs):
    return LiteLLMRouter(
        RouterConfig(
            providers={"openai": ProviderConfig("openai", "", ["fixture"], api_key="synthetic")},
            default_model="fixture",
            fallback_chain=[],
            retry_backoff_seconds=0,
            **kwargs,
        )
    )


@pytest.mark.parametrize("usage", [None, {"prompt_tokens": 4, "completion_tokens": 2}])
async def test_actual_sdk_response_becomes_dict_without_inventing_usage(monkeypatch, usage):
    original = sdk_response(usage=usage)
    assert not isinstance(original, dict)
    calls = []

    async def transport(**options):
        calls.append(options)
        return original

    monkeypatch.setattr(litellm, "acompletion", transport)
    collector = UsageCollector()
    budget = ConstraintEnforcer(ConstraintLimits(max_llm_calls_per_query=1))
    t = active_usage.set(collector)
    b = active_budget.set(budget)
    try:
        result = await BudgetedRouter(native_router(max_retries=0)).complete([])
        assert isinstance(result, dict)
        assert result["choices"][0]["message"]["content"] == "SELECT id FROM orders"
        summary = collector.summary()
        assert summary["attempt_count"] == budget.llm_calls_used == len(calls) == 1
        assert summary["attempts"][0]["usage_basis"] == "sdk_normalized"
        assert summary["attempts"][0]["prompt_tokens"] == (4 if usage else None)
        assert summary["missing_usage_count"] == (0 if usage else 1)
        assert calls[0]["num_retries"] == 0
    finally:
        active_budget.reset(b)
        active_usage.reset(t)


@pytest.mark.parametrize(
    "reason,message",
    [
        ("stop", {}),
        ("length", {}),
        ("content_filter", {}),
        ("stop", {"refusal": "private refusal"}),
        (
            "tool_calls",
            {
                "tool_calls": [
                    {
                        "id": "fixture",
                        "type": "function",
                        "function": {"name": "private_tool", "arguments": "private_args"},
                    }
                ]
            },
        ),
    ],
)
async def test_sdk_completion_markers_reach_sql_guard(monkeypatch, reason, message):
    async def transport(**options):
        return sdk_response(reason, {"prompt_tokens": 3, "completion_tokens": 1}, **message)

    monkeypatch.setattr(litellm, "acompletion", transport)
    collector = UsageCollector()
    t = active_usage.set(collector)
    try:
        generator = SQLGenerator(BudgetedRouter(native_router(max_retries=0)))
        if reason == "stop" and not message:
            assert (await generator.generate(SQR(raw_text="orders")))[0].sql_text
        else:
            with pytest.raises(RuntimeError):
                await generator.generate(SQR(raw_text="orders"))
        assert collector.records[0].status == (
            "completed" if reason == "stop" and not message else "incomplete"
        )
        assert collector.records[0].prompt_tokens == 3
        assert "private" not in str(collector.summary())
    finally:
        active_usage.reset(t)


async def test_sdk_exception_usage_survives_retry_mapping(monkeypatch):
    calls = []

    async def transport(**options):
        calls.append(options)
        if len(calls) == 1:
            error = litellm.InternalServerError(
                message="private", model="fixture", llm_provider="openai"
            )
            error.usage = {"prompt_tokens": 5, "completion_tokens": 0}
            raise error
        return sdk_response(usage={"prompt_tokens": 2, "completion_tokens": 1})

    monkeypatch.setattr(litellm, "acompletion", transport)
    collector = UsageCollector()
    t = active_usage.set(collector)
    try:
        await native_router(max_retries=1).complete([])
        assert [r.status for r in collector.records] == ["failed", "completed"]
        assert collector.summary()["known_prompt_tokens"] == 7
        assert collector.summary()["missing_usage_count"] == 0
        assert collector.records[0].completion_tokens == 0
        assert all(r.usage_basis == "sdk_normalized" for r in collector.records)
        assert "private" not in str(collector.summary())
    finally:
        active_usage.reset(t)


async def test_sdk_stream_tail_usage_and_cleanup(monkeypatch):
    closed = []

    async def raw():
        try:
            yield ModelResponseStream(choices=[{"delta": {"content": "SELECT 1"}}])
            yield ModelResponseStream(choices=[{"delta": {}, "finish_reason": "stop"}])
            yield ModelResponseStream(choices=[], usage=Usage(prompt_tokens=4, completion_tokens=2))
        finally:
            closed.append(True)

    async def transport(**options):
        return raw()

    monkeypatch.setattr(litellm, "acompletion", transport)
    collector = UsageCollector()
    t = active_usage.set(collector)
    try:
        assert [x async for x in native_router(max_retries=0).complete_stream([])] == ["SELECT 1"]
        assert collector.records[0].status == "completed"
        assert collector.records[0].usage_basis == "sdk_normalized"
        assert collector.summary()["known_prompt_tokens"] == 4
        assert closed
    finally:
        active_usage.reset(t)


async def test_dict_transport_stays_compatible(monkeypatch):
    response = {"choices": [], "usage": {"prompt_tokens": 0, "completion_tokens": 2}}

    async def transport(**options):
        return response

    monkeypatch.setattr(litellm, "acompletion", transport)
    collector = UsageCollector()
    t = active_usage.set(collector)
    try:
        assert await native_router(max_retries=0).complete([]) is response
        assert collector.records[0].usage_basis == "response_fields"
        assert collector.records[0].prompt_tokens == 0
    finally:
        active_usage.reset(t)


@pytest.mark.parametrize("response", [None, [], "private", 123, SimpleNamespace()])
def test_normalizer_rejects_unstructured_values(response):
    from app.llm.responses import normalize_completion

    with pytest.raises(TypeError, match="Non-streaming model response must be an object"):
        normalize_completion(response)


@pytest.mark.parametrize("bad_dump", [None, [], "private", RuntimeError("private")])
def test_serializer_failure_is_safe(bad_dump):
    from app.llm.responses import normalize_completion

    class Broken:
        def model_dump(self):
            if isinstance(bad_dump, Exception):
                raise bad_dump
            return bad_dump

    with pytest.raises(TypeError) as failure:
        normalize_completion(Broken())
    assert "private" not in str(failure.value)


def test_sdk_defaulted_zero_is_identified_as_normalized():
    # The actual SDK erased original presence; fields_set also contains both counts.
    usage = Usage(prompt_tokens=4)
    assert usage.completion_tokens == 0
    assert "completion_tokens" in usage.model_fields_set
    response = sdk_response(usage=usage)
    collector = UsageCollector()
    from app.llm.usage import observe

    with observe(collector, "openai", "fixture", "transport_boundary"):
        collector.observe(response, usage_basis="sdk_normalized")
    assert collector.summary()["attempts"][0]["usage_basis"] == "sdk_normalized"


async def test_sdk_stream_cancel_keeps_provenance_and_closes(monkeypatch):
    entered = asyncio.Event()
    closed = []

    async def raw():
        try:
            yield ModelResponseStream(choices=[{"delta": {"content": "SELECT "}}])
            entered.set()
            await asyncio.Event().wait()
        finally:
            closed.append(True)

    async def transport(**options):
        return raw()

    monkeypatch.setattr(litellm, "acompletion", transport)
    collector = UsageCollector()
    t = active_usage.set(collector)
    try:

        async def consume():
            return [x async for x in native_router(max_retries=0).complete_stream([])]

        task = asyncio.create_task(consume())
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert collector.records[0].status == "cancelled"
        assert collector.records[0].usage_basis == "sdk_normalized"
        assert collector.records[0].prompt_tokens is None
        assert closed
    finally:
        active_usage.reset(t)


@pytest.mark.parametrize("getter", ["model_dump", "usage"])
async def test_broken_sdk_metadata_does_not_expose_exception(monkeypatch, getter):
    class Response:
        def model_dump(self):
            return {"choices": []}

        @property
        def usage(self):
            raise RuntimeError("private metadata")

    class BrokenSerializer(Response):
        @property
        def model_dump(self):
            raise RuntimeError("private serializer")

    async def transport(**options):
        return BrokenSerializer() if getter == "model_dump" else Response()

    monkeypatch.setattr(litellm, "acompletion", transport)
    collector = UsageCollector()
    t = active_usage.set(collector)
    try:
        if getter == "model_dump":
            with pytest.raises(TypeError) as failure:
                await native_router(max_retries=0).complete([])
            assert "private" not in str(failure.value)
        else:
            assert await native_router(max_retries=0).complete([]) == {"choices": []}
        assert collector.records[0].prompt_tokens is None
        assert "private" not in str(collector.summary())
    finally:
        active_usage.reset(t)


async def test_sdk_multiple_candidates_do_not_double_count(monkeypatch):
    calls = []

    async def transport(**options):
        calls.append(options)
        return sdk_response(usage={"prompt_tokens": 3, "completion_tokens": 2})

    monkeypatch.setattr(litellm, "acompletion", transport)
    collector = UsageCollector()
    t = active_usage.set(collector)
    try:
        candidates = await SQLGenerator(BudgetedRouter(native_router(max_retries=0))).generate(
            SQR(raw_text="orders"),
            num_candidates=3,
        )
        assert candidates
        assert len(collector.records) == len(calls) == 3
        assert collector.summary()["known_prompt_tokens"] == 9
        assert collector.summary()["known_completion_tokens"] == 6
    finally:
        active_usage.reset(t)


def test_normalizer_does_not_initialize_sdk_in_fresh_process():
    import subprocess
    import sys

    code = """
import importlib.abc
import sys
class NoSDK(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "litellm" or fullname.startswith("litellm."):
            raise AssertionError("SDK import attempted")
sys.meta_path.insert(0, NoSDK())
from app.llm.responses import normalize_completion
assert normalize_completion({"choices": []}) == {"choices": []}
assert "litellm" not in sys.modules
"""
    subprocess.run([sys.executable, "-c", code], check=True, timeout=20)


async def test_direct_dict_stream_failure_does_not_change_usage_basis():
    async def raw():
        yield {
            "choices": [{"delta": {"content": "SELECT 1"}, "finish_reason": "length"}],
            "usage": {"prompt_tokens": 3, "completion_tokens": 1},
        }

    from app.llm.usage import observe

    collector = UsageCollector()
    t = active_usage.set(collector)
    try:
        with (
            pytest.raises(RuntimeError),
            observe(collector, "fixture", "fixture", "adapter_boundary"),
        ):
            _ = [x async for x in LiteLLMRouter._stream_to_async_gen(raw())]
        assert collector.records[0].usage_basis == "response_fields"
        assert collector.records[0].prompt_tokens == 3
    finally:
        active_usage.reset(t)


@pytest.mark.parametrize("before_first_chunk", [True, False])
async def test_sdk_stream_error_retains_direct_usage_without_retry(monkeypatch, before_first_chunk):
    calls = []
    closed = []

    async def raw():
        try:
            if not before_first_chunk:
                yield ModelResponseStream(choices=[{"delta": {"content": "SELECT "}}])
            error = litellm.InternalServerError(
                message="private",
                model="fixture",
                llm_provider="openai",
            )
            error.usage = Usage(prompt_tokens=5, completion_tokens=1)
            raise error
        finally:
            closed.append(True)

    async def transport(**options):
        calls.append(options)
        return raw()

    monkeypatch.setattr(litellm, "acompletion", transport)
    collector = UsageCollector()
    t = active_usage.set(collector)
    try:
        with pytest.raises(litellm.InternalServerError):
            _ = [x async for x in native_router(max_retries=2).complete_stream([])]
        assert len(calls) == len(collector.records) == 1
        assert collector.records[0].status == "failed"
        assert collector.records[0].usage_basis == "sdk_normalized"
        assert collector.summary()["known_prompt_tokens"] == 5
        assert "private" not in str(collector.summary())
        assert closed
    finally:
        active_usage.reset(t)
