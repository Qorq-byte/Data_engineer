"""Probe real Schema linking and prompt assembly with a deterministic Router.

The caller supplies a RAG backed by verified collection vectors. This module
never creates a provider or calls an external model. Its result proves context
wiring under the fixed plan, not SQL model quality.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from dataclasses import asdict
from pathlib import Path
from typing import Any

from app.evaluation.artifacts import write_atomic
from app.evaluation.predictions import fingerprint
from app.harness.constraint import ConstraintEnforcer, ConstraintLimits
from app.harness.events import NodeEvent, NodeEventStatus
from app.harness.plan_loader import PlanLoader
from app.harness.runner import WorkflowRunner
from app.knowledge.retrieval.schema_rag import SchemaMetadataRAG
from app.knowledge.schema_retriever import SchemaRetrievalResult, SchemaRetriever
from app.llm.budget import BudgetedRouter
from app.llm.router import RouterConfig
from app.models.query import SQR
from app.models.schema import ColumnSchema, ForeignKey, SchemaSnapshot, TableSchema
from app.nodes.base import NodeInput, NodeOutput
from app.nodes.generate_sql import GenerateSQLNode
from app.nodes.registry import NodeRegistry
from app.nodes.schema_linking import SchemaLinkingNode

QUERY_TEXT = "orders user_id users id 关联键"
PLAN_PATH = (Path(__file__).resolve().parents[2]
             / "tests/fixtures/embedding_acceptance/schema_prompt.yml")
ORDER_COLUMN_MARKER = "stage43_linked_order_column"
USER_COLUMN_MARKER = "stage43_linked_user_column"
UNRELATED_COLUMN = "stage43_unrelated_column_not_for_prompt"
EXPECTED_LIFECYCLE = ["link.setup_input", "link.execute", "link.update_context",
                      "gen.setup_input", "gen.execute", "gen.update_context"]


def _schema() -> SchemaSnapshot:
    return SchemaSnapshot(database_type="sqlite", database_name="stage43_probe", tables={
        "orders": TableSchema(name="orders", schema_name="main", columns=[
            ColumnSchema("id", "INTEGER", is_primary_key=True),
            ColumnSchema("user_id", "INTEGER", is_foreign_key=True,
                         references=("users", "id"), comment=ORDER_COLUMN_MARKER),
            ColumnSchema("product_id", "INTEGER", is_foreign_key=True,
                         references=("products", "id")),
            ColumnSchema("amount", "REAL"),
        ], foreign_keys=[ForeignKey("orders_user_fk", "user_id", "users", "id"),
                         ForeignKey("orders_product_fk", "product_id", "products", "id")]),
        "users": TableSchema(name="users", schema_name="main", columns=[
            ColumnSchema("id", "INTEGER", is_primary_key=True),
            ColumnSchema("name", "TEXT", comment=USER_COLUMN_MARKER),
        ]),
        "products": TableSchema(name="products", schema_name="main", columns=[
            ColumnSchema("id", "INTEGER", is_primary_key=True), ColumnSchema("name", "TEXT"),
        ]),
        "unrelated": TableSchema(name="unrelated", schema_name="main", columns=[
            ColumnSchema("id", "INTEGER", is_primary_key=True),
            ColumnSchema(UNRELATED_COLUMN, "TEXT"),
        ]),
    })


class _RecordedRetriever(SchemaRetriever):
    def __init__(self, rag: SchemaMetadataRAG) -> None:
        super().__init__(rag)
        self.last_result: SchemaRetrievalResult | None = None

    async def retrieve(self, query: str, schema: SchemaSnapshot,
                       **kwargs: Any) -> SchemaRetrievalResult:
        result = await super().retrieve(query, schema, **kwargs)
        self.last_result = result
        return result


class _RecordedLink(SchemaLinkingNode):
    def __init__(self, rag: SchemaMetadataRAG, events: list[str]) -> None:
        self.probe_retriever = _RecordedRetriever(rag)
        super().__init__(retriever=self.probe_retriever)
        self._events = events

    async def setup_input(self, raw_input: dict[str, Any]) -> NodeInput:
        self._events.append("link.setup_input")
        return await super().setup_input(raw_input)

    async def execute(self, input: NodeInput) -> NodeOutput:
        self._events.append("link.execute")
        return await super().execute(input)

    async def update_context(self, output: NodeOutput,
                             shared_context: dict[str, Any]) -> dict[str, Any]:
        self._events.append("link.update_context")
        return await super().update_context(output, shared_context)


class _ProbeRouter:
    config = RouterConfig(default_model="stage43-deterministic-probe", fallback_chain=[])

    def __init__(self, linking: list[NodeOutput]) -> None:
        self.linking = linking
        self.calls = 0
        self.messages: list[dict[str, str]] = []

    async def complete(
        self, messages: list[dict[str, str]], model: str | None = None,
        temperature: float = 0.7, n: int = 1, **kwargs: Any,
    ) -> dict[str, Any]:
        self.calls += 1
        self.messages = [dict(message) for message in messages]
        prompt = "\n".join(message["content"] for message in messages)
        if (self.calls != 1 or n != 1 or not self.linking
                or self.linking[0].metadata.get("method") != "hybrid"
                or self.linking[0].metadata.get("warnings")
                or ORDER_COLUMN_MARKER not in prompt or USER_COLUMN_MARKER not in prompt
                or UNRELATED_COLUMN in prompt):
            raise RuntimeError("Schema-to-prompt wiring proof failed")
        return {"model": self.config.default_model, "choices": [{"message": {
            "content": "SELECT id FROM orders ORDER BY id"}, "finish_reason": "stop"}]}

    def complete_stream(
        self, messages: list[dict[str, str]], model: str | None = None,
        temperature: float = 0.7, **kwargs: Any,
    ) -> AsyncIterator[str]:
        raise RuntimeError("Streaming is outside the deterministic wiring probe")


class _RecordedGenerate(GenerateSQLNode):
    def __init__(self, router: _ProbeRouter, events: list[str]) -> None:
        super().__init__(router=BudgetedRouter(router))
        self._events = events

    async def setup_input(self, raw_input: dict[str, Any]) -> NodeInput:
        self._events.append("gen.setup_input")
        return await super().setup_input(raw_input)

    async def execute(self, input: NodeInput) -> NodeOutput:
        self._events.append("gen.execute")
        return await super().execute(input)

    async def update_context(self, output: NodeOutput,
                             shared_context: dict[str, Any]) -> dict[str, Any]:
        self._events.append("gen.update_context")
        return await super().update_context(output, shared_context)


async def schema_prompt_probe(
    rag: SchemaMetadataRAG, query_text: str, run_dir: Path,
) -> dict[str, Any]:
    """Prove verified RAG context reaches the generator through the fixed Harness.

    Query text is fixed to the existing frozen q03 input, so this proof needs no
    additional collection request. Artifacts live under run_dir/schema-prompt;
    no global settings/environment, registry, RAG engine or default Router changes.
    """
    if query_text != QUERY_TEXT:
        raise ValueError("Schema prompt probe requires the frozen retrieval03 text")
    plan = PlanLoader(str(PLAN_PATH.parent)).load(PLAN_PATH.stem)
    if ([(node.id, node.node) for node in plan.node_order]
            != [("link", "schema_linking"), ("gen", "generate_sql")]
            or plan.max_iterations != 2 or plan.failure_policy.max_retries != 0
            or not plan.evaluation.enabled
            or plan.evaluation.metrics != ["execution_success", "syntax_valid"]):
        raise ValueError("Schema prompt probe plan changed")
    output_dir = run_dir / "schema-prompt"
    output_dir.mkdir(parents=True, exist_ok=False)
    lifecycle: list[str] = []
    linking: list[NodeOutput] = []
    router = _ProbeRouter(linking)
    registry = NodeRegistry()
    link_node = _RecordedLink(rag, lifecycle)
    registry.register(link_node)
    registry.register(_RecordedGenerate(router, lifecycle))
    constraints = ConstraintEnforcer(ConstraintLimits(
        max_llm_calls_per_query=1, max_node_retries=0, read_only=True,
    ))
    runner = WorkflowRunner(plan, registry, constraints=constraints)
    trace_path = output_dir / "workflow-trace.json"

    async def persist_own_trace() -> None:
        payload = json.loads(json.dumps(asdict(runner.trace), default=str, allow_nan=False))
        await asyncio.to_thread(write_atomic, trace_path, payload)

    async def record_event(status: NodeEventStatus, event: NodeEvent) -> None:
        if status == "started" and event["node_id"] == "link":
            # Override only this newly created trace instance's sink; the actual
            # runner still owns lifecycle/order and invokes persist normally.
            object.__setattr__(runner.trace, "persist", persist_own_trace)
        if status == "completed" and event["node_id"] == "link":
            output = event.get("output")
            if output is not None:
                linking.append(output)

    result = await runner.run(query_text, on_node_event=record_event,
                              schema=_schema(), sqr=SQR(raw_text=query_text))
    trace_order = [node.node_name for node in runner.trace.nodes]
    if (result.status != "completed" or lifecycle != EXPECTED_LIFECYCLE
            or trace_order != ["schema_linking", "generate_sql"]
            or router.calls != 1 or constraints.llm_calls_used != 1
            or not linking or linking[0].metadata.get("method") != "hybrid"
            or linking[0].metadata.get("warnings")
            or result.context.get("primary_sql") != "SELECT id FROM orders ORDER BY id"):
        raise RuntimeError("Schema-to-prompt wiring proof failed")
    linked = result.context.get("linked_schema")
    if (not isinstance(linked, SchemaSnapshot)
            or not {"orders", "users"} <= set(result.context.get("linked_tables", []))
            or linked.tables["unrelated"].columns):
        raise RuntimeError("Schema-to-prompt filtered context proof failed")
    retrieved = link_node.probe_retriever.last_result
    if retrieved is None or not retrieved.search_results:
        raise RuntimeError("Schema-to-prompt retrieved-hit proof failed")
    hit_evidence = [{"doc_id": str(getattr(hit, "doc_id", "")),
                     "db_id": str(getattr(hit, "db_id", "")),
                     "schema_name": str(getattr(hit, "schema_name", "")),
                     "table_name": str(getattr(hit, "table_name", ""))}
                    for hit in retrieved.search_results]
    if any(hit["db_id"] != "shop_a" or hit["schema_name"] != "main"
           for hit in hit_evidence):
        raise RuntimeError("Schema-to-prompt retrieved namespace proof failed")
    write_atomic(output_dir / "prompt.json", router.messages)
    report: dict[str, Any] = {
        "status": "passed", "query_id": "retrieval03", "query_text_sha256": fingerprint(query_text),
        "plan_sha256": fingerprint(PLAN_PATH.read_text(encoding="utf-8")),
        "trace_order": trace_order, "node_lifecycle": lifecycle,
        "link_method": linking[0].metadata["method"], "link_warnings": [],
        "retrieved_doc_ids": [hit["doc_id"] for hit in hit_evidence],
        "retrieved_hit_evidence": hit_evidence,
        "linked_tables": result.context["linked_tables"],
        "linked_columns": {name: table.column_names for name, table in linked.tables.items()},
        "source_namespace": "main", "prompt_messages_sha256": fingerprint(router.messages),
        "relevant_column_markers_in_prompt": True, "unrelated_column_excluded": True,
        "deterministic_router_calls": router.calls, "harness_llm_calls_reserved": 1,
        "real_llm_calls": 0, "actual_cost_usd": None,
        "artifacts": {"trace": "schema-prompt/workflow-trace.json",
                      "prompt": "schema-prompt/prompt.json"},
        "measurement_scope": "Fixed Harness schema-context wiring with deterministic Router; "
                             "not a model quality or production namespace coverage estimate",
    }
    write_atomic(output_dir / "report.json", report)
    return report
