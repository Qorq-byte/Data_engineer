"""NL2SQL Data Engineering Agent — application entry point.

Starts the FastAPI server and optionally the MCP server.

Usage::

    python main.py                  # Start FastAPI on :8000
    python main.py --web            # Start Streamlit Web UI
"""

import asyncio
import sys

import uvicorn


async def initialize():
    """Initialize all system components before starting the server."""
    print("=" * 60)
    print("  NL2SQL Data Engineering Agent v0.1.0")
    print("=" * 60)

    # 1. Register built-in nodes
    print("\n[1/9] Registering built-in nodes...")
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

    node_registry.register(ParseNLNode())
    node_registry.register(SchemaLinkingNode())
    node_registry.register(HybridSearchNode())
    node_registry.register(GenerateSQLNode())
    node_registry.register(ValidateSQLNode())
    node_registry.register(ExecuteSQLNode())
    node_registry.register(MetricResolveNode())
    node_registry.register(ReflectionNode())
    node_registry.register(ExplainPlanNode())
    node_registry.register(DialectTranslateNode())
    node_registry.register(FormatSQLNode())
    node_registry.register(RespondNode())
    print(f"   Registered {len(node_registry)} nodes: {node_registry.list_all()}")

    # 2. Load agent configuration
    print("\n[2/9] Loading agent configuration...")
    from app.harness.config_loader import ConfigLoader

    config_loader = ConfigLoader("app/config/agent.yml")
    agent_config = config_loader.load()
    print(f"   Agent: {agent_config.name} v{agent_config.version}")
    print(f"   Default model: {agent_config.provider.default}")
    print(f"   Default workflow: {agent_config.workflow.default}")
    print(f"   Constraints: read_only={agent_config.constraints.read_only}")

    # 3. Load available workflow plans
    print("\n[3/9] Loading workflow plans...")
    from app.harness.plan_loader import PlanLoader

    plan_loader = PlanLoader("app/workflow/plans")
    plans = plan_loader.list_available()
    for p in plans:
        plan = plan_loader.load(p)
        print(f"   {plan.id}: {plan.name} ({len(plan.node_order)} nodes)")

    # 4. Register MCP servers
    print("\n[4/9] Registering MCP servers...")
    from app.mcp.registry import mcp_registry
    from app.mcp.servers.db_server import db_mcp_config
    from app.mcp.servers.knowledge_server import knowledge_mcp_config
    from app.mcp.servers.learning_server import learning_mcp_config
    from app.mcp.servers.vector_server import vector_mcp_config

    mcp_registry.register(db_mcp_config)
    mcp_registry.register(knowledge_mcp_config)
    mcp_registry.register(vector_mcp_config)
    mcp_registry.register(learning_mcp_config)
    print(f"   MCP Servers: {mcp_registry.list_names()}")

    # 5. Initialize Plan Selector (Phase 3)
    print("\n[5/9] Initializing plan selector...")
    from app.workflow.plan_selector import PlanSelector

    plan_selector = PlanSelector(plan_loader=plan_loader)
    print(f"   Plan selector ready with {len(plan_loader.list_available())} plans")

    # 6. Initialize multi-agent system (Phase 4)
    print("\n[6/9] Initializing multi-agent system...")
    from app.agents.feedback import FeedbackAgent
    from app.agents.nl_understander import NLUnderstandingAgent
    from app.agents.orchestrator import OrchestratorAgent
    from app.agents.registry import agent_registry
    from app.agents.schema_retriever import SchemaRetrievalAgent
    from app.agents.sql_generator import SQLGenerationAgent
    from app.agents.sql_validator import ValidationAgent
    from app.agents.tool_executor import ToolExecutionAgent

    orchestrator = OrchestratorAgent()
    orchestrator.register(NLUnderstandingAgent())
    orchestrator.register(SchemaRetrievalAgent())
    orchestrator.register(SQLGenerationAgent())
    orchestrator.register(ValidationAgent())
    orchestrator.register(ToolExecutionAgent())
    orchestrator.register(FeedbackAgent())
    print(
        f"   Orchestrator ready with {len(agent_registry.list_all())} agents: "
        f"{agent_registry.list_all()}"
    )

    # 7. Initialize auth database
    print("\n[7/9] Initializing auth database...")
    from app.auth.database import init_auth_db

    try:
        await init_auth_db()
        print("   Auth database ready — users table created")
    except Exception as e:
        print(f"   WARNING: Auth database unavailable ({e}) — login/register disabled")

    # 8. Initialize subagent system (Phase 4)
    print("\n[8/9] Initializing subagent system...")
    from app.subagent.manager import subagent_manager

    names = subagent_manager.load_and_register()
    if names:
        for name in names:
            inst = subagent_manager.get(name)
            delivery_info = ", ".join(
                f"{d.type}" for d in (inst.config.delivery if inst else [])
            )
            print(
                f"   {name}: domain={inst.config.domain if inst else '?'}, "
                f"delivery=[{delivery_info}]"
            )
    else:
        print("   No subagent configs found in app/config/subagents/")

    # 9. Initialize RAG engine (schema + metric + document hybrid search)
    print("\n[9/9] Initializing RAG engine (schema + metric + document hybrid search)...")
    try:
        from app.rag.engine import initialize_rag_engine

        rag_engine = await initialize_rag_engine()
        schema_stats = rag_engine.schema_rag.stats()
        metric_stats = rag_engine.metric_rag.stats()
        doc_stats = rag_engine.document_store.stats()
        print(f"   Schema index: {schema_stats}")
        print(f"   Metric index: {metric_stats}")
        print(f"   Document index: {doc_stats}")
    except Exception as e:
        print(f"   WARNING: RAG engine unavailable ({e}) — hybrid search disabled")

    print("\n" + "=" * 60)
    print("  Initialization complete. Starting server...")
    print("=" * 60 + "\n")

    return {
        "config_loader": config_loader,
        "plan_loader": plan_loader,
        "plan_selector": plan_selector,
        "orchestrator": orchestrator,
        "subagent_manager": subagent_manager,
    }


async def run_api():
    """Start the FastAPI server."""
    try:
        await initialize()
    except Exception as e:
        print(f"ERROR during initialization: {e}", file=sys.stderr)
        raise

    config = uvicorn.Config(
        "app.api:create_app",
        host="0.0.0.0",
        port=8001,
        reload=False,
        log_level="info",
        factory=True,
    )
    server = uvicorn.Server(config)
    await server.serve()


async def main():
    """Main entry point — starts the FastAPI server."""
    await run_api()


if __name__ == "__main__":
    asyncio.run(main())
