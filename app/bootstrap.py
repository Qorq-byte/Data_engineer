"""Idempotent catalog registration shared by all FastAPI launch modes.

This does not start alternate query flows; the query API still uses request_query.
"""


def initialize_catalogs() -> None:
    from app.agents.feedback import FeedbackAgent
    from app.agents.nl_understander import NLUnderstandingAgent
    from app.agents.registry import agent_registry
    from app.agents.schema_retriever import SchemaRetrievalAgent
    from app.agents.sql_generator import SQLGenerationAgent
    from app.agents.sql_validator import ValidationAgent
    from app.agents.tool_executor import ToolExecutionAgent
    from app.mcp.registry import mcp_registry
    from app.mcp.servers.db_server import db_mcp_config
    from app.mcp.servers.knowledge_server import knowledge_mcp_config
    from app.mcp.servers.learning_server import learning_mcp_config
    from app.mcp.servers.vector_server import vector_mcp_config
    from app.nodes.dialect_translate import DialectTranslateNode
    from app.nodes.execute_sql import ExecuteSQLNode
    from app.nodes.explain_plan import ExplainPlanNode
    from app.nodes.format_sql import FormatSQLNode
    from app.nodes.generate_sql import GenerateSQLNode
    from app.nodes.hybrid_search import HybridSearchNode
    from app.nodes.metric_resolve import MetricResolveNode
    from app.nodes.parse_nl import ParseNLNode
    from app.nodes.reflection import ReflectionNode
    from app.nodes.registry import node_registry
    from app.nodes.respond import RespondNode
    from app.nodes.schema_linking import SchemaLinkingNode
    from app.nodes.validate_sql import ValidateSQLNode
    from app.subagent.manager import subagent_manager

    for node_type in [
        ParseNLNode,
        SchemaLinkingNode,
        HybridSearchNode,
        GenerateSQLNode,
        ValidateSQLNode,
        ExecuteSQLNode,
        MetricResolveNode,
        ReflectionNode,
        ExplainPlanNode,
        DialectTranslateNode,
        FormatSQLNode,
        RespondNode,
    ]:
        if node_type.name not in node_registry:
            node_registry.register(node_type())
    for config in [db_mcp_config, knowledge_mcp_config, vector_mcp_config, learning_mcp_config]:
        if config.name not in mcp_registry.list_names():
            mcp_registry.register(config)
    for agent_type in [
        NLUnderstandingAgent,
        SchemaRetrievalAgent,
        SQLGenerationAgent,
        ValidationAgent,
        ToolExecutionAgent,
        FeedbackAgent,
    ]:
        if agent_registry.get(agent_type.name) is None:
            agent_registry.register(agent_type())
    for config in subagent_manager.load_configs():
        if subagent_manager.get(config.name) is None:
            subagent_manager.register(config)
