"""Completion evidence belongs to the selected candidate, never a request sibling."""

import asyncio
from dataclasses import asdict
from types import SimpleNamespace

import pytest

from app.api import query
from app.core.self_heal import SelfHealingRetry
from app.core.sql_generator import SQLGenerator
from app.llm.router import LiteLLMRouter, RouterConfig
from app.models.query import SQR, SQLCandidate, ValidationReport
from app.nodes.base import NodeInput
from app.nodes.generate_sql import GenerateSQLNode
from tests.test_api import test_query_contract as contracts
from tests.test_llm.test_completion_finish import ScriptedRouter, completion
from tests.test_rag.test_prediction_export import binding, build, dataset, response

connected_query = contracts.connected_query


@pytest.mark.parametrize("strategy", ["temperature", "multi_perspective"])
@pytest.mark.parametrize("object_shape", [False, True])
async def test_alternatives_share_only_their_own_response_evidence(strategy, object_shape):
    first = completion("stop", "SELECT 1\n---ALTERNATIVE---\nSELECT 2")
    second = completion(None, "SELECT 3")
    first["model"] = "first-response-model"
    if object_shape:
        first = SimpleNamespace(model=first["model"], choices=[SimpleNamespace(
            finish_reason="stop",
            message=SimpleNamespace(content=first["choices"][0]["message"]["content"]),
        )])
        second = SimpleNamespace(choices=[SimpleNamespace(
            message=SimpleNamespace(content="SELECT 3"),
        )])
    candidates = await SQLGenerator(ScriptedRouter([first, second])).generate(
        SQR(raw_text="orders"), num_candidates=2, strategy=strategy,
    )
    assert [c.sql_text for c in candidates] == ["SELECT 1", "SELECT 2", "SELECT 3"]
    assert [c.finish_reason for c in candidates] == ["stop", "stop", None]
    assert [c.generation_source for c in candidates] == ["model_response"] * 3
    assert [c.model for c in candidates[:2]] == ["first-response-model"] * 2
    assert candidates[2].model != "first-response-model"


async def test_interleaved_generations_do_not_share_completion_evidence():
    entered = asyncio.Event()

    class Router(ScriptedRouter):
        async def complete(self, *args, **kwargs):
            if "slow" in kwargs["messages"][0]["content"]:
                entered.set()
                await asyncio.sleep(0.01)
                return completion(None, "SELECT 1")
            await entered.wait()
            return completion("stop", "SELECT 2")

    generator = SQLGenerator(Router([]))
    unknown, stopped = await asyncio.gather(
        generator.generate(SQR(raw_text="slow")), generator.generate(SQR(raw_text="fast")),
    )
    assert unknown[0].finish_reason is None
    assert stopped[0].finish_reason == "stop"


def test_legacy_candidate_and_dto_do_not_invent_completion():
    candidate = SQLCandidate(id="old", sql_text="SELECT 1")
    dto = query.CandidateResult(**asdict(candidate)).model_dump()
    assert dto["generation_source"] == "unknown"
    assert dto["finish_reason"] is None


async def test_prepared_branch_copies_and_clears_model_completion():
    original = SQLCandidate(id="prepared", sql_text="SELECT 1")
    original.finish_reason = "stop"
    original.generation_source = "model_response"
    node = GenerateSQLNode()
    output = await node.execute(NodeInput("orders", config={"prepared_candidates": [original]}))
    candidate = output.context["primary_candidate"]
    assert candidate is not original
    assert candidate.finish_reason is None and candidate.generation_source == "prepared"
    assert original.finish_reason == "stop" and original.generation_source == "model_response"


@pytest.mark.parametrize("alternatives", [False, True])
async def test_text_stream_does_not_invent_terminal_evidence(alternatives):
    closed = []

    class Generator:
        async def generate_stream(self, *args, **kwargs):
            try:
                yield "SELECT 1"
                if alternatives:
                    yield "\n---ALTERNATIVE---\nSELECT 2"
            finally:
                closed.append(True)

    node = GenerateSQLNode()
    node._generator = Generator()
    output = await node.execute(NodeInput("orders", config={"streaming": True}))
    assert closed == [True]
    candidates = output.context["candidates"]
    assert len(candidates) == (2 if alternatives else 1)
    assert all(c.generation_source == "stream_text" and c.finish_reason is None for c in candidates)


@pytest.mark.parametrize("validated", [False, True])
async def test_healing_preserves_corrected_candidate_evidence(validated):
    generator = SQLGenerator(ScriptedRouter([completion("stop", "SELECT 1")]))

    class Validator:
        async def validate(self, *args, **kwargs):
            return ValidationReport(passed=True)

    healer = SelfHealingRetry(generator, Validator() if validated else None)
    corrected = await healer.retry_until_valid(SQR(raw_text="orders"), "SELECT broken", "syntax")
    assert corrected.finish_reason == "stop" and corrected.generation_source == "model_response"
    assert corrected.generation_mode == "self_heal"


@pytest.mark.parametrize("protocol", ["json", "sse", "mcp"])
@pytest.mark.parametrize("prepared", [False, True])
async def test_protocols_preserve_actual_candidate_evidence(
    connected_query, monkeypatch, protocol, prepared,
):
    class Router(LiteLLMRouter):
        async def complete(self, *args, **kwargs):
            assert not prepared, "prepared SQL must not call LLM"
            return completion("stop", "SELECT id FROM orders ORDER BY id")

    monkeypatch.setattr(query, "get_default_router", lambda: Router(RouterConfig(), mock_mode=True))
    question = "数据库有多少张表" if prepared else "find all orders"
    if protocol == "mcp":
        result = await contracts.mcp_query(nl_text=question, db_id="contract")
    else:
        path = "/api/v1/query" + ("/stream" if protocol == "sse" else "")
        http = connected_query.post(path, json={
            "nl_text": question, "database": "contract", "execute": True, "num_candidates": 1,
        })
        assert http.status_code == 200
        result = (
            next(v for e, v in contracts.stream_events(http) if e == "done")
            if protocol == "sse" else http.json()
        )
    if not prepared:
        assert result["status"] == "completed", result.get("errors")
    # The existing validator can reject sqlite_master as outside the business schema.
    # Candidate origin must survive that workflow failure as well.
    candidate = result["candidates"][0]
    if protocol == "mcp":
        assert "execution" not in candidate  # Preserve the tool's top-level 100-row cap.
    assert candidate["generation_source"] == ("prepared" if prepared else "model_response")
    assert candidate["finish_reason"] == (None if prepared else "stop")
    assert result["llm_calls"] == (0 if prepared else 1)


@pytest.mark.parametrize("source", ["prepared", "invalid-source", 123])
def test_export_rejects_prepared_and_invalid_origins(source):
    item = response()
    item["candidates"][1].update(generation_source=source, finish_reason="stop")
    with pytest.raises(ValueError, match="source|Prepared"):
        build(responses=[item])


@pytest.mark.parametrize("source", ["unknown", "stream_text", "model_response"])
@pytest.mark.parametrize("reason", [None, "stop"])
def test_export_reads_selected_candidate_not_request_or_siblings(source, reason):
    item = response()
    item["candidates"][0].update(generation_source="prepared", finish_reason="stop")
    item["candidates"][1].update(generation_source=source, finish_reason=reason)
    row = build(responses=[item])[0]
    assert row["finish_reason"] == (reason if source == "model_response" else None)
    expected = "candidate_fields" if source == "model_response" else "unavailable"
    assert row["provenance"]["completion_source"] == expected


@pytest.mark.parametrize("source", ["unknown", "stream_text"])
@pytest.mark.parametrize("marker", ["length", "refusal", "tool_calls"])
def test_unknown_origin_never_hides_explicit_unsafe_completion(source, marker):
    item = response()
    candidate = item["candidates"][1]
    candidate.update(generation_source=source, finish_reason="stop")
    if marker == "length":
        candidate["finish_reason"] = "length"
    else:
        candidate[marker] = "private data"
    row = build(responses=[item])[0]
    assert row["finish_reason"] in ("length", "other")
    assert "private" not in str(row)


async def test_runtime_candidate_exports_stop_without_borrowing_request_usage():
    generator = SQLGenerator(ScriptedRouter([completion()]))
    candidate = (await generator.generate(SQR(raw_text="orders")))[0]
    item = response()
    selected = asdict(candidate)
    selected.update(id="b", db_id="db2", dialect="sqlite")
    item["candidates"] = [selected]
    item["llm_usage"] = None
    row = build(data=dataset(), responses=[item], bindings=[binding()])[0]
    assert row["finish_reason"] == "stop"
    assert row["provenance"]["llm_usage"] is None
