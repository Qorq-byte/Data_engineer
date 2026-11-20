"""Tenant scoping for node-local SQLite sessions."""

from __future__ import annotations

from app.auth.tenant import reset_current_tenant_id, set_current_tenant_id
from app.harness.session import AdvancedSQLiteSession


def test_sqlite_session_keeps_legacy_text_and_separates_tenant_keys(tmp_path) -> None:
    path = str(tmp_path / "sessions.db")
    legacy = AdvancedSQLiteSession(path)
    legacy.set_summary("tenant-4:s1", "default summary")
    legacy.save_turn("tenant-4:s1", {"value": "default turn"})
    legacy.close()

    store = AdvancedSQLiteSession(path)
    try:
        tenant = set_current_tenant_id(4)
        try:
            assert not store.is_active("s1")
            assert store.get_summary("s1") == ""
            assert store.get_turns("s1") == []
            store.set_summary("s1", "tenant four summary")
            store.save_turn("s1", {"value": "tenant four turn"})
            assert store.is_active("s1")
            assert store.get_turns("s1") == [{"value": "tenant four turn"}]
        finally:
            reset_current_tenant_id(tenant)

        assert store.get_turns("s1") == []
        assert store.get_summary("tenant-4:s1") == "default summary"
        assert store.get_turns("tenant-4:s1") == [{"value": "default turn"}]
        store.clear("tenant-4:s1")
        tenant = set_current_tenant_id(4)
        try:
            assert store.get_summary("s1") == "tenant four summary"
            store.clear("s1")
            assert store.get_turns("s1") == []
        finally:
            reset_current_tenant_id(tenant)
    finally:
        store.close()
