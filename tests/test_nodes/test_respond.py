"""Tests for RespondNode — template mode, LLM mode, context updates, i18n."""

from __future__ import annotations

import pytest

from app.llm.router import LiteLLMRouter, RouterConfig
from app.models.query import ValidationReport
from app.nodes.base import NodeInput
from app.nodes.respond import RespondNode

# ── Fixtures / helpers ─────────────────────────────────────────────────


EXECUTION_RESULT = {
    "columns": ["id", "name", "total"],
    "rows": [
        [1, "Alice", 120.5],
        [2, "Bob", 88.0],
        [3, "Carol", 42.0],
        [4, "Dave", 17.5],
        [5, "Eve", 9.9],
        [6, "Frank", 3.3],
        [7, "Grace", 1.1],
    ],
    "row_count": 7,
    "truncated": False,
}


def _input(
    context: dict | None = None,
    config: dict | None = None,
    query_text: str = "统计每个用户的订单总额",
) -> NodeInput:
    return NodeInput(query_text=query_text, context=context or {}, config=config or {})


class FakeRouter:
    """Deterministic router double recording calls (see tests/test_llm/ for style)."""

    def __init__(self, content: str = "这是 LLM 生成的回复。", error: Exception | None = None):
        self.content = content
        self.error = error
        self.calls: list[dict] = []

    async def complete(self, messages, model=None, **kwargs):
        self.calls.append({"messages": messages, "model": model})
        if self.error is not None:
            raise self.error
        return {
            "choices": [{"message": {"role": "assistant", "content": self.content}}],
            "model": model or "fake-model",
        }


@pytest.fixture
def node() -> RespondNode:
    return RespondNode()


# ═══════════════════════════════════════════════════════════════════════════
# Node identity
# ═══════════════════════════════════════════════════════════════════════════


class TestNodeIdentity:
    def test_name_and_description(self, node):
        assert node.name == "respond"
        assert node.description

    def test_is_agentic_node(self, node):
        from app.nodes.agentic import AgenticNode

        assert isinstance(node, AgenticNode)


# ═══════════════════════════════════════════════════════════════════════════
# Template mode (no router)
# ═══════════════════════════════════════════════════════════════════════════


class TestTemplateResponse:
    async def test_with_execution_result(self, node):
        output = await node.execute(
            _input(context={"primary_sql": "SELECT 1", "last_execution": EXECUTION_RESULT})
        )
        assert output.errors == []
        assert output.result["used_llm"] is False
        text = output.result["response"]
        assert "7" in text  # row count
        assert "SELECT 1" in text

    async def test_preview_limited_to_five_rows(self, node):
        output = await node.execute(
            _input(context={"sql": "SELECT 1", "last_execution": EXECUTION_RESULT})
        )
        text = output.result["response"]
        assert "Alice" in text
        assert "Eve" in text
        assert "Frank" not in text  # row 6 not in default 5-row preview

    async def test_preview_rows_configurable(self, node):
        output = await node.execute(
            _input(
                context={"sql": "SELECT 1", "last_execution": EXECUTION_RESULT},
                config={"preview_rows": 2},
            )
        )
        text = output.result["response"]
        assert "Bob" in text
        assert "Carol" not in text

    async def test_no_sql_no_result(self, node):
        output = await node.execute(_input(context={}))
        assert output.errors == []
        assert "未生成 SQL" in output.result["response"]
        assert output.result["used_llm"] is False

    async def test_sql_but_not_executed(self, node):
        output = await node.execute(_input(context={"primary_sql": "SELECT count(*) FROM users"}))
        text = output.result["response"]
        assert "尚未执行" in text
        assert "SELECT count(*) FROM users" in text

    async def test_execution_result_fallback_key(self, node):
        """execution_result is accepted as a fallback for last_execution."""
        output = await node.execute(
            _input(context={"sql": "SELECT 1", "execution_result": EXECUTION_RESULT})
        )
        assert "7" in output.result["response"]
        assert output.metadata["has_result"] is True

    async def test_truncated_result_noted(self, node):
        execution = {**EXECUTION_RESULT, "truncated": True}
        output = await node.execute(
            _input(context={"sql": "SELECT 1", "last_execution": execution})
        )
        assert "截断" in output.result["response"]

    async def test_validation_passed_mentioned(self, node):
        report = ValidationReport(passed=True, syntax_ok=True)
        output = await node.execute(
            _input(
                context={
                    "sql": "SELECT 1",
                    "last_execution": EXECUTION_RESULT,
                    "validation_report": report,
                }
            )
        )
        assert "验证通过" in output.result["response"]

    async def test_validation_failed_with_errors(self, node):
        report = ValidationReport(
            passed=False,
            syntax_ok=False,
            syntax_errors=["near 'FORM': syntax error"],
        )
        output = await node.execute(
            _input(context={"sql": "SELECT * FORM users", "validation_report": report})
        )
        text = output.result["response"]
        assert "验证未通过" in text
        assert "near 'FORM'" in text

    async def test_validation_report_as_dict(self, node):
        report = {"passed": False, "schema_errors": ["unknown table: orderz"]}
        output = await node.execute(
            _input(context={"sql": "SELECT * FROM orderz", "validation_report": report})
        )
        text = output.result["response"]
        assert "验证未通过" in text
        assert "orderz" in text

    async def test_never_raises_on_garbage_context(self, node):
        output = await node.execute(
            _input(context={"last_execution": {}, "validation_report": object()})
        )
        assert isinstance(output.result["response"], str)
        assert output.result["response"]


# ═══════════════════════════════════════════════════════════════════════════
# Language handling
# ═══════════════════════════════════════════════════════════════════════════


class TestLanguage:
    async def test_default_language_is_chinese(self, node):
        output = await node.execute(
            _input(context={"sql": "SELECT 1", "last_execution": EXECUTION_RESULT})
        )
        assert "查询已完成" in output.result["response"]
        assert output.metadata["language"] == "zh"

    async def test_english_via_context(self, node):
        output = await node.execute(
            _input(
                context={
                    "language": "en",
                    "sql": "SELECT 1",
                    "last_execution": EXECUTION_RESULT,
                }
            )
        )
        text = output.result["response"]
        assert "Query completed" in text
        assert output.metadata["language"] == "en"

    async def test_english_no_sql_message(self, node):
        output = await node.execute(_input(context={"language": "en"}))
        assert "No SQL was generated" in output.result["response"]

    async def test_config_language_overrides_context(self, node):
        output = await node.execute(
            _input(
                context={"language": "zh", "sql": "SELECT 1"},
                config={"language": "en"},
            )
        )
        assert output.metadata["language"] == "en"

    async def test_sqr_language_fallback(self, node):
        output = await node.execute(
            _input(context={"sqr_language": "en", "sql": "SELECT 1"})
        )
        assert output.metadata["language"] == "en"


# ═══════════════════════════════════════════════════════════════════════════
# LLM mode (router injected)
# ═══════════════════════════════════════════════════════════════════════════


class TestLLMResponse:
    async def test_router_used_for_response(self):
        router = FakeRouter(content="共有 7 个用户，总额最高的是 Alice。")
        node = RespondNode(router=router)
        output = await node.execute(
            _input(context={"sql": "SELECT 1", "last_execution": EXECUTION_RESULT})
        )
        assert output.result["used_llm"] is True
        assert output.result["response"] == "共有 7 个用户，总额最高的是 Alice。"
        assert len(router.calls) == 1

    async def test_prompt_contains_question_sql_and_preview(self):
        router = FakeRouter()
        node = RespondNode(router=router)
        report = ValidationReport(passed=True)
        await node.execute(
            _input(
                context={
                    "sql": "SELECT name FROM users",
                    "last_execution": EXECUTION_RESULT,
                    "validation_report": report,
                },
                query_text="列出所有用户",
            )
        )
        user_msg = router.calls[0]["messages"][-1]["content"]
        assert "列出所有用户" in user_msg
        assert "SELECT name FROM users" in user_msg
        assert "Alice" in user_msg
        assert "passed" in user_msg

    async def test_model_config_forwarded(self):
        router = FakeRouter()
        node = RespondNode(router=router)
        await node.execute(_input(context={"sql": "SELECT 1"}, config={"model": "gpt-4.1"}))
        assert router.calls[0]["model"] == "gpt-4.1"

    async def test_router_error_falls_back_to_template(self):
        router = FakeRouter(error=RuntimeError("provider down"))
        node = RespondNode(router=router)
        output = await node.execute(
            _input(context={"sql": "SELECT 1", "last_execution": EXECUTION_RESULT})
        )
        assert output.result["used_llm"] is False
        assert "查询已完成" in output.result["response"]

    async def test_empty_llm_content_falls_back_to_template(self):
        router = FakeRouter(content="   ")
        node = RespondNode(router=router)
        output = await node.execute(_input(context={"sql": "SELECT 1"}))
        assert output.result["used_llm"] is False
        assert output.result["response"]

    async def test_with_real_litellm_mock_mode(self):
        """Integration with LiteLLMRouter in mock_mode (see tests/test_llm/)."""
        router = LiteLLMRouter(RouterConfig(providers={}), mock_mode=True)
        node = RespondNode(router=router)
        output = await node.execute(
            _input(context={"sql": "SELECT 1", "last_execution": EXECUTION_RESULT})
        )
        assert output.result["used_llm"] is True
        assert "mock response" in output.result["response"]

    async def test_english_system_prompt_when_english(self):
        router = FakeRouter(content="Done.")
        node = RespondNode(router=router)
        await node.execute(_input(context={"language": "en", "sql": "SELECT 1"}))
        system_msg = router.calls[0]["messages"][0]["content"]
        assert "English" in system_msg


# ═══════════════════════════════════════════════════════════════════════════
# Output contract / update_context
# ═══════════════════════════════════════════════════════════════════════════


class TestOutputContract:
    async def test_result_shape(self, node):
        output = await node.execute(_input(context={"sql": "SELECT 1"}))
        assert set(output.result.keys()) == {"response", "used_llm"}
        assert isinstance(output.result["response"], str)
        assert isinstance(output.result["used_llm"], bool)

    async def test_update_context_writes_response(self, node):
        output = await node.execute(
            _input(context={"sql": "SELECT 1", "last_execution": EXECUTION_RESULT})
        )
        shared = {"sql": "SELECT 1", "other": "keep-me"}
        merged = await node.update_context(output, shared)
        assert merged["response"] == output.result["response"]
        assert merged["other"] == "keep-me"

    async def test_metadata_flags(self, node):
        output = await node.execute(_input(context={}))
        assert output.metadata["status"] == "success"
        assert output.metadata["has_sql"] is False
        assert output.metadata["has_result"] is False
        assert output.metadata["used_llm"] is False

    async def test_setup_input_roundtrip(self, node):
        """RespondNode works through the standard 3-step lifecycle."""
        raw = {
            "query_text": "查一下订单",
            "context": {"sql": "SELECT * FROM orders", "last_execution": EXECUTION_RESULT},
            "config": {"preview_rows": 1},
        }
        node_input = await node.setup_input(raw)
        output = await node.execute(node_input)
        merged = await node.update_context(output, node_input.context)
        assert "response" in merged
        assert "Alice" in merged["response"]
        assert "Bob" not in merged["response"]
