"""Tests for Knowledge MCP Server — tool/resource definitions, config, registry.

Covers tool schemas, resource URIs, server config construction, and
MCPServerRegistry integration.
"""

from __future__ import annotations

import pytest

from app.mcp.registry import MCPServerRegistry
from app.mcp.servers.knowledge_server import (
    GLOSSARY_TERMS_RESOURCE,
    LOOKUP_TERM_TOOL,
    MATCH_RULES_TOOL,
    RULES_RESOURCE,
    SEARCH_TERMS_TOOL,
    knowledge_mcp_config,
)
from app.models.mcp import MCPResourceDef, MCPServerConfig, MCPToolDef

# ── Module-level constants ───────────────────────────────────────────

EXPECTED_TOOLS = {"search_terms", "match_rules", "lookup_term"}
EXPECTED_RESOURCES = {"glossary://{domain}/terms", "rules://{domain}/"}


# ── Tool definition tests ────────────────────────────────────────────


class TestSearchTermsTool:
    """Tests for the search_terms tool definition."""

    def test_name(self):
        assert SEARCH_TERMS_TOOL.name == "search_terms"

    def test_description(self):
        assert "glossary" in SEARCH_TERMS_TOOL.description.lower()
        assert "term" in SEARCH_TERMS_TOOL.description.lower()

    def test_input_schema_type(self):
        assert SEARCH_TERMS_TOOL.input_schema["type"] == "object"

    def test_input_schema_required(self):
        assert SEARCH_TERMS_TOOL.input_schema["required"] == ["query_text"]

    def test_input_schema_properties(self):
        props = SEARCH_TERMS_TOOL.input_schema["properties"]
        assert "query_text" in props
        assert "domain" in props
        assert "threshold" in props
        assert props["query_text"]["type"] == "string"
        assert props["domain"]["type"] == "string"
        assert props["threshold"]["type"] == "number"
        assert props["threshold"]["default"] == 0.3

    def test_is_mcp_tool_def(self):
        assert isinstance(SEARCH_TERMS_TOOL, MCPToolDef)


class TestMatchRulesTool:
    """Tests for the match_rules tool definition."""

    def test_name(self):
        assert MATCH_RULES_TOOL.name == "match_rules"

    def test_description(self):
        assert "rule" in MATCH_RULES_TOOL.description.lower()

    def test_input_schema_required(self):
        assert MATCH_RULES_TOOL.input_schema["required"] == ["query_text"]

    def test_input_schema_properties(self):
        props = MATCH_RULES_TOOL.input_schema["properties"]
        assert "query_text" in props
        assert "domain" in props
        assert "threshold" in props
        assert props["threshold"]["default"] == 0.0

    def test_is_mcp_tool_def(self):
        assert isinstance(MATCH_RULES_TOOL, MCPToolDef)


class TestLookupTermTool:
    """Tests for the lookup_term tool definition."""

    def test_name(self):
        assert LOOKUP_TERM_TOOL.name == "lookup_term"

    def test_description(self):
        assert "glossary" in LOOKUP_TERM_TOOL.description.lower()
        assert "term" in LOOKUP_TERM_TOOL.description.lower()

    def test_input_schema_required(self):
        assert LOOKUP_TERM_TOOL.input_schema["required"] == ["term"]

    def test_input_schema_properties(self):
        props = LOOKUP_TERM_TOOL.input_schema["properties"]
        assert "term" in props
        assert "domain" in props
        assert props["term"]["type"] == "string"
        assert props["domain"]["type"] == "string"

    def test_no_query_text_in_props(self):
        """lookup_term searches by exact term name, not query text."""
        props = LOOKUP_TERM_TOOL.input_schema["properties"]
        assert "query_text" not in props

    def test_is_mcp_tool_def(self):
        assert isinstance(LOOKUP_TERM_TOOL, MCPToolDef)


# ── Resource definition tests ────────────────────────────────────────


class TestGlossaryTermsResource:
    """Tests for the glossary://{domain}/terms resource definition."""

    def test_uri(self):
        assert GLOSSARY_TERMS_RESOURCE.uri == "glossary://{domain}/terms"

    def test_description(self):
        assert "glossary" in GLOSSARY_TERMS_RESOURCE.description.lower()
        assert "term" in GLOSSARY_TERMS_RESOURCE.description.lower()

    def test_content_type_default(self):
        assert GLOSSARY_TERMS_RESOURCE.content_type == "application/json"

    def test_is_mcp_resource_def(self):
        assert isinstance(GLOSSARY_TERMS_RESOURCE, MCPResourceDef)


class TestRulesResource:
    """Tests for the rules://{domain}/ resource definition."""

    def test_uri(self):
        assert RULES_RESOURCE.uri == "rules://{domain}/"

    def test_description(self):
        assert "rule" in RULES_RESOURCE.description.lower()

    def test_content_type_default(self):
        assert RULES_RESOURCE.content_type == "application/json"

    def test_is_mcp_resource_def(self):
        assert isinstance(RULES_RESOURCE, MCPResourceDef)


# ── Server config tests ──────────────────────────────────────────────


class TestKnowledgeMCPServerConfig:
    """Tests for the knowledge_mcp_config server configuration."""

    def test_name(self):
        assert knowledge_mcp_config.name == "Knowledge MCP Server"

    def test_server_type(self):
        assert knowledge_mcp_config.server_type == "knowledge"

    def test_transport(self):
        assert knowledge_mcp_config.transport == "sse"

    def test_host(self):
        assert knowledge_mcp_config.host == "0.0.0.0"

    def test_port(self):
        assert knowledge_mcp_config.port == 8082

    def test_status_default(self):
        assert knowledge_mcp_config.status == "active"

    def test_tool_count(self):
        assert len(knowledge_mcp_config.tools) == 3

    def test_tool_names(self):
        names = {t.name for t in knowledge_mcp_config.tools}
        assert names == EXPECTED_TOOLS

    def test_resource_count(self):
        assert len(knowledge_mcp_config.resources) == 2

    def test_resource_uris(self):
        uris = {r.uri for r in knowledge_mcp_config.resources}
        assert uris == EXPECTED_RESOURCES

    def test_is_mcp_server_config(self):
        assert isinstance(knowledge_mcp_config, MCPServerConfig)

    def test_metadata_default(self):
        assert isinstance(knowledge_mcp_config.metadata, dict)
        assert knowledge_mcp_config.metadata == {}

    def test_registered_at_is_set(self):
        assert knowledge_mcp_config.registered_at is not None

    def test_last_health_check_default(self):
        assert knowledge_mcp_config.last_health_check is None


class TestKnowledgeMCPServerToolsNoOverlap:
    """Each tool serves a distinct purpose — verify no schema confusion."""

    def test_search_terms_vs_match_rules(self):
        """search_terms and match_rules serve different knowledge domains."""
        # search_terms uses GlossaryManager, match_rules uses RuleEngine
        assert SEARCH_TERMS_TOOL.name != MATCH_RULES_TOOL.name
        assert (
            SEARCH_TERMS_TOOL.input_schema["properties"]["threshold"]["default"]
            != MATCH_RULES_TOOL.input_schema["properties"]["threshold"]["default"]
        )

    def test_lookup_term_unique(self):
        """lookup_term is the only tool requiring 'term' (not query_text)."""
        assert "term" in LOOKUP_TERM_TOOL.input_schema["required"]
        assert "query_text" not in LOOKUP_TERM_TOOL.input_schema["required"]

    def test_all_tools_have_distinct_names(self):
        names = [
            SEARCH_TERMS_TOOL.name,
            MATCH_RULES_TOOL.name,
            LOOKUP_TERM_TOOL.name,
        ]
        assert len(names) == len(set(names))

    def test_all_tools_have_descriptions(self):
        for tool in knowledge_mcp_config.tools:
            assert tool.description, f"{tool.name} has no description"


# ── Registry integration tests ───────────────────────────────────────


class TestKnowledgeMCPServerRegistry:
    """Tests for registering the Knowledge MCP Server with the registry."""

    @pytest.fixture(autouse=True)
    def _clean_registry(self):
        """Ensure registry is clean before and after each test."""
        MCPServerRegistry.reset()
        yield
        MCPServerRegistry.reset()

    def test_register(self):
        MCPServerRegistry.register(knowledge_mcp_config)
        names = MCPServerRegistry.list_names()
        assert "Knowledge MCP Server" in names

    def test_get_after_register(self):
        MCPServerRegistry.register(knowledge_mcp_config)
        server = MCPServerRegistry.get("Knowledge MCP Server")
        assert server is knowledge_mcp_config
        assert server.server_type == "knowledge"

    def test_get_missing_raises(self):
        with pytest.raises(KeyError, match="Knowledge MCP Server"):
            MCPServerRegistry.get("Knowledge MCP Server")

    def test_unregister(self):
        MCPServerRegistry.register(knowledge_mcp_config)
        removed = MCPServerRegistry.unregister("Knowledge MCP Server")
        assert removed is knowledge_mcp_config
        assert MCPServerRegistry.list_names() == []

    def test_unregister_missing(self):
        assert MCPServerRegistry.unregister("Knowledge MCP Server") is None

    def test_list_all(self):
        MCPServerRegistry.register(knowledge_mcp_config)
        all_servers = MCPServerRegistry.list_all()
        assert len(all_servers) == 1
        assert all_servers[0] is knowledge_mcp_config

    def test_list_active(self):
        MCPServerRegistry.register(knowledge_mcp_config)
        active = MCPServerRegistry.list_active()
        assert len(active) == 1
        assert active[0] is knowledge_mcp_config

    def test_get_server_tools(self):
        MCPServerRegistry.register(knowledge_mcp_config)
        tools = MCPServerRegistry.get_server_tools("Knowledge MCP Server")
        assert len(tools) == 3
        tool_names = {t.name for t in tools}
        assert tool_names == EXPECTED_TOOLS

    def test_get_server_resources(self):
        MCPServerRegistry.register(knowledge_mcp_config)
        resources = MCPServerRegistry.get_server_resources("Knowledge MCP Server")
        assert len(resources) == 2
        resource_uris = {r.uri for r in resources}
        assert resource_uris == EXPECTED_RESOURCES

    def test_update_health(self):
        MCPServerRegistry.register(knowledge_mcp_config)
        MCPServerRegistry.update_health("Knowledge MCP Server", "error")
        server = MCPServerRegistry.get("Knowledge MCP Server")
        assert server.status == "error"  # type: ignore[comparison-overlap]
        assert server.last_health_check is not None

    def test_multiple_servers(self):
        """Knowledge MCP can coexist with other server types in registry."""
        db_config = MCPServerConfig(
            name="Database MCP Server",
            server_type="database",
        )
        MCPServerRegistry.register(db_config)
        MCPServerRegistry.register(knowledge_mcp_config)
        assert len(MCPServerRegistry.list_names()) == 2
        assert "Knowledge MCP Server" in MCPServerRegistry.list_names()
        assert "Database MCP Server" in MCPServerRegistry.list_names()

    def test_list_by_type(self):
        """Can filter servers by server_type after registration."""
        MCPServerRegistry.register(knowledge_mcp_config)
        all_servers = MCPServerRegistry.list_all()
        knowledge_servers = [
            s for s in all_servers if s.server_type == "knowledge"
        ]
        assert len(knowledge_servers) == 1
        assert knowledge_servers[0].name == "Knowledge MCP Server"


# ── Edge cases ───────────────────────────────────────────────────────


class TestKnowledgeMCPServerEdgeCases:
    """Edge case tests for the Knowledge MCP Server definitions."""

    def test_tool_server_id_default(self):
        for tool in knowledge_mcp_config.tools:
            assert tool.server_id == ""

    def test_resource_server_id_default(self):
        for resource in knowledge_mcp_config.resources:
            assert resource.server_id == ""

    def test_config_immutable_tools_list(self):
        """Verify tools list is a copy, not a shared reference."""
        config_tools = knowledge_mcp_config.tools
        assert isinstance(config_tools, list)
        assert len(config_tools) == 3

    def test_all_tools_have_valid_input_schema(self):
        for tool in knowledge_mcp_config.tools:
            schema = tool.input_schema
            assert schema["type"] == "object"
            assert isinstance(schema.get("properties", {}), dict)
            assert isinstance(schema.get("required", []), list)

    def test_no_duplicate_uris(self):
        uris = [r.uri for r in knowledge_mcp_config.resources]
        assert len(uris) == len(set(uris))

    def test_port_does_not_conflict_with_db_server(self):
        """Knowledge MCP on 8082, DB MCP on 8081 — no conflict."""
        from app.mcp.servers.db_server import db_mcp_config

        assert knowledge_mcp_config.port != db_mcp_config.port
        assert knowledge_mcp_config.port == 8082
        assert db_mcp_config.port == 8081
