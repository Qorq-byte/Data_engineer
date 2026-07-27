"""Vector MCP Server — exposes hybrid search and KNN vector search via MCP.

See SPEC §3.3.4 (MCP tool definitions) and implementation-plan §4.6.2.

This server provides:
  Tools:
    - hybrid_search  → dense + sparse retrieval with RRF fusion
    - knn_search     → pure vector similarity search (LanceDB)
    - refresh_index  → reload/reindex embeddings from storage
  Resources:
    - embeddings://{store}/ → embeddings store metadata and stats
"""

from app.models.mcp import MCPResourceDef, MCPServerConfig, MCPToolDef

# ── Tool definitions ───────────────────────────────────────────────

HYBRID_SEARCH_TOOL = MCPToolDef(
    name="hybrid_search",
    description=(
        "Combined dense (vector) + sparse (BM25) search with RRF "
        "(Reciprocal Rank Fusion) merging. Returns ranked results "
        "across schema metadata, business metrics, and documents."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "query_text": {
                "type": "string",
                "description": "Natural-language query text to search for",
            },
            "target": {
                "type": "string",
                "enum": ["schema", "metrics", "documents", "all"],
                "default": "all",
                "description": "Which RAG backend(s) to query",
            },
            "top_k": {
                "type": "integer",
                "default": 10,
                "description": "Maximum number of fused results to return",
            },
            "alpha": {
                "type": "number",
                "default": 0.5,
                "description": (
                    "Dense-vs-sparse weight (0.0 = sparse-only, "
                    "0.5 = balanced, 1.0 = dense-only)"
                ),
            },
            "db_id": {
                "type": "string",
                "description": "Filter schema results to a specific database",
            },
            "domain": {
                "type": "string",
                "description": "Filter metric and document results to a domain",
            },
            "content_type": {
                "type": "string",
                "description": "Filter document results by content type",
            },
            "language": {
                "type": "string",
                "default": "auto",
                "description": "Language hint (zh / en / auto)",
            },
        },
        "required": ["query_text"],
    },
)

KNN_SEARCH_TOOL = MCPToolDef(
    name="knn_search",
    description=(
        "Pure vector-similarity (KNN) search against a LanceDB namespace. "
        "Use this when you only need dense semantic search without BM25 sparse retrieval."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "query_text": {
                "type": "string",
                "description": "Natural-language query text to embed and search",
            },
            "namespace": {
                "type": "string",
                "description": (
                    "LanceDB namespace to search (schema_metadata / metrics / documents)"
                ),
            },
            "top_k": {
                "type": "integer",
                "default": 10,
                "description": "Maximum number of nearest neighbours to return",
            },
            "filter_expr": {
                "type": "string",
                "description": (
                    "Optional LanceDB SQL-like filter expression "
                    "(e.g. \"db_id = 'mydb'\" or \"domain = 'ecommerce'\")"
                ),
            },
        },
        "required": ["query_text"],
    },
)

REFRESH_INDEX_TOOL = MCPToolDef(
    name="refresh_index",
    description=(
        "Refresh or rebuild vector indices from source storage. "
        "Re-indexes embeddings to ensure search results are up-to-date."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "namespace": {
                "type": "string",
                "description": (
                    "Namespace to refresh (schema_metadata / metrics / documents). "
                    "If omitted, all namespaces are refreshed."
                ),
            },
            "full_rebuild": {
                "type": "boolean",
                "default": False,
                "description": "If true, drop and recreate all indices from scratch",
            },
        },
    },
)

# ── Resource definitions ────────────────────────────────────────────

EMBEDDINGS_STORE_RESOURCE = MCPResourceDef(
    uri="embeddings://{store}/",
    description=(
        "Metadata and statistics for an embeddings store, including "
        "namespace counts, embedding dimensions, and indexing status"
    ),
)

# ── Server config ──────────────────────────────────────────────────

vector_mcp_config = MCPServerConfig(
    name="Vector MCP Server",
    server_type="vector",
    transport="sse",
    host="0.0.0.0",
    port=8083,
    tools=[
        HYBRID_SEARCH_TOOL,
        KNN_SEARCH_TOOL,
        REFRESH_INDEX_TOOL,
    ],
    resources=[EMBEDDINGS_STORE_RESOURCE],
)
