"""Knowledge MCP Server — exposes glossary terms and business rules via MCP.

See SPEC §3.3.4 (MCP tool definitions) and implementation-plan §4.6.2.

This server provides:
  Tools:
    - search_terms  → search glossary terms matching NL query text
    - match_rules   → match business rules against NL query text
    - lookup_term   → look up a specific glossary term with mapping/resolution
  Resources:
    - glossary://{domain}/terms → all terms in a domain
    - rules://{domain}/         → all rules in a domain
"""

from app.models.mcp import MCPResourceDef, MCPServerConfig, MCPToolDef

# ── Tool definitions ───────────────────────────────────────────────

SEARCH_TERMS_TOOL = MCPToolDef(
    name="search_terms",
    description="Search glossary terms matching a natural-language query text",
    input_schema={
        "type": "object",
        "properties": {
            "query_text": {
                "type": "string",
                "description": "Natural-language query text to match against",
            },
            "domain": {
                "type": "string",
                "description": "Optional domain scope for term lookup",
            },
            "threshold": {
                "type": "number",
                "description": "Minimum match score (0.0–1.0), default 0.3",
                "default": 0.3,
            },
        },
        "required": ["query_text"],
    },
)

MATCH_RULES_TOOL = MCPToolDef(
    name="match_rules",
    description="Match business rules against a natural-language query text",
    input_schema={
        "type": "object",
        "properties": {
            "query_text": {
                "type": "string",
                "description": "Natural-language query text to match against",
            },
            "domain": {
                "type": "string",
                "description": "Optional domain scope for rule lookup",
            },
            "threshold": {
                "type": "number",
                "description": "Minimum match score (0.0–1.0), default 0.0",
                "default": 0.0,
            },
        },
        "required": ["query_text"],
    },
)

LOOKUP_TERM_TOOL = MCPToolDef(
    name="lookup_term",
    description=(
        "Look up a specific glossary term, returning its mapping and "
        "resolved schema objects"
    ),
    input_schema={
        "type": "object",
        "properties": {
            "term": {
                "type": "string",
                "description": "The business term to look up",
            },
            "domain": {
                "type": "string",
                "description": "Optional domain scope for term lookup",
            },
        },
        "required": ["term"],
    },
)

# ── Resource definitions ────────────────────────────────────────────

GLOSSARY_TERMS_RESOURCE = MCPResourceDef(
    uri="glossary://{domain}/terms",
    description="All glossary terms for a given domain",
)

RULES_RESOURCE = MCPResourceDef(
    uri="rules://{domain}/",
    description="All business rules for a given domain",
)

# ── Server config ──────────────────────────────────────────────────

knowledge_mcp_config = MCPServerConfig(
    name="Knowledge MCP Server",
    server_type="knowledge",
    transport="sse",
    host="0.0.0.0",
    port=8082,
    tools=[
        SEARCH_TERMS_TOOL,
        MATCH_RULES_TOOL,
        LOOKUP_TERM_TOOL,
    ],
    resources=[GLOSSARY_TERMS_RESOURCE, RULES_RESOURCE],
)
