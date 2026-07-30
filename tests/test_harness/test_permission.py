"""Tests for PermissionManager."""

from app.harness.permission import PermissionManager


def test_default_deny():
    pm = PermissionManager(default_level="deny")
    assert pm.check("some_tool") == "deny"
    assert pm.is_denied("some_tool")
    assert not pm.is_allowed("some_tool")


def test_default_allow():
    pm = PermissionManager(default_level="allow")
    assert pm.check("some_tool") == "allow"
    assert pm.is_allowed("some_tool")


def test_explicit_rules():
    pm = PermissionManager(
        rules=[
            {"execute_read_query": "allow"},
            {"execute_write_query": "deny"},
        ],
        default_level="deny",
    )
    assert pm.check("execute_read_query") == "allow"
    assert pm.check("execute_write_query") == "deny"
    assert pm.check("unknown_tool") == "deny"


def test_ask_level():
    pm = PermissionManager(
        rules=[{"call_llm": "ask"}],
        default_level="deny",
    )
    assert pm.check("call_llm") == "ask"
    assert not pm.is_allowed("call_llm")
    assert not pm.is_denied("call_llm")


def test_add_remove_rules():
    pm = PermissionManager(default_level="deny")
    pm.add_rule("new_tool", "allow")
    assert pm.check("new_tool") == "allow"
    pm.remove_rule("new_tool")
    assert pm.check("new_tool") == "deny"


def test_list_rules():
    pm = PermissionManager(
        rules=[{"a": "allow"}, {"b": "deny"}],
    )
    rules = pm.list_rules()
    assert rules["a"] == "allow"
    assert rules["b"] == "deny"
