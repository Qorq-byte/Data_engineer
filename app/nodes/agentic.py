"""AgenticNode — enhanced base node for LLM-powered nodes.

See SPEC §4.12.3 for the 7 built-in capabilities.

AgenticNode injects 7 harness-provided capabilities into every LLM node:
  ① AdvancedSQLiteSession   — memory management (per node_name isolation)
  ② ToolRegistry            — func tools + MCP tools
  ③ PermissionManager       — allow / deny / ask
  ④ SkillManager            — skill loading and execution
  ⑤ ActionHistoryManager    — I/O trace logging
  ⑥ Auto-compaction         — context compression at 90% token threshold
  ⑦ Streaming support       — SSE streaming for intermediate tokens

All capabilities are initialized lazily on first property access to keep
node construction cheap and avoid circular imports during startup.
"""

from dataclasses import dataclass, field
from typing import Any

from app.nodes.base import BaseNode, NodeInput


@dataclass
class AgenticConfig:
    """Configuration for AgenticNode's 7 capabilities."""

    session_db_path: str = ":memory:"
    session_ttl: int = 3600
    func_tools: list[Any] = field(default_factory=list)
    mcp_servers: list[Any] = field(default_factory=list)
    permission_rules: list[dict] = field(default_factory=list)
    default_permission: str = "deny"  # "allow", "deny", "ask"
    skills_dir: str = "./skills"
    max_action_history: int = 100
    enable_auto_compaction: bool = True
    compaction_max_tokens: int = 32000
    enable_streaming: bool = True
    node_name: str = ""  # used for memory isolation key


class AgenticNode(BaseNode):
    """Base class for all LLM-powered nodes.

    Subclasses (GenerateSQLNode, SchemaLinkingNode, etc.) inherit
    session, tool, permission, skill, history, compaction, and streaming
    capabilities without implementing them manually.
    """

    def __init__(self, config: AgenticConfig | None = None):
        self.agentic_config = config or AgenticConfig()

        # These are initialized lazily to avoid circular imports
        # with harness modules during startup.
        self._session: Any = None
        self._tool_registry: Any = None
        self._permission_manager: Any = None
        self._skill_manager: Any = None
        self._action_history: Any = None
        self._compactor: Any = None

    # ── Lazy accessors (initialize on first use) ──────────────

    @property
    def session(self) -> Any:
        if self._session is None:
            self._session = self._init_session()
        return self._session

    @property
    def tool_registry(self) -> Any:
        if self._tool_registry is None:
            self._tool_registry = self._init_tool_registry()
        return self._tool_registry

    @property
    def permission_manager(self) -> Any:
        if self._permission_manager is None:
            self._permission_manager = self._init_permission_manager()
        return self._permission_manager

    @property
    def skill_manager(self) -> Any:
        if self._skill_manager is None:
            self._skill_manager = self._init_skill_manager()
        return self._skill_manager

    @property
    def action_history(self) -> Any:
        if self._action_history is None:
            self._action_history = self._init_action_history()
        return self._action_history

    @property
    def compactor(self) -> Any:
        if self._compactor is None:
            self._compactor = self._init_compactor()
        return self._compactor

    # ── Initializers ──────────────────────────────────────────

    def _init_session(self) -> Any:
        """① Initialize AdvancedSQLiteSession (isolated by node_name)."""
        cfg = self.agentic_config
        try:
            from app.harness.session import AdvancedSQLiteSession

            return AdvancedSQLiteSession(
                db_path=cfg.session_db_path,
                session_ttl=cfg.session_ttl,
                node_name=cfg.node_name or self.name,
            )
        except ImportError:
            return None

    def _init_tool_registry(self) -> Any:
        """② Initialize ToolRegistry with configured func tools + MCP servers."""
        cfg = self.agentic_config
        try:
            from app.harness.tool_registry import ToolRegistry

            registry = ToolRegistry()
            for tool in cfg.func_tools:
                name = getattr(tool, "name", None) or getattr(
                    tool, "__name__", str(tool)
                )
                description = getattr(tool, "description", "") or (
                    getattr(tool, "__doc__", "") or ""
                )
                registry.register(name, tool, description=description)
            for server in cfg.mcp_servers:
                server_name = getattr(server, "name", str(server))
                tools = getattr(server, "tools", None)
                if isinstance(tools, dict):
                    registry.register_mcp_server(server_name, tools)
            return registry
        except ImportError:
            return None

    def _init_permission_manager(self) -> Any:
        """③ Initialize PermissionManager."""
        cfg = self.agentic_config
        try:
            from app.harness.permission import PermissionManager

            return PermissionManager(
                rules=cfg.permission_rules,
                default_level=cfg.default_permission,
            )
        except ImportError:
            return None

    def _init_skill_manager(self) -> Any:
        """④ Initialize SkillManager (skills discovered from skills_dir)."""
        cfg = self.agentic_config
        try:
            from app.harness.skill_manager import SkillManager

            manager = SkillManager(skills_dir=cfg.skills_dir)
            manager.discover()
            return manager
        except ImportError:
            return None

    def _init_action_history(self) -> Any:
        """⑤ Initialize ActionHistoryManager (ring buffer)."""
        cfg = self.agentic_config
        try:
            from app.harness.action_history import ActionHistoryManager

            return ActionHistoryManager(max_entries=cfg.max_action_history)
        except ImportError:
            return None

    def _init_compactor(self) -> Any:
        """⑥ Initialize ContextCompactor (90% token threshold)."""
        cfg = self.agentic_config
        try:
            from app.harness.compaction import ContextCompactor

            return ContextCompactor(max_tokens=cfg.compaction_max_tokens)
        except ImportError:
            return None

    # ── Enhanced setup_input ──────────────────────────────────

    async def setup_input(self, raw_input: dict[str, Any]) -> NodeInput:
        """Enhanced setup: base validation + permission pre-check + compaction.

        Before executing, this method:
          1. Runs base schema validation and defaults (via super()).
          2. Checks tool permissions and records denied tools in context
             (pre-check to fail fast — subclasses and the WorkflowRunner
             can inspect ``context["denied_tools"]``).
          3. Auto-compacts context if at/above the 90% token threshold.
        """
        node_input = await super().setup_input(raw_input)

        # ③ Permission pre-check: surface denied tools before execution
        pm = self.permission_manager
        if pm is not None:
            denied = [
                tool_name
                for tool_name in node_input.config.get("tools", [])
                if pm.check(tool_name, node_input.context) == "deny"
            ]
            if denied:
                node_input.context["denied_tools"] = denied

        # ⑥ Auto-compaction: if context is at/above the 90% token threshold
        if (
            self.agentic_config.enable_auto_compaction
            and self._token_usage(node_input.context) >= 0.90
        ):
            compactor = self.compactor
            if compactor is not None:
                node_input.context = compactor.compact(
                    node_input.context,
                    keep_keys=["query_text", "session_id", "sql", "primary_sql"],
                )

        return node_input

    def _token_usage(self, context: dict[str, Any] | None = None) -> float:
        """Estimate current token usage ratio (0.0 - 1.0) for a context."""
        if context is None:
            return 0.0
        compactor = self.compactor
        if compactor is None:
            return 0.0
        return compactor.usage_ratio(context)

    # ── Helpers for subclasses ────────────────────────────────

    def _is_tool_allowed(self, tool_name: str, context: dict) -> bool:
        """Check if a tool is allowed in the current context."""
        pm = self.permission_manager
        if pm is None:
            return True  # No permission manager = allow all
        return pm.check(tool_name, context) == "allow"

    def record_action(self, input_summary: str) -> int:
        """⑤ Record the start of an action; returns the entry index."""
        history = self.action_history
        if history is None:
            return -1
        return history.record_start(
            self.agentic_config.node_name or self.name, input_summary
        )

    def finish_action(
        self, index: int, output_summary: str = "", error: str | None = None
    ) -> None:
        """⑤ Record the completion of a previously started action."""
        history = self.action_history
        if history is not None and index >= 0:
            history.record_end(index, output_summary=output_summary, error=error)
