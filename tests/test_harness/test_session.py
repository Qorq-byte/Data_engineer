"""Tests for AdvancedSQLiteSession — real SQLite, no mocks.

Covers: memory/disk modes, TTL expiry (injected clock), node_name isolation,
persistence across reopen, and the turn/summary APIs.
"""

from app.harness.session import AdvancedSQLiteSession


class FakeClock:
    """Injectable clock — advances only when told to."""

    def __init__(self, start: float = 1_000.0):
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


# ── construction / modes ──────────────────────────────────────────────────


def test_init_signature_backward_compatible():
    """Existing positional call style (db_path, auto_save, session_ttl) still works."""
    session = AdvancedSQLiteSession(":memory:", False, 120)
    assert session.db_path == ":memory:"
    assert session.auto_save is False
    assert session.session_ttl == 120
    assert session.node_name == "default"
    session.close()


def test_memory_mode_save_and_get_turns():
    session = AdvancedSQLiteSession(":memory:")
    session.save_turn("s1", {"role": "user", "content": "hello"})
    session.save_turn("s1", {"role": "assistant", "content": "hi"})
    turns = session.get_turns("s1")
    assert turns == [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "hi"},
    ]
    session.close()


def test_memory_mode_data_gone_after_close():
    session = AdvancedSQLiteSession(":memory:")
    session.save_turn("s1", {"content": "ephemeral"})
    session.close()
    reopened = AdvancedSQLiteSession(":memory:")
    assert not reopened.is_active("s1")
    assert reopened.get_turns("s1") == []
    reopened.close()


def test_disk_mode_creates_parent_dirs(tmp_path):
    db_path = tmp_path / "nested" / "deeper" / "sessions.db"
    session = AdvancedSQLiteSession(str(db_path))
    session.save_turn("s1", {"content": "on disk"})
    session.close()
    assert db_path.exists()


def test_disk_persistence_turns_survive_reopen(tmp_path):
    db_path = str(tmp_path / "sessions.db")
    session = AdvancedSQLiteSession(db_path)
    session.save_turn("s1", {"content": "first"})
    session.save_turn("s1", {"content": "second"})
    session.close()

    reopened = AdvancedSQLiteSession(db_path)
    assert reopened.is_active("s1")
    assert reopened.get_turns("s1") == [{"content": "first"}, {"content": "second"}]
    reopened.close()


def test_disk_persistence_summary_survives_reopen(tmp_path):
    db_path = str(tmp_path / "sessions.db")
    session = AdvancedSQLiteSession(db_path)
    session.set_summary("s1", "user asked about orders")
    session.close()

    reopened = AdvancedSQLiteSession(db_path)
    assert reopened.get_summary("s1") == "user asked about orders"
    reopened.close()


def test_close_is_idempotent():
    session = AdvancedSQLiteSession(":memory:")
    session.close()
    session.close()  # must not raise


# ── is_active / TTL ───────────────────────────────────────────────────────


def test_is_active_false_for_unknown_session():
    session = AdvancedSQLiteSession(":memory:")
    assert not session.is_active("nope")
    session.close()


def test_is_active_true_after_save_turn():
    session = AdvancedSQLiteSession(":memory:")
    session.save_turn("s1", {"content": "hello"})
    assert session.is_active("s1")
    session.close()


def test_is_active_true_within_ttl():
    clock = FakeClock()
    session = AdvancedSQLiteSession(":memory:", session_ttl=100, clock=clock)
    session.save_turn("s1", {"content": "hello"})
    clock.advance(99)
    assert session.is_active("s1")
    session.close()


def test_is_active_false_after_ttl_expired():
    clock = FakeClock()
    session = AdvancedSQLiteSession(":memory:", session_ttl=100, clock=clock)
    session.save_turn("s1", {"content": "hello"})
    clock.advance(101)
    assert not session.is_active("s1")
    session.close()


def test_save_turn_refreshes_activity():
    clock = FakeClock()
    session = AdvancedSQLiteSession(":memory:", session_ttl=100, clock=clock)
    session.save_turn("s1", {"content": "one"})
    clock.advance(90)
    session.save_turn("s1", {"content": "two"})  # refreshes last_active_at
    clock.advance(90)  # 180s since first turn, 90s since second
    assert session.is_active("s1")
    session.close()


# ── summary API ───────────────────────────────────────────────────────────


def test_get_summary_empty_by_default():
    session = AdvancedSQLiteSession(":memory:")
    assert session.get_summary("unknown") == ""
    session.save_turn("s1", {"content": "hello"})
    assert session.get_summary("s1") == ""
    session.close()


def test_set_summary_and_get_summary():
    session = AdvancedSQLiteSession(":memory:")
    session.save_turn("s1", {"content": "hello"})
    session.set_summary("s1", "greeting exchanged")
    assert session.get_summary("s1") == "greeting exchanged"
    session.set_summary("s1", "updated summary")
    assert session.get_summary("s1") == "updated summary"
    session.close()


def test_set_summary_creates_session():
    session = AdvancedSQLiteSession(":memory:")
    session.set_summary("s1", "summary without turns")
    assert session.is_active("s1")
    assert session.get_summary("s1") == "summary without turns"
    session.close()


# ── clear ─────────────────────────────────────────────────────────────────


def test_clear_removes_turns_and_summary():
    session = AdvancedSQLiteSession(":memory:")
    session.save_turn("s1", {"content": "hello"})
    session.set_summary("s1", "a summary")
    session.clear("s1")
    assert not session.is_active("s1")
    assert session.get_turns("s1") == []
    assert session.get_summary("s1") == ""
    session.close()


def test_clear_only_affects_target_session():
    session = AdvancedSQLiteSession(":memory:")
    session.save_turn("s1", {"content": "one"})
    session.save_turn("s2", {"content": "two"})
    session.clear("s1")
    assert not session.is_active("s1")
    assert session.is_active("s2")
    assert session.get_turns("s2") == [{"content": "two"}]
    session.close()


# ── node_name isolation ───────────────────────────────────────────────────


def test_node_name_isolation_turns(tmp_path):
    db_path = str(tmp_path / "shared.db")
    chat = AdvancedSQLiteSession(db_path, node_name="chat")
    gen_sql = AdvancedSQLiteSession(db_path, node_name="gen_sql")

    chat.save_turn("s1", {"content": "chat turn"})
    gen_sql.save_turn("s1", {"content": "gen_sql turn"})

    assert chat.get_turns("s1") == [{"content": "chat turn"}]
    assert gen_sql.get_turns("s1") == [{"content": "gen_sql turn"}]
    chat.close()
    gen_sql.close()


def test_node_name_isolation_summary_and_clear(tmp_path):
    db_path = str(tmp_path / "shared.db")
    chat = AdvancedSQLiteSession(db_path, node_name="chat")
    gen_sql = AdvancedSQLiteSession(db_path, node_name="gen_sql")

    chat.set_summary("s1", "chat summary")
    gen_sql.set_summary("s1", "gen_sql summary")
    assert chat.get_summary("s1") == "chat summary"
    assert gen_sql.get_summary("s1") == "gen_sql summary"

    chat.clear("s1")  # must not touch gen_sql's data
    assert not chat.is_active("s1")
    assert gen_sql.is_active("s1")
    assert gen_sql.get_summary("s1") == "gen_sql summary"
    chat.close()
    gen_sql.close()


# ── turn data serialization ───────────────────────────────────────────────


def test_turn_data_json_roundtrip_nested_and_unicode():
    session = AdvancedSQLiteSession(":memory:")
    turn = {
        "role": "user",
        "content": "上季度各区域销售额排行",
        "meta": {"tokens": 42, "tags": ["nl2sql", "中文"], "nested": {"ok": True}},
    }
    session.save_turn("s1", turn)
    assert session.get_turns("s1") == [turn]
    session.close()


def test_turns_isolated_between_sessions():
    session = AdvancedSQLiteSession(":memory:")
    session.save_turn("s1", {"content": "for s1"})
    session.save_turn("s2", {"content": "for s2"})
    assert session.get_turns("s1") == [{"content": "for s1"}]
    assert session.get_turns("s2") == [{"content": "for s2"}]
    session.close()


def test_auto_save_false_persists_on_close(tmp_path):
    db_path = str(tmp_path / "deferred.db")
    session = AdvancedSQLiteSession(db_path, auto_save=False)
    session.save_turn("s1", {"content": "deferred write"})
    session.close()  # close() must flush pending writes

    reopened = AdvancedSQLiteSession(db_path)
    assert reopened.get_turns("s1") == [{"content": "deferred write"}]
    reopened.close()
