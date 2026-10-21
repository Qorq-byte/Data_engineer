"""SQL scope, generation envelopes, mandatory policy and evaluator taxonomy."""

import asyncio
import json
from types import SimpleNamespace

import pytest

from app.core.sql_generator import SQLGenerator
from app.core.sql_validator import SQLValidator
from app.llm.router import LiteLLMRouter, RouterConfig
from app.models.query import SQR
from app.models.schema import ColumnSchema, SchemaSnapshot, TableSchema
from app.nodes.base import NodeInput
from app.nodes.validate_sql import ValidateSQLNode
from scripts.evaluate_quality import evaluate_sql
from scripts.evaluate_sql_validation import ROOT, evaluate

DATASET = json.loads((ROOT / "tests/fixtures/sql_validation_eval.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("case", DATASET["cases"], ids=lambda case: case["id"])
async def test_sql_scope_and_policy_contract(case):
    result = await evaluate({**DATASET, "cases": [case]})
    assert result["passed"] == 1, result["details"]


@pytest.mark.parametrize("skip", [False, True])
async def test_skip_syntax_cannot_disable_read_policy(skip):
    report = await SQLValidator().validate("SELECT nextval('private_sequence')", skip_syntax=skip)
    assert not report.passed
    assert any("READ_ONLY_ERROR" in error for error in report.syntax_errors)
    assert (await SQLValidator().validate("SELECT 1", skip_syntax=skip)).passed


async def test_postgres_quoted_identifier_case_is_preserved():
    schema = SchemaSnapshot("postgresql", "fixture", tables={
        "Users": TableSchema("Users", columns=[ColumnSchema("ID", "INTEGER")])
    })
    validator = SQLValidator("postgres")
    assert (await validator.validate('SELECT "ID" FROM "Users"', schema)).passed
    assert not (await validator.validate('SELECT id FROM "Users"', schema)).passed
    assert not (await validator.validate('SELECT "ID" FROM users', schema)).passed


@pytest.mark.parametrize("dialect,sql", [
    ("mysql", "SELECT `id` FROM `users`"),
    ("mysql", "SELECT `id` FROM `fixture`.`users`"),
    ("postgres", 'SELECT "id" FROM "users"'),
    ("sqlite", "SELECT ID FROM USERS"),
])
async def test_basic_dialect_reference_parsing(dialect, sql):
    schema = SchemaSnapshot(dialect, "fixture", tables={
        "users": TableSchema("users", columns=[ColumnSchema("id", "INTEGER")])
    })
    assert (await SQLValidator(dialect).validate(sql, schema)).passed


async def test_validation_exception_clears_stale_success_context():
    class BrokenValidator(SQLValidator):
        async def validate(self, *args, **kwargs):
            raise RuntimeError("fixture validation failure")

    node = ValidateSQLNode(validator=BrokenValidator())
    output = await node.execute(NodeInput("SELECT 1", context={"validation_passed": True}))
    shared = await node.update_context(
        output, {"validation_passed": True, "validation_report": "old"}
    )
    assert output.errors
    assert shared["validation_passed"] is False
    assert shared["validation_report"] is None


class ScriptedRouter(LiteLLMRouter):
    def __init__(self, responses):
        super().__init__(RouterConfig(), mock_mode=True)
        self.responses = iter(responses)

    async def complete(self, messages, **kwargs):
        response = next(self.responses)
        if isinstance(response, BaseException):
            raise response
        return response


@pytest.mark.parametrize("strategy", ["temperature", "multi_perspective"])
@pytest.mark.parametrize("bad", [
    {"choices": None}, {"choices": "wrong"}, {"choices": [{}]},
    {"choices": [{"message": None}]},
    {"choices": [{"message": {"content": None}}]},
    {"choices": [{"message": {"content": ["not", "SQL", "text"]}}]},
    {"choices": [{"message": {"content": {"sql": "SELECT 9"}}}]},
])
async def test_malformed_generation_choice_does_not_destroy_valid_sibling(strategy, bad):
    valid = {"choices": [{"message": {"content": "SELECT 1"}}]}
    candidates = await SQLGenerator(ScriptedRouter([bad, valid])).generate(
        SQR(raw_text="fixture"), num_candidates=2, strategy=strategy,
    )
    assert [candidate.sql_text for candidate in candidates] == ["SELECT 1"]
    assert candidates[0].generation_mode == "primary"


@pytest.mark.parametrize("strategy", ["temperature", "multi_perspective"])
async def test_all_invalid_generation_choices_fail(strategy):
    with pytest.raises(RuntimeError, match="All SQL generation attempts failed"):
        await SQLGenerator(ScriptedRouter([{}, {}])).generate(
            SQR(raw_text="fixture"), num_candidates=2, strategy=strategy,
        )


@pytest.mark.parametrize("strategy", ["temperature", "multi_perspective"])
async def test_cancelled_generation_child_propagates(strategy):
    with pytest.raises(asyncio.CancelledError):
        await SQLGenerator(ScriptedRouter([
            asyncio.CancelledError(), {"choices": [{"message": {"content": "SELECT 1"}}]},
        ])).generate(SQR(raw_text="fixture"), num_candidates=2, strategy=strategy)


async def test_legacy_completion_object_still_yields_text():
    response = SimpleNamespace(choices=[
        SimpleNamespace(message=SimpleNamespace(content="SELECT 1"))
    ])
    candidates = await SQLGenerator(ScriptedRouter([response])).generate(SQR(raw_text="fixture"))
    assert candidates[0].sql_text == "SELECT 1"


@pytest.mark.parametrize("prediction,code", [
    (None, "MISSING_PREDICTION"),
    ({"sql": ""}, "EMPTY_SQL"),
    ({"sql": "DELETE FROM orders"}, "SQL_POLICY_REJECTED"),
    ({"sql": "SELECT missing FROM orders"}, "SQL_EXECUTION_ERROR"),
    ({"sql": "SELECT 2"}, "RESULT_MISMATCH"),
    ({"sql": "WITH RECURSIVE x(n) AS (SELECT 1 UNION ALL SELECT n+1 FROM x WHERE n<1002) "
             "SELECT n FROM x"}, "SQL_RESULT_LIMIT"),
])
def test_sql_evaluation_failure_codes(prediction, code):
    predictions = [] if prediction is None else [{"id": "case", **prediction}]
    result = evaluate_sql([{"id": "case", "gold_sql": "SELECT 1"}], predictions)
    assert result["details"][0]["error_code"] == code
    assert result["error_counts"] == {code: 1}


@pytest.mark.parametrize("gold", ["DELETE FROM orders", "SELECT missing FROM orders"])
def test_bad_gold_is_dataset_error(gold):
    with pytest.raises(ValueError, match="Invalid gold SQL"):
        evaluate_sql([{"id": "case", "gold_sql": gold}], [])


def test_evaluation_timeout_then_next_case_recovers():
    cases = [{"id": "slow", "gold_sql": "SELECT 1"}, {"id": "ok", "gold_sql": "SELECT 1"}]
    predictions = [
        {"id": "slow", "sql": "WITH RECURSIVE x(n) AS (SELECT 1 UNION ALL SELECT n+1 FROM x "
         "WHERE n<10000000) SELECT SUM(n) FROM x"},
        {"id": "ok", "sql": "SELECT 1"},
    ]
    result = evaluate_sql(cases, predictions, timeout_seconds=0.05)
    assert result["details"][0]["error_code"] == "SQL_TIMEOUT"
    assert result["details"][1]["result_correct"]
