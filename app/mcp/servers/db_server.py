"""Database MCP Server — exposes schema, query, and table tools via FastMCP.

See SPEC §3.3.4 (MCP tool definitions) and §3.3.7 (MCP Server external exposure).

This server provides:
  Tools:
    - execute_read_query  → run SELECT queries
    - list_tables         → list all tables
    - describe_table      → get column details for a table
    - search_schema       → search tables and columns by keyword
  Resources:
    - schemas://{db_id}/tables       → all tables
    - schemas://{db_id}/tables/{table} → single table details
"""

from app.models.mcp import MCPResourceDef, MCPServerConfig, MCPToolDef

# ── Tool definitions ───────────────────────────────────────────────

EXECUTE_READ_QUERY_TOOL = MCPToolDef(
    name="execute_read_query",
    description="Execute a read-only SELECT query against the database",
    input_schema={
        "type": "object",
        "properties": {
            "sql": {"type": "string", "description": "Read-only SQL SELECT statement"},
            "max_rows": {"type": "integer", "default": 100},
        },
        "required": ["sql"],
    },
)

LIST_TABLES_TOOL = MCPToolDef(
    name="list_tables",
    description="List all tables in the connected database",
    input_schema={
        "type": "object",
        "properties": {},
    },
)

DESCRIBE_TABLE_TOOL = MCPToolDef(
    name="describe_table",
    description="Get full column definitions for a specific table",
    input_schema={
        "type": "object",
        "properties": {
            "table_name": {"type": "string", "description": "Name of the table"},
        },
        "required": ["table_name"],
    },
)

SEARCH_SCHEMA_TOOL = MCPToolDef(
    name="search_schema",
    description="Search for relevant tables and columns by keyword",
    input_schema={
        "type": "object",
        "properties": {
            "keywords": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Keywords to search for",
            },
            "mode": {
                "type": "string",
                "enum": ["keyword", "semantic", "hybrid"],
                "default": "keyword",
            },
        },
        "required": ["keywords"],
    },
)

# ── Resource definitions ────────────────────────────────────────────

TABLES_RESOURCE = MCPResourceDef(
    uri="schemas://{db_id}/tables",
    description="List of all tables in the database with brief descriptions",
)

TABLE_DETAIL_RESOURCE = MCPResourceDef(
    uri="schemas://{db_id}/tables/{table}",
    description="Full column definitions, indexes, and foreign keys for a single table",
)

# ── Server config ──────────────────────────────────────────────────

db_mcp_config = MCPServerConfig(
    name="Database MCP Server",
    server_type="database",
    transport="sse",
    host="0.0.0.0",
    port=8081,
    tools=[
        EXECUTE_READ_QUERY_TOOL,
        LIST_TABLES_TOOL,
        DESCRIBE_TABLE_TOOL,
        SEARCH_SCHEMA_TOOL,
    ],
    resources=[TABLES_RESOURCE, TABLE_DETAIL_RESOURCE],
)
