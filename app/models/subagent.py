"""Subagent models — SubagentConfig, SubagentContext.

See SPEC §4.10.2 (Subagent definition) for the full specification.
"""

from dataclasses import dataclass, field


@dataclass
class DeliveryConfig:
    """How a subagent is delivered to users."""

    type: str  # "web", "api", "mcp"
    path: str = ""  # Web UI path or API endpoint
    endpoint: str = ""  # API endpoint
    tool_prefix: str = ""  # MCP tool name prefix


@dataclass
class SubagentContext:
    """Isolated context for a subagent instance."""

    domain: str
    databases: list[dict] = field(default_factory=list)
    max_turns: int = 20
    enable_sql_edit: bool = True
    enable_execution: bool = True
    enable_multi_candidate: bool = True


@dataclass
class LLMConfig:
    """LLM configuration for a subagent."""

    model: str = "claude-sonnet-4"
    temperature: float = 0.3
    max_tokens: int = 4096


@dataclass
class AccessControl:
    """Access control configuration for a subagent."""

    allowed_users: list[str] = field(default_factory=lambda: ["*"])
    rate_limit: int = 100  # max requests per hour


@dataclass
class SubagentConfig:
    """Full configuration for a scoped subagent chatbot.

    Loaded from subagents/{name}.yaml.
    """

    name: str
    label: dict[str, str] = field(default_factory=dict)
    domain: str = ""
    description: str = ""
    context: SubagentContext = field(default_factory=lambda: SubagentContext(domain=""))
    capabilities: list[str] = field(default_factory=list)
    delivery: list[DeliveryConfig] = field(default_factory=list)
    llm_config: LLMConfig = field(default_factory=LLMConfig)
    access_control: AccessControl = field(default_factory=AccessControl)

    @property
    def label_zh(self) -> str:
        return self.label.get("zh", self.name)

    @property
    def label_en(self) -> str:
        return self.label.get("en", self.name)
