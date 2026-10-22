"""Source-bound linking, bounded graph expansion and visible degradation."""

import asyncio
import json
from types import SimpleNamespace

import pytest

from app.knowledge.schema_graph import expand_foreign_keys
from app.knowledge.schema_retriever import SchemaRetriever, _build_filtered_schema
from app.models.query import SQR, Entity
from app.nodes.base import NodeInput
from app.nodes.schema_linking import SchemaLinkingNode
from scripts.evaluate_schema_linking import ROOT, FixtureSearch, build_schema, evaluate

DATASET = json.loads((ROOT / "tests/fixtures/schema_linking_eval.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("case", DATASET["cases"], ids=lambda case: case["id"])
async def test_linking_boundary_regression(case):
    result = await evaluate({**DATASET, "cases": [case]})
    assert result["passed"] == 1, result["details"]
    assert result["llm_or_embedding_calls"] == 0


@pytest.mark.parametrize("seeds,expected", [
    (["users", "users", "missing_table"], ["users", "orders"]),
    (["orders"], ["orders", "order_items", "users"]),
    (["products"], ["products", "order_items", "suppliers"]),
    (["users", "products"], ["users", "products", "order_items", "orders", "suppliers"]),
    ([], []),
])
def test_graph_is_one_hop_deterministic_and_does_not_modify_input(seeds, expected):
    schema = build_schema(DATASET)
    original = list(seeds)
    assert expand_foreign_keys(seeds, schema) == expected
    assert expand_foreign_keys(seeds, schema) == expected
    assert seeds == original
    assert "missing_table" not in schema.tables
    assert schema.tables["users"].foreign_keys[0].ref_table == "missing_table"


async def test_invalid_hits_never_reach_result_columns_or_raw_hits():
    schema = build_schema(DATASET)
    search = FixtureSearch({"hits": [
        {"db_id": "fixture", "table_name": "orders", "column_name": "amount"},
        {"db_id": "foreign", "table_name": "users", "column_name": "id"},
        {"db_id": None, "table_name": "users", "column_name": "id"},
        {"db_id": "fixture", "table_name": "orders", "column_name": "stale"},
    ]})
    result = await SchemaRetriever(search).retrieve("opaque", schema, db_id="fixture")
    assert [(hit.table_name, hit.column_name) for hit in result.search_results] == [
        ("orders", "amount")
    ]
    assert [(col.table, col.name) for col in result.columns] == [("orders", "amount")]
    assert result.warnings[0].startswith("SCHEMA_RAG_HITS_REJECTED")
    assert "foreign" not in " ".join(result.warnings)


@pytest.mark.parametrize("namespace", ["main", "other", None])
async def test_known_namespace_rejects_same_database_wrong_schema_hit(namespace):
    schema = build_schema(DATASET)
    schema.tables["orders"].schema_name = "main"

    class Search:
        async def find_relevant_tables(self, *args, **kwargs):
            return [SimpleNamespace(
                db_id="fixture", schema_name=namespace, table_name="orders",
                column_name="amount", rrf_score=1.0,
            )]

    result = await SchemaRetriever(Search()).retrieve("opaque", schema, db_id="fixture")
    assert bool(result.search_results) is (namespace == "main")
    assert bool(result.warnings) is (namespace != "main")
    assert bool(result.columns) is (namespace == "main")


async def test_glossary_column_owner_seeds_one_hop_expansion():
    class Glossary:
        def resolve(self, term):
            return ["amount"] if term == "revenue" else []

    result = await SchemaRetriever(glossary=Glossary()).retrieve(
        "revenue", build_schema(DATASET),
        sqr=SQR(raw_text="revenue", entities=[
            Entity(name="revenue", type="column", normalized="revenue")
        ]),
    )
    assert set(result.tables) == {"orders", "users", "order_items"}
    assert [(col.table, col.name) for col in result.columns] == [("orders", "amount")]


def test_filtered_schema_does_not_invent_unknown_tables():
    filtered = _build_filtered_schema(build_schema(DATASET), ["deleted_table", "orders"])
    assert set(filtered.tables) == set(DATASET["tables"])
    assert filtered.tables["orders"].columns
    assert filtered.tables["products"].columns == []


@pytest.mark.parametrize("at_node", [False, True])
async def test_cancelled_search_is_not_converted_to_keyword_success(at_node):
    class CancelledSearch:
        async def find_relevant_tables(self, *args, **kwargs):
            raise asyncio.CancelledError()

    schema = build_schema(DATASET)
    retriever = SchemaRetriever(CancelledSearch())
    with pytest.raises(asyncio.CancelledError):
        if at_node:
            await SchemaLinkingNode(retriever).execute(NodeInput(
                "users", context={"schema": schema}, config={"db_id": "fixture"},
            ))
        else:
            await retriever.retrieve("users", schema, db_id="fixture")


async def test_custom_retriever_exception_falls_back_without_private_details():
    class BrokenRetriever:
        async def retrieve(self, **kwargs):
            raise RuntimeError("private fixture backend text")

    output = await SchemaLinkingNode(BrokenRetriever()).execute(NodeInput(
        "users", context={"schema": build_schema(DATASET)}, config={"db_id": "fixture"},
    ))
    assert set(output.context["linked_tables"]) == {"users", "orders"}
    assert output.metadata["method"] == "keyword_match"
    assert output.context["schema_linking_warnings"] == output.metadata["warnings"]
    assert "retrieval failed" in output.metadata["warnings"][0]
    assert "private fixture backend text" not in str(output)
