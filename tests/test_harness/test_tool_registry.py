"""Tests for the harness ToolRegistry (② Tool integration)."""

import pytest

from app.harness.tool_registry import ToolRegistry


class TestToolRegistry:
    def test_register_and_call(self):
        reg = ToolRegistry()
        reg.register("add", lambda a, b: a + b, description="adds")
        assert reg.call("add", 2, 3) == 5

    def test_get_entry_metadata(self):
        reg = ToolRegistry()
        reg.register("t", lambda: None, description="d", source="func")
        entry = reg.get("t")
        assert entry is not None
        assert (entry.name, entry.description, entry.source) == ("t", "d", "func")

    def test_get_missing_returns_none(self):
        assert ToolRegistry().get("nope") is None

    def test_call_missing_raises_keyerror(self):
        with pytest.raises(KeyError, match="not registered"):
            ToolRegistry().call("nope")

    def test_latest_registration_wins(self):
        reg = ToolRegistry()
        reg.register("t", lambda: "old")
        reg.register("t", lambda: "new")
        assert reg.call("t") == "new"

    def test_register_mcp_server(self):
        reg = ToolRegistry()
        reg.register_mcp_server("db", {"list_tables": lambda: ["users"]})
        entry = reg.get("list_tables")
        assert entry is not None
        assert entry.source == "mcp"
        assert entry.metadata["server"] == "db"
        assert reg.call("list_tables") == ["users"]

    def test_list_all_sorted(self):
        reg = ToolRegistry()
        reg.register("b", lambda: None)
        reg.register("a", lambda: None)
        assert reg.list_all() == ["a", "b"]

    def test_unregister_and_len_contains(self):
        reg = ToolRegistry()
        reg.register("t", lambda: None)
        assert "t" in reg
        assert len(reg) == 1
        reg.unregister("t")
        reg.unregister("t")  # no-op on absent
        assert "t" not in reg
        assert len(reg) == 0
