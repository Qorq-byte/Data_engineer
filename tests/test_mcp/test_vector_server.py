"""Tests for Vector MCP Server — tool/resource definitions, config, registry.

Covers tool schemas, resource URIs, server config construction, and
MCPServerRegistry integration.
"""

from __future__ import annotations

import pytest

from app.mcp.registry import MCPServerRegistry
from app.mcp.servers.vector_server import (
    EMBEDDINGS_STORE_RESOURCE,
    HYBRID_SEARCH_TOOL,
    KNN_SEARCH_TOOL,
    REFRESH_INDEX_TOOL,
    vector_mcp_config,
)
from app.models.mcp import MCPResourceDef, MCPServerConfig, MCPToolDef

# ── Module-level constants ───────────────────────────────────────────

EXPECTED_TOOLS = {"hybrid_search", "knn_search", "refresh_index"}
EXPECTED_RESOURCES = {"embeddings://{store}/"}


# ── Tool definition tests ────────────────────────────────────────────


class TestHybridSearchTool:
    """Tests for the hybrid_search tool definition."""

    def test_name(self):
        assert HYBRID_SEARCH_TOOL.name == "hybrid_search"

    def test_description(self):
        desc = HYBRID_SEARCH_TOOL.description.lower()
        assert "dense" in desc or "vector" in desc
        assert "sparse" in desc or "bm25" in desc
        assert "rrf" in desc

    def test_input_schema_type(self):
        assert HYBRID_SEARCH_TOOL.input_schema["type"] == "object"

    def test_input_schema_required(self):
        assert HYBRID_SEARCH_TOOL.input_schema["required"] == ["query_text"]

    def test_input_schema_properties(self):
        props = HYBRID_SEARCH_TOOL.input_schema["properties"]
        assert "query_text" in props
        assert "target" in props
        assert "top_k" in props
        assert "alpha" in props
        assert "db_id" in props
        assert "domain" in props
        assert "content_type" in props
        assert "language" in props
        assert props["query_text"]["type"] == "string"
        assert props["top_k"]["type"] == "integer"
        assert props["top_k"]["default"] == 10
        assert props["alpha"]["type"] == "number"
        assert props["alpha"]["default"] == 0.5

    def test_target_enum_values(self):
        enum_vals = HYBRID_SEARCH_TOOL.input_schema["properties"]["target"]["enum"]
        assert set(enum_vals) == {"schema", "metrics", "documents", "all"}

    def test_is_mcp_tool_def(self):
        assert isinstance(HYBRID_SEARCH_TOOL, MCPToolDef)


class TestKNNSearchTool:
    """Tests for the knn_search tool definition."""

    def test_name(self):
        assert KNN_SEARCH_TOOL.name == "knn_search"

    def test_description(self):
        desc = KNN_SEARCH_TOOL.description.lower()
        assert "vector" in desc or "knn" in desc or "similarity" in desc

    def test_input_schema_required(self):
        assert KNN_SEARCH_TOOL.input_schema["required"] == ["query_text"]

    def test_input_schema_properties(self):
        props = KNN_SEARCH_TOOL.input_schema["properties"]
        assert props["query_text"]["type"] == "string"
        assert props["namespace"]["type"] == "string"
        assert props["top_k"]["type"] == "integer"
        assert props["top_k"]["default"] == 10
        assert props["filter_expr"]["type"] == "string"

    def test_namespace_is_optional(self):
        """namespace is not required — default/all namespaces if omitted."""
        assert "namespace" not in KNN_SEARCH_TOOL.input_schema["required"]

    def test_is_mcp_tool_def(self):
        assert isinstance(KNN_SEARCH_TOOL, MCPToolDef)


class TestRefreshIndexTool:
    """Tests for the refresh_index tool definition."""

    def test_name(self):
        assert REFRESH_INDEX_TOOL.name == "refresh_index"

    def test_description(self):
        desc = REFRESH_INDEX_TOOL.description.lower()
        assert "refresh" in desc or "rebuild" in desc or "re-index" in desc

    def test_input_schema_no_required(self):
        """refresh_index has no required fields — all params are optional."""
        assert REFRESH_INDEX_TOOL.input_schema.get("required", []) == []

    def test_input_schema_properties(self):
        props = REFRESH_INDEX_TOOL.input_schema["properties"]
        assert props["namespace"]["type"] == "string"
        assert props["full_rebuild"]["type"] == "boolean"
        assert props["full_rebuild"]["default"] is False

    def test_is_mcp_tool_def(self):
        assert isinstance(REFRESH_INDEX_TOOL, MCPToolDef)


# ── Resource definition tests ────────────────────────────────────────


class TestEmbeddingsStoreResource:
    """Tests for the embeddings://{store}/ resource definition."""

    def test_uri(self):
        assert EMBEDDINGS_STORE_RESOURCE.uri == "embeddings://{store}/"

    def test_description(self):
        desc = EMBEDDINGS_STORE_RESOURCE.description.lower()
        assert "embedding" in desc or "metadata" in desc or "statistics" in desc

    def test_content_type_default(self):
        assert EMBEDDINGS_STORE_RESOURCE.content_type == "application/json"

    def test_is_mcp_resource_def(self):
        assert isinstance(EMBEDDINGS_STORE_RESOURCE, MCPResourceDef)


# ── Server config tests ──────────────────────────────────────────────


class TestVectorMCPServerConfig:
    """Tests for the vector_mcp_config server configuration."""

    def test_name(self):
        assert vector_mcp_config.name == "Vector MCP Server"

    def test_server_type(self):
        assert vector_mcp_config.server_type == "vector"

    def test_transport(self):
        assert vector_mcp_config.transport == "sse"

    def test_host(self):
        assert vector_mcp_config.host == "0.0.0.0"

    def test_port(self):
        assert vector_mcp_config.port == 8083

    def test_status_default(self):
        assert vector_mcp_config.status == "active"

    def test_tool_count(self):
        assert len(vector_mcp_config.tools) == 3

    def test_tool_names(self):
        names = {t.name for t in vector_mcp_config.tools}
        assert names == EXPECTED_TOOLS

    def test_resource_count(self):
        assert len(vector_mcp_config.resources) == 1

    def test_resource_uris(self):
        uris = {r.uri for r in vector_mcp_config.resources}
        assert uris == EXPECTED_RESOURCES

    def test_is_mcp_server_config(self):
        assert isinstance(vector_mcp_config, MCPServerConfig)

    def test_metadata_default(self):
        assert isinstance(vector_mcp_config.metadata, dict)
        assert vector_mcp_config.metadata == {}

    def test_registered_at_is_set(self):
        assert vector_mcp_config.registered_at is not None

    def test_last_health_check_default(self):
        assert vector_mcp_config.last_health_check is None


class TestVectorMCPServerToolsNoOverlap:
    """Each tool serves a distinct purpose — verify no schema confusion."""

    def test_hybrid_vs_knn(self):
        """hybrid_search uses RRF fusion; knn_search is pure vector lookup."""
        # hybrid has alpha (dense/sparse weight); knn does not
        assert "alpha" in HYBRID_SEARCH_TOOL.input_schema["properties"]
        assert "alpha" not in KNN_SEARCH_TOOL.input_schema["properties"]

    def test_hybrid_has_target_enum(self):
        """Only hybrid_search supports multi-backend targeting."""
        assert "target" in HYBRID_SEARCH_TOOL.input_schema["properties"]
        assert "target" not in KNN_SEARCH_TOOL.input_schema["properties"]

    def test_refresh_has_no_required_params(self):
        """refresh_index is the most flexible — nothing required."""
        assert REFRESH_INDEX_TOOL.input_schema.get("required", []) == []

    def test_all_tools_have_distinct_names(self):
        names = [
            HYBRID_SEARCH_TOOL.name,
            KNN_SEARCH_TOOL.name,
            REFRESH_INDEX_TOOL.name,
        ]
        assert len(names) == len(set(names))

    def test_all_tools_have_descriptions(self):
        for tool in vector_mcp_config.tools:
            assert tool.description, f"{tool.name} has no description"


# ── Registry integration tests ───────────────────────────────────────


class TestVectorMCPServerRegistry:
    """Tests for registering the Vector MCP Server with the registry."""

    @pytest.fixture(autouse=True)
    def _clean_registry(self):
        """Ensure registry is clean before and after each test."""
        MCPServerRegistry.reset()
        yield
        MCPServerRegistry.reset()

    def test_register(self):
        MCPServerRegistry.register(vector_mcp_config)
        names = MCPServerRegistry.list_names()
        assert "Vector MCP Server" in names

    def test_get_after_register(self):
        MCPServerRegistry.register(vector_mcp_config)
        server = MCPServerRegistry.get("Vector MCP Server")
        assert server is vector_mcp_config
        assert server.server_type == "vector"

    def test_get_missing_raises(self):
        with pytest.raises(KeyError, match="Vector MCP Server"):
            MCPServerRegistry.get("Vector MCP Server")

    def test_unregister(self):
        MCPServerRegistry.register(vector_mcp_config)
        removed = MCPServerRegistry.unregister("Vector MCP Server")
        assert removed is vector_mcp_config
        assert MCPServerRegistry.list_names() == []

    def test_list_all(self):
        MCPServerRegistry.register(vector_mcp_config)
        all_servers = MCPServerRegistry.list_all()
        assert len(all_servers) == 1
        assert all_servers[0] is vector_mcp_config

    def test_list_active(self):
        MCPServerRegistry.register(vector_mcp_config)
        active = MCPServerRegistry.list_active()
        assert len(active) == 1
        assert active[0] is vector_mcp_config

    def test_get_server_tools(self):
        MCPServerRegistry.register(vector_mcp_config)
        tools = MCPServerRegistry.get_server_tools("Vector MCP Server")
        assert len(tools) == 3
        tool_names = {t.name for t in tools}
        assert tool_names == EXPECTED_TOOLS

    def test_get_server_resources(self):
        MCPServerRegistry.register(vector_mcp_config)
        resources = MCPServerRegistry.get_server_resources("Vector MCP Server")
        assert len(resources) == 1
        assert resources[0].uri == "embeddings://{store}/"

    def test_update_health(self):
        MCPServerRegistry.register(vector_mcp_config)
        MCPServerRegistry.update_health("Vector MCP Server", "error")
        server = MCPServerRegistry.get("Vector MCP Server")
        assert server.status == "error"  # type: ignore[comparison-overlap]
        assert server.last_health_check is not None

    def test_all_three_servers_coexist(self):
        """Vector MCP can coexist with Database and Knowledge in registry."""
        from app.mcp.servers.db_server import db_mcp_config
        from app.mcp.servers.knowledge_server import knowledge_mcp_config

        MCPServerRegistry.register(db_mcp_config)
        MCPServerRegistry.register(knowledge_mcp_config)
        MCPServerRegistry.register(vector_mcp_config)

        names = MCPServerRegistry.list_names()
        assert len(names) == 3
        assert "Vector MCP Server" in names
        assert "Knowledge MCP Server" in names
        assert "Database MCP Server" in names

    def test_list_by_type(self):
        MCPServerRegistry.register(vector_mcp_config)
        all_servers = MCPServerRegistry.list_all()
        vector_servers = [s for s in all_servers if s.server_type == "vector"]
        assert len(vector_servers) == 1
        assert vector_servers[0].name == "Vector MCP Server"


# ── Edge cases ───────────────────────────────────────────────────────


class TestVectorMCPServerEdgeCases:
    """Edge case tests for the Vector MCP Server definitions."""

    def test_tool_server_id_default(self):
        for tool in vector_mcp_config.tools:
            assert tool.server_id == ""

    def test_resource_server_id_default(self):
        for resource in vector_mcp_config.resources:
            assert resource.server_id == ""

    def test_all_tools_have_valid_input_schema(self):
        for tool in vector_mcp_config.tools:
            schema = tool.input_schema
            assert schema["type"] == "object"
            assert isinstance(schema.get("properties", {}), dict)

    def test_no_duplicate_uris(self):
        uris = [r.uri for r in vector_mcp_config.resources]
        assert len(uris) == len(set(uris))

    def test_port_does_not_conflict(self):
        """Each MCP server has a unique port."""
        from app.mcp.servers.db_server import db_mcp_config
        from app.mcp.servers.knowledge_server import knowledge_mcp_config

        ports = {
            db_mcp_config.port,
            knowledge_mcp_config.port,
            vector_mcp_config.port,
        }
        assert len(ports) == 3
        assert vector_mcp_config.port == 8083

    def test_hybrid_search_minimal_input(self):
        """hybrid_search only requires query_text — all other fields optional."""
        required = HYBRID_SEARCH_TOOL.input_schema["required"]
        assert "query_text" in required
        assert "target" not in required
        assert "top_k" not in required

    def test_knn_filter_expr_optional(self):
        """filter_expr is for advanced use only, not required."""
        assert "filter_expr" not in KNN_SEARCH_TOOL.input_schema.get("required", [])
