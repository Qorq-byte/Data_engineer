"""Stream completion evidence must survive the real transport and every adapter."""

import asyncio
from dataclasses import asdict

import litellm
import pytest
from litellm.types.utils import ModelResponseStream, Usage

from app.api.query import CandidateResult
from app.core.sql_generator import SQLGenerator
from app.harness.constraint import ConstraintEnforcer, ConstraintLimits
from app.llm.budget import BudgetedRouter, active_budget
from app.llm.router import LiteLLMRouter, RouterConfig
from app.llm.usage import UsageCollector, active_usage
from app.models.query import SQR
from app.nodes.base import NodeInput
from app.nodes.generate_sql import GenerateSQLNode
from tests.test_llm.test_sdk_boundary import native_router
from tests.test_rag.test_prediction_export import build, response


def delta(text):
    return ModelResponseStream(choices=[{"delta": {"content": text}}])


def end(reason="stop"):
    return ModelResponseStream(choices=[{"delta": {}, "finish_reason": reason}])


def transport_fixture(monkeypatch, items):
    calls, closed = [], []

    async def raw():
        try:
            for item in items:
                if isinstance(item, BaseException):
                    raise item
                if isinstance(item, asyncio.Event):
                    await item.wait()
                else:
                    yield item
        finally:
            closed.append(True)

    async def transport(**options):
        calls.append(options)
        return raw()

    monkeypatch.setattr(litellm, "acompletion", transport)
    return calls, closed


@pytest.mark.parametrize("alternatives", [False, True])
@pytest.mark.parametrize("with_usage", [False, True])
async def test_native_stream_candidate_has_its_own_stop_and_single_attempt(
    monkeypatch, alternatives, with_usage,
):
    text = "SELECT 1\n---ALTERNATIVE---\nSELECT 2" if alternatives else "SELECT 1"
    items = [delta(text), end()]
    if with_usage:
        items.append(ModelResponseStream(choices=[], usage=Usage(
            prompt_tokens=7, completion_tokens=0,
        )))
    calls, closed = transport_fixture(monkeypatch, items)
    collector = UsageCollector()
    budget = ConstraintEnforcer(ConstraintLimits(max_llm_calls_per_query=1))
    u, b = active_usage.set(collector), active_budget.set(budget)
    router = native_router(max_retries=0, max_concurrency=1)
    try:
        output = await GenerateSQLNode(router=BudgetedRouter(router)).execute(
            NodeInput("orders", config={"streaming": True}),
        )
        assert not output.errors
        candidates = output.context["candidates"]
        assert len(candidates) == (2 if alternatives else 1)
        for candidate in candidates:
            assert candidate.generation_source == "model_stream"
            assert candidate.finish_reason == "stop"
            dto = CandidateResult(**asdict(candidate)).model_dump()
            item = response()
            dto.update(id="b", db_id="db2", dialect="sqlite")
            item["candidates"] = [dto]
            assert build(responses=[item])[0]["finish_reason"] == "stop"
        assert len(calls) == len(closed) == collector.summary()["attempt_count"] == 1
        assert budget.llm_calls_used == 1
        record = collector.records[0]
        assert record.status == "completed" and record.finish_status == "stop"
        assert record.completion_tokens == (0 if with_usage else None)
        assert router.cost_tracker.records[-1].success
        assert not any("event" in key or "structured" in key for key in calls[0])
    finally:
        active_usage.reset(u)
        active_budget.reset(b)


async def test_native_text_api_still_returns_only_strings(monkeypatch):
    transport_fixture(monkeypatch, [delta("SELECT "), delta("1"), end()])
    assert [x async for x in native_router().complete_stream([])] == ["SELECT ", "1"]
    assert [x async for x in SQLGenerator(native_router()).generate_stream(
        SQR(raw_text="orders"),
    )] == ["SELECT ", "1"]


async def test_terminal_event_waits_for_usage_tail_and_close(monkeypatch):
    tail = asyncio.Event()
    _, closed = transport_fixture(monkeypatch, [delta("SELECT 1"), end(), tail])
    stream = native_router().complete_stream_events([])
    first = await anext(stream)
    assert first.kind == "delta"
    pending = asyncio.create_task(anext(stream))
    await asyncio.sleep(0.01)
    assert not pending.done() and not closed
    tail.set()
    terminal = await pending
    assert terminal.kind == "end" and terminal.finish_reason == "stop"
    assert closed == [True]
    await stream.aclose()


@pytest.mark.parametrize("failure", ["length", "missing", "late_text", "late_stop", "exception"])
async def test_invalid_native_tail_never_produces_candidate_or_retry(monkeypatch, failure):
    tails = {
        "length": [end("length")], "missing": [], "late_text": [end(), delta("2")],
        "late_stop": [end(), end()], "exception": [end(), RuntimeError("tail failed")],
    }
    calls, closed = transport_fixture(monkeypatch, [delta("SELECT 1"), *tails[failure]])
    router = native_router(max_retries=3, max_concurrency=1)
    output = await GenerateSQLNode(router=BudgetedRouter(router)).execute(
        NodeInput("orders", config={"streaming": True}),
    )
    assert output.errors and output.result["candidates"] == []
    assert len(calls) == len(closed) == 1
    assert not router.cost_tracker.records[-1].success
    # The failed physical stream must release the only slot.
    transport_fixture(monkeypatch, [delta("SELECT 2"), end()])
    assert [x async for x in router.complete_stream([])] == ["SELECT 2"]


@pytest.mark.parametrize("stop_seen", [False, True])
async def test_cancelled_stream_releases_slot_without_success(monkeypatch, stop_seen):
    blocked = asyncio.Event()
    items = [delta("SELECT 1"), *([end()] if stop_seen else []), blocked]
    calls, closed = transport_fixture(monkeypatch, items)
    router = native_router(max_retries=3, max_concurrency=1)
    collector = UsageCollector()
    budget = ConstraintEnforcer(ConstraintLimits(max_llm_calls_per_query=1))
    u, b = active_usage.set(collector), active_budget.set(budget)
    try:
        task = asyncio.create_task(GenerateSQLNode(router=BudgetedRouter(router)).execute(
            NodeInput("orders", config={"streaming": True}),
        ))
        while not calls:
            await asyncio.sleep(0)
        await asyncio.sleep(0.01)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert len(calls) == len(closed) == budget.llm_calls_used == len(collector.records) == 1
        assert collector.records[0].status == "cancelled"
        assert not router.cost_tracker.records[-1].success
    finally:
        active_usage.reset(u)
        active_budget.reset(b)
    transport_fixture(monkeypatch, [delta("SELECT 2"), end()])
    probe = router.complete_stream([])
    try:
        assert await asyncio.wait_for(anext(probe), 1) == "SELECT 2"
    finally:
        await probe.aclose()


@pytest.mark.parametrize("budgeted", [False, True])
async def test_overridden_text_router_does_not_inherit_native_evidence(budgeted):
    calls = []

    class TextRouter(LiteLLMRouter):
        async def complete_stream(self, *args, **kwargs):
            calls.append(True)
            yield "SELECT 1"

    router = TextRouter(RouterConfig(), mock_mode=True)
    node = GenerateSQLNode(router=BudgetedRouter(router) if budgeted else router)
    output = await node.execute(NodeInput("orders", config={"streaming": True}))
    assert not output.errors and calls == [True]
    assert output.context["candidates"][0].generation_source == "stream_text"
    assert output.context["candidates"][0].finish_reason is None


@pytest.mark.parametrize("bad", ["missing", "duplicate", "after", "type", "reason", "source"])
async def test_explicit_event_adapter_is_checked_and_closed(bad):
    from app.llm.streaming import StreamEvent

    closed = []

    class Router:
        config = RouterConfig()

        async def complete_stream_events(self, *args, **kwargs):
            try:
                yield StreamEvent("delta", text="SELECT 1")
                if bad == "type":
                    yield "private malformed event"
                    return
                if bad == "missing":
                    return
                yield StreamEvent(
                    "end", finish_reason="length" if bad == "reason" else "stop",
                    generation_source="prepared" if bad == "source" else "model_stream",
                )
                if bad == "duplicate":
                    yield StreamEvent("end", finish_reason="stop", generation_source="model_stream")
                if bad == "after":
                    yield StreamEvent("delta", text="private late SQL")
            finally:
                closed.append(True)

    output = await GenerateSQLNode(router=BudgetedRouter(Router())).execute(
        NodeInput("orders", config={"streaming": True}),
    )
    assert output.errors and output.result["candidates"] == []
    assert "private" not in str(output.errors)
    assert closed == [True]


async def test_mock_events_keep_unknown_completion():
    output = await GenerateSQLNode(router=BudgetedRouter(LiteLLMRouter(
        RouterConfig(), mock_mode=True,
    ))).execute(NodeInput("SELECT 1", config={"streaming": True}))
    assert not output.errors
    assert output.context["candidates"][0].generation_source == "stream_text"
    assert output.context["candidates"][0].finish_reason is None


@pytest.mark.parametrize("mode", ["node", "text"])
async def test_explicit_event_override_is_budgeted_once(mode):
    from app.llm.streaming import StreamEvent

    class EventRouter(LiteLLMRouter):
        async def complete_stream_events(self, *args, **kwargs):
            yield StreamEvent("delta", text="SELECT 1")
            yield StreamEvent("end", finish_reason="stop", generation_source="model_stream")

    budget = ConstraintEnforcer(ConstraintLimits(max_llm_calls_per_query=1))
    collector = UsageCollector()
    b, u = active_budget.set(budget), active_usage.set(collector)
    try:
        router = BudgetedRouter(EventRouter(RouterConfig(), mock_mode=True))
        if mode == "text":
            assert [x async for x in router.complete_stream([])] == ["SELECT 1"]
        else:
            result = await GenerateSQLNode(router=router).execute(
                NodeInput("orders", config={"streaming": True}),
            )
            assert result.context["candidates"][0].finish_reason == "stop"
        assert budget.llm_calls_used == len(collector.records) == 1
        assert collector.records[0].completion_tokens is None
    finally:
        active_budget.reset(b)
        active_usage.reset(u)


async def test_instance_text_override_is_not_bypassed_or_under_counted():
    calls = []

    async def text(*args, **kwargs):
        calls.append(True)
        yield "SELECT 9"

    router = LiteLLMRouter(RouterConfig(), mock_mode=True)
    router.complete_stream = text
    budget = ConstraintEnforcer(ConstraintLimits(max_llm_calls_per_query=1))
    b = active_budget.set(budget)
    try:
        result = await GenerateSQLNode(router=BudgetedRouter(router)).execute(
            NodeInput("orders", config={"streaming": True}),
        )
        assert calls == [True] and budget.llm_calls_used == 1
        assert result.context["candidates"][0].sql_text == "SELECT 9"
        assert result.context["candidates"][0].finish_reason is None
    finally:
        active_budget.reset(b)


async def test_generator_text_override_does_not_inherit_event_path():
    class Generator(SQLGenerator):
        async def generate_stream(self, *args, **kwargs):
            yield "SELECT 9"

    node = GenerateSQLNode()
    node._generator = Generator(LiteLLMRouter(RouterConfig(), mock_mode=True))
    result = await node.execute(NodeInput("orders", config={"streaming": True}))
    candidate = result.context["candidates"][0]
    assert candidate.sql_text == "SELECT 9" and candidate.finish_reason is None
    assert candidate.generation_source == "stream_text"


@pytest.mark.parametrize("early_close", [False, True])
async def test_timeout_and_early_close_release_slot_and_keep_single_usage(monkeypatch, early_close):
    blocked = asyncio.Event()
    calls, closed = transport_fixture(monkeypatch, [delta("SELECT 1"), end(), blocked])
    collector = UsageCollector()
    budget = ConstraintEnforcer(ConstraintLimits(max_llm_calls_per_query=1))
    router = native_router(max_retries=3, max_concurrency=1, timeout_seconds=0.05)
    b, u = active_budget.set(budget), active_usage.set(collector)
    try:
        if early_close:
            stream = BudgetedRouter(router).complete_stream_events([])
            assert (await anext(stream)).kind == "delta"
            await stream.aclose()
        else:
            result = await GenerateSQLNode(router=BudgetedRouter(router)).execute(
                NodeInput("orders", config={"streaming": True}),
            )
            assert result.errors and result.result["candidates"] == []
        assert len(calls) == len(closed) == budget.llm_calls_used == len(collector.records) == 1
        assert collector.records[0].status == ("closed" if early_close else "timeout")
        assert collector.records[0].completion_tokens is None
        assert not router.cost_tracker.records[-1].success
    finally:
        active_budget.reset(b)
        active_usage.reset(u)
    transport_fixture(monkeypatch, [delta("SELECT 2"), end()])
    assert [x async for x in router.complete_stream([])] == ["SELECT 2"]


async def test_interleaved_native_streams_do_not_borrow_terminal_evidence(monkeypatch):
    entered, release = asyncio.Event(), asyncio.Event()

    async def transport(**options):
        slow = "slow" in options["messages"][0]["content"]

        async def raw():
            if slow:
                entered.set()
                yield delta("SELECT 1")
                await release.wait()  # End without a marker after the sibling succeeds.
            else:
                await entered.wait()
                yield delta("SELECT 2")
                yield end()
                release.set()

        return raw()

    monkeypatch.setattr(litellm, "acompletion", transport)
    router = native_router(max_retries=0, max_concurrency=2)

    async def run(question):
        collector = UsageCollector()
        u = active_usage.set(collector)
        try:
            result = await GenerateSQLNode(router=BudgetedRouter(router)).execute(
                NodeInput(question, config={"streaming": True}),
            )
            assert len(collector.records) == 1
            return result
        finally:
            active_usage.reset(u)

    slow, fast = await asyncio.wait_for(asyncio.gather(run("slow"), run("fast")), 2)
    assert slow.errors and slow.result["candidates"] == []
    assert fast.context["candidates"][0].finish_reason == "stop"
    assert fast.context["candidates"][0].sql_text == "SELECT 2"
