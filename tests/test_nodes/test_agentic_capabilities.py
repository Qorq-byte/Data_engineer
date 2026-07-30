"""Tests for AgenticNode's 7 wired harness capabilities.

Covers lazy initialization and behavior of:
  ① AdvancedSQLiteSession  ② ToolRegistry  ③ PermissionManager
  ④ SkillManager  ⑤ ActionHistoryManager  ⑥ Auto-compaction  ⑦ Streaming flag
"""

from app.harness.action_history import ActionHistoryManager
from app.harness.compaction import ContextCompactor
from app.harness.permission import PermissionManager
from app.harness.session import AdvancedSQLiteSession
from app.harness.skill_manager import SkillManager
from app.harness.tool_registry import ToolRegistry
from app.nodes.agentic import AgenticConfig, AgenticNode
from app.nodes.base import NodeInput, NodeOutput


class ProbeNode(AgenticNode):
    """Minimal concrete AgenticNode for capability testing."""

    name = "probe"
    description = "test probe node"

    async def execute(self, input: NodeInput) -> NodeOutput:
        return NodeOutput(result="ok")


# ── ① Session ──────────────────────────────────────────────────────


class TestSessionCapability:
    def test_session_is_advanced_sqlite_session(self):
        node = ProbeNode()
        assert isinstance(node.session, AdvancedSQLiteSession)

    def test_session_isolated_by_node_name(self):
        node = ProbeNode(AgenticConfig(node_name="custom_iso"))
        assert node.session.node_name == "custom_iso"

    def test_session_defaults_to_class_name(self):
        node = ProbeNode()
        assert node.session.node_name == "probe"

    def test_session_save_and_get_turns(self):
        node = ProbeNode()
        node.session.save_turn("s1", {"q": "hello"})
        assert node.session.get_turns("s1") == [{"q": "hello"}]

    def test_session_lazy_and_cached(self):
        node = ProbeNode()
        assert node._session is None
        first = node.session
        assert node.session is first


# ── ② Tool registry ────────────────────────────────────────────────


class TestToolRegistryCapability:
    def test_tool_registry_instance(self):
        node = ProbeNode()
        assert isinstance(node.tool_registry, ToolRegistry)

    def test_func_tools_registered(self):
        def my_tool():
            """Does a thing."""
            return 42

        node = ProbeNode(AgenticConfig(func_tools=[my_tool]))
        assert "my_tool" in node.tool_registry
        assert node.tool_registry.call("my_tool") == 42

    def test_named_tool_object_registered(self):
        class Tool:
            name = "fancy"
            description = "fancy tool"

            def __call__(self):
                return "fancy!"

        node = ProbeNode(AgenticConfig(func_tools=[Tool()]))
        entry = node.tool_registry.get("fancy")
        assert entry is not None
        assert entry.description == "fancy tool"

    def test_empty_registry_by_default(self):
        node = ProbeNode()
        assert len(node.tool_registry) == 0


# ── ③ Permission manager ───────────────────────────────────────────


class TestPermissionCapability:
    def test_permission_manager_instance(self):
        node = ProbeNode()
        assert isinstance(node.permission_manager, PermissionManager)

    def test_default_deny(self):
        node = ProbeNode()
        assert node._is_tool_allowed("anything", {}) is False

    def test_allow_rule(self):
        node = ProbeNode(
            AgenticConfig(permission_rules=[{"execute_read_query": "allow"}])
        )
        assert node._is_tool_allowed("execute_read_query", {}) is True

    async def test_setup_input_records_denied_tools(self):
        node = ProbeNode(
            AgenticConfig(permission_rules=[{"good_tool": "allow"}])
        )
        node_input = await node.setup_input(
            {
                "query_text": "q",
                "context": {},
                "config": {"tools": ["good_tool", "bad_tool"]},
            }
        )
        assert node_input.context["denied_tools"] == ["bad_tool"]

    async def test_setup_input_no_denied_key_when_all_allowed(self):
        node = ProbeNode(AgenticConfig(permission_rules=[{"t1": "allow"}]))
        node_input = await node.setup_input(
            {"query_text": "q", "context": {}, "config": {"tools": ["t1"]}}
        )
        assert "denied_tools" not in node_input.context


# ── ④ Skill manager ────────────────────────────────────────────────


class TestSkillCapability:
    def test_skill_manager_instance(self):
        node = ProbeNode()
        assert isinstance(node.skill_manager, SkillManager)

    def test_skills_discovered_from_dir(self, tmp_path):
        skill_file = tmp_path / "explorer.yaml"
        skill_file.write_text(
            "name: explorer\ndescription: explores\ninstructions: do it\n",
            encoding="utf-8",
        )
        node = ProbeNode(AgenticConfig(skills_dir=str(tmp_path)))
        assert "explorer" in [s.name for s in node.skill_manager.list_all()]

    def test_missing_skills_dir_tolerated(self, tmp_path):
        node = ProbeNode(AgenticConfig(skills_dir=str(tmp_path / "nope")))
        assert node.skill_manager.list_all() == []


# ── ⑤ Action history ───────────────────────────────────────────────


class TestActionHistoryCapability:
    def test_action_history_instance(self):
        node = ProbeNode()
        assert isinstance(node.action_history, ActionHistoryManager)

    def test_record_and_finish_action(self):
        node = ProbeNode()
        idx = node.record_action("input xyz")
        node.finish_action(idx, output_summary="output abc")
        entry = node.action_history.last()
        assert entry is not None
        assert entry.node_name == "probe"
        assert entry.input_summary == "input xyz"
        assert entry.output_summary == "output abc"
        assert entry.completed_at is not None

    def test_ring_buffer_max_entries(self):
        node = ProbeNode(AgenticConfig(max_action_history=3))
        for i in range(5):
            node.record_action(f"action {i}")
        assert len(node.action_history) == 3


# ── ⑥ Auto-compaction ──────────────────────────────────────────────


class TestCompactionCapability:
    def test_compactor_instance(self):
        node = ProbeNode()
        assert isinstance(node.compactor, ContextCompactor)

    def test_token_usage_empty_context(self):
        node = ProbeNode()
        assert node._token_usage({}) == 0.0

    def test_token_usage_no_context_backward_compat(self):
        node = ProbeNode()
        assert node._token_usage() == 0.0

    def test_token_usage_grows_with_content(self):
        node = ProbeNode(AgenticConfig(compaction_max_tokens=100))
        assert node._token_usage({"k": "word " * 200}) > 0.5

    async def test_setup_input_compacts_oversized_context(self):
        node = ProbeNode(AgenticConfig(compaction_max_tokens=200))
        big_value = "lorem ipsum dolor " * 500
        node_input = await node.setup_input(
            {
                "query_text": "q",
                "context": {"schema_text": big_value, "sql": "SELECT 1"},
                "config": {},
            }
        )
        # Oversized value was compacted; protected key survived verbatim
        assert len(node_input.context["schema_text"]) < len(big_value)
        assert node_input.context["sql"] == "SELECT 1"

    async def test_setup_input_skips_compaction_when_disabled(self):
        node = ProbeNode(
            AgenticConfig(
                compaction_max_tokens=200, enable_auto_compaction=False
            )
        )
        big_value = "lorem ipsum dolor " * 500
        node_input = await node.setup_input(
            {"query_text": "q", "context": {"blob": big_value}, "config": {}}
        )
        assert node_input.context["blob"] == big_value

    async def test_setup_input_small_context_untouched(self):
        node = ProbeNode()
        node_input = await node.setup_input(
            {"query_text": "q", "context": {"a": "small"}, "config": {}}
        )
        assert node_input.context == {"a": "small"}


# ── ⑦ Streaming flag ───────────────────────────────────────────────


class TestStreamingCapability:
    def test_streaming_enabled_by_default(self):
        node = ProbeNode()
        assert node.agentic_config.enable_streaming is True

    def test_streaming_can_be_disabled(self):
        node = ProbeNode(AgenticConfig(enable_streaming=False))
        assert node.agentic_config.enable_streaming is False
