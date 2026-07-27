"""Learning MCP Server — expose feedback records, rule extraction, and evolution stats.

See SPEC §5.6 (Phase 5 roadmap).

Provides tools and resources for the continuous learning pipeline.
Port 8084 (configurable).
"""

from __future__ import annotations

from typing import Any

from app.models.mcp import MCPResourceDef, MCPServerConfig, MCPToolDef

# ── Tool definitions ──────────────────────────────────────────────────────


LEARNING_TOOLS: list[dict[str, Any]] = [
    {
        "name": "list_feedback",
        "description": "List stored feedback records, optionally filtered by domain and rating.",
        "parameters": {
            "type": "object",
            "properties": {
                "domain": {"type": "string", "description": "Domain filter"},
                "min_rating": {"type": "integer", "description": "Minimum rating (1-5)"},
                "limit": {"type": "integer", "description": "Max records to return", "default": 50},
            },
        },
    },
    {
        "name": "get_feedback_stats",
        "description": "Get aggregate feedback statistics.",
        "parameters": {
            "type": "object",
            "properties": {
                "domain": {"type": "string", "description": "Domain filter"},
            },
        },
    },
    {
        "name": "extract_rules",
        "description": (
            "Trigger pattern analysis to extract candidate business rules "
            "from recent feedback."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "domain": {"type": "string", "description": "Domain filter"},
                "window_days": {
                    "type": "integer",
                    "description": "Look-back window in days",
                    "default": 30,
                },
            },
        },
    },
    {
        "name": "list_candidate_rules",
        "description": "List extracted candidate rules awaiting review.",
        "parameters": {
            "type": "object",
            "properties": {
                "domain": {"type": "string", "description": "Domain filter"},
                "status": {
                    "type": "string",
                    "description": "Filter by status",
                    "enum": ["pending_review", "approved", "rejected"],
                },
            },
        },
    },
    {
        "name": "approve_rule",
        "description": "Approve a candidate rule and write it to the domain rules config.",
        "parameters": {
            "type": "object",
            "properties": {
                "rule_id": {"type": "string", "description": "Candidate rule ID"},
            },
            "required": ["rule_id"],
        },
    },
    {
        "name": "get_evolution_stats",
        "description": "Get EvolvableContext lifecycle and evolution statistics.",
        "parameters": {
            "type": "object",
            "properties": {},
        },
    },
]

# ── Resource definitions ───────────────────────────────────────────────────

LEARNING_RESOURCES: list[dict[str, Any]] = [
    {
        "uri": "feedback://{domain}/recent",
        "name": "Recent feedback records",
        "description": "Last 50 feedback records for a domain.",
        "parameters": {
            "domain": {"type": "string", "description": "Domain identifier"},
        },
    },
    {
        "uri": "feedback://{domain}/stats",
        "name": "Feedback statistics",
        "description": "Aggregate feedback statistics for a domain.",
        "parameters": {
            "domain": {"type": "string", "description": "Domain identifier"},
        },
    },
    {
        "uri": "rules://{domain}/candidates",
        "name": "Candidate rules",
        "description": "Pending candidate rules for a domain.",
        "parameters": {
            "domain": {"type": "string", "description": "Domain identifier"},
        },
    },
]

# ── Server config ──────────────────────────────────────────────────────────


def get_learning_server_config() -> dict[str, Any]:
    """Return the learning MCP server configuration."""
    return {
        "server_type": "learning",
        "host": "0.0.0.0",
        "port": 8084,
        "tools": LEARNING_TOOLS,
        "resources": LEARNING_RESOURCES,
    }


learning_mcp_config = MCPServerConfig(
    name="Learning MCP Server",
    server_type="learning",
    transport="sse",
    host="0.0.0.0",
    port=8084,
    tools=[
        MCPToolDef(
            name=t["name"],
            description=t["description"],
            input_schema=t.get("parameters", {}),
        )
        for t in LEARNING_TOOLS
    ],
    resources=[
        MCPResourceDef(uri=r["uri"], description=r["description"])
        for r in LEARNING_RESOURCES
    ],
)


# ── Handler implementations ────────────────────────────────────────────────


async def handle_list_feedback(
    domain: str = "", min_rating: int = 0, limit: int = 50
) -> dict[str, Any]:
    """Handler for ``list_feedback`` tool."""
    from app.learning.feedback_collector import feedback_collector

    records = feedback_collector.get_recent(
        domain=domain, min_rating=min_rating, limit=limit
    )
    return {"count": len(records), "records": records}


async def handle_get_feedback_stats(domain: str = "") -> dict[str, Any]:
    """Handler for ``get_feedback_stats`` tool."""
    from app.learning.feedback_collector import feedback_collector

    return feedback_collector.get_stats(domain=domain)


async def handle_extract_rules(
    domain: str = "", window_days: int = 30
) -> dict[str, Any]:
    """Handler for ``extract_rules`` tool."""
    from app.learning.pattern_analyzer import PatternAnalyzer

    analyzer = PatternAnalyzer()
    candidates = analyzer.analyze(domain=domain, window_days=window_days)
    return {
        "count": len(candidates),
        "candidates": analyzer.to_dicts(candidates),
    }


async def handle_list_candidate_rules(
    domain: str = "", status: str = "pending_review"
) -> dict[str, Any]:
    """Handler for ``list_candidate_rules`` tool."""
    from app.learning.pattern_analyzer import PatternAnalyzer

    analyzer = PatternAnalyzer()
    candidates = analyzer.analyze(domain=domain, min_occurrence=1)
    filtered = [c for c in candidates if c.status == status]
    return {"count": len(filtered), "candidates": analyzer.to_dicts(filtered)}


async def handle_approve_rule(rule_id: str) -> dict[str, Any]:
    """Handler for ``approve_rule`` tool."""
    from app.learning.pattern_analyzer import PatternAnalyzer

    analyzer = PatternAnalyzer()
    candidates = analyzer.analyze(min_occurrence=1)
    target = next((c for c in candidates if c.rule_id == rule_id), None)
    if target is None:
        return {"success": False, "error": f"Rule '{rule_id}' not found"}

    target.status = "approved"
    # Persist to rule engine
    try:
        from app.knowledge.domain_manager import domain_manager
        from app.knowledge.rule_engine import RuleEngine
        from app.models.domain import BusinessRule

        engine = RuleEngine(domain_manager=domain_manager)
        engine.load_from_domains()
        rule = BusinessRule(
            id=target.suggested_rule_name,
            name=target.suggested_rule_name,
            description=target.pattern_description,
            pattern=target.suggested_pattern,
            enforcement=target.suggested_enforcement,
            domain=target.domain_id,
        )
        engine.add(rule, domain=target.domain_id)
    except Exception as e:
        return {"success": False, "error": str(e)}

    return {"success": True, "rule_id": rule_id}


async def handle_get_evolution_stats() -> dict[str, Any]:
    """Handler for ``get_evolution_stats`` tool."""
    try:
        from app.knowledge.evolvable_context import EvolvableContext

        ctx = EvolvableContext()
        return ctx.get_evolution_stats()
    except Exception:
        return {"error": "EvolvableContext not available"}
