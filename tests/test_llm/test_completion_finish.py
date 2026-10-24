"""A syntactically valid SQL prefix still cannot establish model completion."""

import asyncio
from types import SimpleNamespace

import pytest

from app.core.sql_generator import SQLGenerator
from app.llm.completion import IncompleteCompletionError
from app.llm.router import LiteLLMRouter, RouterConfig
from app.models.query import SQR


def completion(reason="stop", text="SELECT id FROM orders"):
    return {"choices": [{"finish_reason": reason, "message": {"content": text}}]}


class ScriptedRouter:
    config = RouterConfig()

    def __init__(self, responses):
        self.responses = iter(responses)

    async def complete(self, *args, **kwargs):
        return next(self.responses)


def chunk(text=None, reason=None):
    return SimpleNamespace(choices=[SimpleNamespace(
        delta=SimpleNamespace(content=text), finish_reason=reason,
    )])


async def chunks(items):
    for item in items:
        yield item


async def test_length_sql_prefix_never_becomes_candidate():
    generator = SQLGenerator(ScriptedRouter([completion("length")]))
    with pytest.raises(RuntimeError, match="output limit"):
        await generator.generate(SQR(raw_text="find filtered orders"))


@pytest.mark.parametrize("end", ["length", None])
async def test_stream_cannot_accept_length_or_missing_terminal_marker(end):
    items = [chunk("SELECT id FROM orders")]
    if end:
        items.append(chunk(reason=end))
    with pytest.raises(RuntimeError, match="output limit|completion marker"):
        _ = [part async for part in LiteLLMRouter._stream_to_async_gen(chunks(items))]


@pytest.mark.parametrize("strategy", ["temperature", "multi_perspective"])
@pytest.mark.parametrize("reason", ["length", "content_filter", "tool_calls", "unknown-secret"])
async def test_rejects_whole_response_and_preserves_complete_independent_sibling(strategy, reason):
    router = ScriptedRouter([
        completion(reason, "SELECT 999\n---ALTERNATIVE---\nSELECT 998"),
        completion("stop", "SELECT 1"),
    ])
    warnings = []
    result = await SQLGenerator(router).generate(
        SQR(raw_text="orders"), num_candidates=2, strategy=strategy, warnings=warnings,
    )
    assert [candidate.sql_text for candidate in result] == ["SELECT 1"]
    assert result[0].generation_mode == "primary"
    assert len(warnings) == 1 and "Discarded incomplete" in warnings[0]
    assert "unknown-secret" not in warnings[0] and "999" not in warnings[0]


@pytest.mark.parametrize("object_shape", [False, True])
@pytest.mark.parametrize("reason", ["length", "content_filter", "function_call", "secret", 7])
async def test_all_rejected_fail_with_safe_error(object_shape, reason):
    response = completion(reason, "SELECT private_customer_data")
    if object_shape:
        response = SimpleNamespace(choices=[SimpleNamespace(
            finish_reason=reason, message=SimpleNamespace(content="SELECT private_customer_data"),
        )])
    with pytest.raises(IncompleteCompletionError) as failure:
        await SQLGenerator(ScriptedRouter([response])).generate(SQR(raw_text="orders"))
    assert "private_customer_data" not in str(failure.value)
    assert "secret" not in str(failure.value)


@pytest.mark.parametrize("reason", ["stop", None])
async def test_nonstream_stop_and_legacy_missing_reason_compatible(reason):
    response = completion(reason, "SELECT 1")
    if reason is None:
        del response["choices"][0]["finish_reason"]
    result = await SQLGenerator(ScriptedRouter([response])).generate(SQR(raw_text="orders"))
    assert result[0].sql_text == "SELECT 1"


@pytest.mark.parametrize("field", ["refusal", "tool_calls", "function_call"])
async def test_stop_cannot_override_refusal_or_tool_request(field):
    response = completion()
    response["choices"][0]["message"][field] = "private tool arguments"
    with pytest.raises(IncompleteCompletionError, match="refusal or tool"):
        await SQLGenerator(ScriptedRouter([response])).generate(SQR(raw_text="orders"))


async def test_success_stream_accepts_dicts_and_final_usage_chunk():
    items = [
        {"choices": [{"delta": {"content": "SELECT "}, "finish_reason": None}]},
        chunk("1", "stop"), SimpleNamespace(choices=[], usage={"completion_tokens": 2}),
    ]
    result = [text async for text in LiteLLMRouter._stream_to_async_gen(chunks(items))]
    assert result == ["SELECT ", "1"]


@pytest.mark.parametrize("tail", [chunk("2"), chunk(reason="stop")])
async def test_stream_continuation_after_stop_rejected(tail):
    with pytest.raises(IncompleteCompletionError, match="continued after"):
        _ = [text async for text in LiteLLMRouter._stream_to_async_gen(
            chunks([chunk("SELECT 1", "stop"), tail])
        )]


@pytest.mark.parametrize("reason", ["content_filter", "tool_calls", "private reason"])
async def test_stream_explicit_nonstop_is_never_success(reason):
    with pytest.raises(IncompleteCompletionError) as failure:
        _ = [text async for text in LiteLLMRouter._stream_to_async_gen(
            chunks([chunk("SELECT 1"), chunk(reason=reason)])
        )]
    assert "private reason" not in str(failure.value)


@pytest.mark.parametrize("field", ["refusal", "tool_calls", "function_call"])
async def test_stream_refusal_and_tools_rejected(field):
    event = chunk("SELECT 1", "stop")
    setattr(event.choices[0].delta, field, "private arguments")
    with pytest.raises(IncompleteCompletionError, match="refusal or tool"):
        _ = [text async for text in LiteLLMRouter._stream_to_async_gen(chunks([event]))]


@pytest.mark.parametrize("content", [False, 0, [], {}, 123])
async def test_stream_nontext_values_rejected_even_when_falsey(content):
    with pytest.raises(IncompleteCompletionError, match="non-text"):
        _ = [text async for text in LiteLLMRouter._stream_to_async_gen(
            chunks([chunk(content, "stop")])
        )]


async def test_real_transport_stream_failure_closes_and_releases_slot_without_retry(monkeypatch):
    import app.llm.router as module
    from app.harness.constraint import ConstraintEnforcer, ConstraintLimits
    from app.llm.budget import active_budget
    from app.llm.router import ProviderConfig

    calls, closed = [], []
    end = "length"

    async def transport(**options):
        calls.append(options)

        async def events():
            try:
                yield chunk("SELECT 1")
                yield chunk(reason=end)
            finally:
                closed.append(True)

        return events()

    monkeypatch.setattr(module, "_LITELLM_AVAILABLE", True)
    monkeypatch.setattr(module.litellm, "acompletion", transport)
    router = LiteLLMRouter(RouterConfig(
        providers={"openai": ProviderConfig("openai", "", models=["fixture"],
                                            api_key="synthetic-test-key")},
        default_model="fixture", fallback_chain=["fixture"], max_retries=3, max_concurrency=1,
    ))
    budget = ConstraintEnforcer(ConstraintLimits(max_llm_calls_per_query=2))
    token = active_budget.set(budget)
    try:
        with pytest.raises(IncompleteCompletionError):
            _ = [text async for text in router.complete_stream([])]
        assert len(calls) == budget.llm_calls_used == len(closed) == 1
        end = "stop"
        async with asyncio.timeout(1):
            assert [text async for text in router.complete_stream([])] == ["SELECT 1"]
        assert len(calls) == budget.llm_calls_used == len(closed) == 2
    finally:
        active_budget.reset(token)


@pytest.mark.parametrize("exit_mode", ["close", "cancel", "error", "length", "stop"])
async def test_stream_always_closes_upstream(exit_mode):
    closed = []

    async def events():
        try:
            yield chunk("SELECT 1")
            if exit_mode == "cancel":
                await asyncio.Event().wait()
            if exit_mode == "error":
                raise OSError("transport failure")
            yield chunk(reason="length" if exit_mode == "length" else "stop")
        finally:
            closed.append(True)

    stream = LiteLLMRouter._stream_to_async_gen(events())
    assert await anext(stream) == "SELECT 1"
    if exit_mode == "close":
        await stream.aclose()
    elif exit_mode == "cancel":
        task = asyncio.create_task(anext(stream))
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    elif exit_mode in ("error", "length"):
        with pytest.raises((OSError, IncompleteCompletionError)):
            await anext(stream)
    else:
        with pytest.raises(StopAsyncIteration):
            await anext(stream)
    assert closed == [True]


@pytest.mark.parametrize("streaming", [False, True])
async def test_node_discards_accumulated_sql_after_incomplete_response(streaming):
    from app.nodes.base import NodeInput
    from app.nodes.generate_sql import GenerateSQLNode

    class Router(ScriptedRouter):
        async def complete_stream(self, *args, **kwargs):
            async for text in LiteLLMRouter._stream_to_async_gen(
                chunks([chunk("SELECT id FROM orders"), chunk(reason="length")])
            ):
                yield text

    node = GenerateSQLNode(router=Router([completion("length")]))
    output = await node.execute(NodeInput("filtered orders", config={"streaming": streaming}))
    assert output.result == {"candidates": [], "primary_sql": None}
    assert output.context == {}
    assert output.metadata["status"] == "generation_error"
    assert "output limit" in output.errors[0]


async def test_same_generator_concurrent_diagnostics_are_request_local():
    class Router(ScriptedRouter):
        async def complete(self, messages, **kwargs):
            await asyncio.sleep(0)
            return completion("length" if "bad-marker" in messages[0]["content"] else "stop")

    generator = SQLGenerator(Router([]))
    bad_warnings, good_warnings = [], []
    results = await asyncio.gather(
        generator.generate(SQR(raw_text="bad-marker"), warnings=bad_warnings),
        generator.generate(SQR(raw_text="good-marker"), warnings=good_warnings),
        return_exceptions=True,
    )
    assert isinstance(results[0], IncompleteCompletionError)
    assert results[1][0].sql_text == "SELECT id FROM orders"
    assert bad_warnings == good_warnings == []


async def test_public_generator_budget_router_early_close_releases_real_transport(monkeypatch):
    import app.llm.router as module
    from app.llm.budget import BudgetedRouter
    from app.llm.router import ProviderConfig

    closed = []

    async def transport(**options):
        async def events():
            try:
                yield chunk("SELECT 1")
                yield chunk(reason="stop")
            finally:
                closed.append(True)
        return events()

    monkeypatch.setattr(module, "_LITELLM_AVAILABLE", True)
    monkeypatch.setattr(module.litellm, "acompletion", transport)
    router = LiteLLMRouter(RouterConfig(
        providers={"openai": ProviderConfig("openai", "", models=["fixture"],
                                            api_key="synthetic-test-key")},
        default_model="fixture", fallback_chain=[], max_concurrency=1,
    ))
    generator = SQLGenerator(BudgetedRouter(router))
    stream = generator.generate_stream(SQR(raw_text="orders"))
    assert await anext(stream) == "SELECT 1"
    await stream.aclose()
    assert closed == [True]
    async with asyncio.timeout(1):
        assert [text async for text in generator.generate_stream(SQR(raw_text="orders"))] == [
            "SELECT 1"
        ]
    assert closed == [True, True]
