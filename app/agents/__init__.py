"""Multi-Agent System — custom lightweight agent framework.

See SPEC §4.11 and implementation-plan §4.11 for the full architecture.

Agents are thin wrappers around existing core modules (NLParser, SchemaRetriever,
SQLGenerator, etc.) that add tracing, logging, and inter-agent communication
via the AgentBus.

Exports (flattened public API):
  - BaseAgent, AgentResult, AgentStatus
  - AgentBus, AgentMessage, MessageType
  - OrchestratorAgent, OrchestratorResult, TaskBreakdown
  - All 6 sub-agents
  - CollaborationMode and 5 mode runner functions
  - AgentTraceCollector, OrchestratorTrace, AgentTraceEntry
  - AgentRegistry, agent_registry singleton
  - agent_bus, agent_trace_collector singletons
"""

from app.agents.base import AgentResult, AgentStatus, BaseAgent
from app.agents.bus import AgentBus, AgentMessage, MessageType, agent_bus
from app.agents.collaboration import (
    CollaborationMode,
    run_multi_step,
    run_negotiation,
    run_parallel_debate,
    run_pipeline,
    run_retry_correction,
    select_mode,
)
from app.agents.feedback import FeedbackAgent, FeedbackRecord
from app.agents.nl_understander import NLUnderstandingAgent
from app.agents.orchestrator import (
    OrchestratorAgent,
    OrchestratorResult,
    TaskBreakdown,
)
from app.agents.registry import AgentRegistry, agent_registry
from app.agents.schema_retriever import SchemaRetrievalAgent
from app.agents.sql_generator import SQLGenerationAgent
from app.agents.sql_validator import ValidationAgent
from app.agents.tool_executor import ToolExecutionAgent
from app.agents.trace import (
    AgentTraceCollector,
    AgentTraceEntry,
    OrchestratorTrace,
    agent_trace_collector,
)

__all__ = [
    # Base
    "BaseAgent",
    "AgentResult",
    "AgentStatus",
    # Registry
    "AgentRegistry",
    "agent_registry",
    # Bus
    "AgentBus",
    "AgentMessage",
    "MessageType",
    "agent_bus",
    # Orchestrator
    "OrchestratorAgent",
    "OrchestratorResult",
    "TaskBreakdown",
    # Sub-agents
    "NLUnderstandingAgent",
    "SchemaRetrievalAgent",
    "SQLGenerationAgent",
    "ValidationAgent",
    "ToolExecutionAgent",
    "FeedbackAgent",
    "FeedbackRecord",
    # Collaboration
    "CollaborationMode",
    "run_pipeline",
    "run_retry_correction",
    "run_parallel_debate",
    "run_negotiation",
    "run_multi_step",
    "select_mode",
    # Trace
    "AgentTraceCollector",
    "AgentTraceEntry",
    "OrchestratorTrace",
    "agent_trace_collector",
]
