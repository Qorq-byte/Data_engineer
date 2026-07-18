"""agent.yml config loader — loads, validates, and hot-reloads agent configuration.

See SPEC §3.4.5 for the full agent.yml schema.
"""

import os
import time
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class ProviderConfig:
    default: str = "claude-sonnet-4"
    fallback: list[str] = field(default_factory=list)
    litellm_config: str = ""


@dataclass
class NodeAgentConfig:
    model: str = "none"
    tools: list[str] = field(default_factory=list)
    permissions: dict[str, str] = field(default_factory=dict)
    skills: list[str] = field(default_factory=list)
    streaming: bool = False


@dataclass
class SkillsConfig:
    mode: str = "auto"
    directory: str = "./skills"
    preload: list[str] = field(default_factory=list)


@dataclass
class WorkflowBindingConfig:
    default: str = "gensql_agentic"
    auto_select: bool = False


@dataclass
class BuiltinSubagentsMemory:
    gen_sql: bool = False
    schema_linking: bool = False
    validate_sql: bool = False
    execute_sql: bool = False
    reflection: bool = False


@dataclass
class UserSubagentsMemory:
    default: bool = True


@dataclass
class StorageMemory:
    subagent: str = ":memory:"
    top_level: str = "~/.data_engineer/sessions/"


@dataclass
class MemoryConfig:
    builtin_subagents: BuiltinSubagentsMemory = field(
        default_factory=BuiltinSubagentsMemory
    )
    chat: bool = True
    user_subagents: UserSubagentsMemory = field(default_factory=UserSubagentsMemory)
    storage: StorageMemory = field(default_factory=StorageMemory)
    isolation: str = "node_name"


@dataclass
class GlobalContextConfig:
    source: str = "AGENTS.md"
    inject_lines: int = 200
    auto_reload: bool = True


@dataclass
class ConstraintsConfig:
    max_llm_calls_per_query: int = 10
    max_node_retries: int = 3
    reflection_max_rounds: int = 3
    reflection_on_exhausted: str = "force_output"
    session_ttl_seconds: int = 3600
    read_only: bool = True
    max_result_rows: int = 1000
    statement_timeout_ms: int = 30000


@dataclass
class AgentConfig:
    name: str = ""
    version: str = ""
    provider: ProviderConfig = field(default_factory=ProviderConfig)
    nodes: dict[str, NodeAgentConfig] = field(default_factory=dict)
    skills: SkillsConfig = field(default_factory=SkillsConfig)
    workflow: WorkflowBindingConfig = field(default_factory=WorkflowBindingConfig)
    memory: MemoryConfig = field(default_factory=MemoryConfig)
    global_context: GlobalContextConfig = field(default_factory=GlobalContextConfig)
    constraints: ConstraintsConfig = field(default_factory=ConstraintsConfig)


class ConfigLoader:
    """Loads and manages agent.yml configuration.

    Supports hot-reload: when file mtime changes, reloads automatically.
    """

    def __init__(self, path: str = "app/config/agent.yml"):
        self.path = Path(path)
        self._config: AgentConfig | None = None
        self._last_loaded: float = 0
        self._last_mtime: float = 0

    def load(self, force: bool = False) -> AgentConfig:
        """Load agent.yml, with hot-reload if file has changed.

        Args:
            force: If True, skip mtime check and always reload.

        Returns:
            Parsed and validated AgentConfig.
        """
        if not self.path.exists():
            raise FileNotFoundError(f"Agent config not found: {self.path}")

        current_mtime = os.path.getmtime(self.path)
        if not force and self._config is not None and current_mtime <= self._last_mtime:
            return self._config

        import yaml

        with open(self.path, encoding="utf-8") as f:
            raw = yaml.safe_load(f)

        agent_raw = raw.get("agent", raw)
        self._config = self._parse(agent_raw)
        self._last_mtime = current_mtime
        self._last_loaded = time.time()
        return self._config

    def get_node_config(self, node_name: str) -> NodeAgentConfig:
        """Get LLM model, tools, and permissions for a specific node."""
        config = self.load()
        return config.nodes.get(
            node_name,
            NodeAgentConfig(model="none", tools=[], permissions={}),
        )

    def get_constraints(self) -> ConstraintsConfig:
        return self.load().constraints

    # ── Private parser ──────────────────────────────────────────

    @staticmethod
    def _parse(raw: dict) -> AgentConfig:
        return AgentConfig(
            name=raw.get("name", ""),
            version=raw.get("version", ""),
            provider=ConfigLoader._parse_provider(raw.get("provider", {})),
            nodes=ConfigLoader._parse_nodes(raw.get("nodes", {})),
            skills=ConfigLoader._parse_skills(raw.get("skills", {})),
            workflow=ConfigLoader._parse_workflow(raw.get("workflow", {})),
            memory=ConfigLoader._parse_memory(raw.get("memory", {})),
            global_context=ConfigLoader._parse_global_context(
                raw.get("global_context", {})
            ),
            constraints=ConfigLoader._parse_constraints(raw.get("constraints", {})),
        )

    @staticmethod
    def _parse_provider(raw: dict) -> ProviderConfig:
        return ProviderConfig(
            default=raw.get("default", ""),
            fallback=raw.get("fallback", []),
            litellm_config=raw.get("litellm_config", ""),
        )

    @staticmethod
    def _parse_nodes(raw: dict) -> dict[str, NodeAgentConfig]:
        return {
            name: NodeAgentConfig(
                model=cfg.get("model", "none"),
                tools=cfg.get("tools", []),
                permissions=cfg.get("permissions", {}),
                skills=cfg.get("skills", []),
                streaming=cfg.get("streaming", False),
            )
            for name, cfg in raw.items()
        }

    @staticmethod
    def _parse_skills(raw: dict) -> SkillsConfig:
        return SkillsConfig(
            mode=raw.get("mode", "auto"),
            directory=raw.get("directory", "./skills"),
            preload=raw.get("preload", []),
        )

    @staticmethod
    def _parse_workflow(raw: dict) -> WorkflowBindingConfig:
        return WorkflowBindingConfig(
            default=raw.get("default", "gensql_agentic"),
            auto_select=raw.get("auto_select", False),
        )

    @staticmethod
    def _parse_memory(raw: dict) -> MemoryConfig:
        builtin = raw.get("builtin_subagents", {})
        user = raw.get("user_subagents", {})
        storage = raw.get("storage", {})
        return MemoryConfig(
            builtin_subagents=BuiltinSubagentsMemory(
                gen_sql=builtin.get("gen_sql", False),
                schema_linking=builtin.get("schema_linking", False),
                validate_sql=builtin.get("validate_sql", False),
                execute_sql=builtin.get("execute_sql", False),
                reflection=builtin.get("reflection", False),
            ),
            chat=raw.get("chat", True),
            user_subagents=UserSubagentsMemory(
                default=user.get("default", True),
            ),
            storage=StorageMemory(
                subagent=storage.get("subagent", ":memory:"),
                top_level=storage.get("top_level", "~/.data_engineer/sessions/"),
            ),
            isolation=raw.get("isolation", "node_name"),
        )

    @staticmethod
    def _parse_global_context(raw: dict) -> GlobalContextConfig:
        return GlobalContextConfig(
            source=raw.get("source", "AGENTS.md"),
            inject_lines=raw.get("inject_lines", 200),
            auto_reload=raw.get("auto_reload", True),
        )

    @staticmethod
    def _parse_constraints(raw: dict) -> ConstraintsConfig:
        return ConstraintsConfig(
            max_llm_calls_per_query=raw.get("max_llm_calls_per_query", 10),
            max_node_retries=raw.get("max_node_retries", 3),
            reflection_max_rounds=raw.get("reflection_max_rounds", 3),
            reflection_on_exhausted=raw.get("reflection_on_exhausted", "force_output"),
            session_ttl_seconds=raw.get("session_ttl_seconds", 3600),
            read_only=raw.get("read_only", True),
            max_result_rows=raw.get("max_result_rows", 1000),
            statement_timeout_ms=raw.get("statement_timeout_ms", 30000),
        )
