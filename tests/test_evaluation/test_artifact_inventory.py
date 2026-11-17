"""Artifact inventory tolerates vanished real SQLite journals without hiding I/O errors."""

import sqlite3

import pytest

from app.evaluation.nl2sql_runtime import artifact_bytes


def test_sqlite_journal_disappears_after_enumeration(tmp_path):
    database = tmp_path / "audit.db"
    with sqlite3.connect(database) as connection:
        connection.execute("PRAGMA journal_mode=DELETE")
        connection.execute("CREATE TABLE usage (tokens INTEGER)")
        connection.execute("INSERT INTO usage VALUES (1)")
        journal = tmp_path / "audit.db-journal"
        assert journal.is_file()
        enumerated = list(tmp_path.rglob("*"))
        assert journal in enumerated
        connection.commit()
        assert not journal.exists()
        assert artifact_bytes(enumerated) == database.stat().st_size


def test_inventory_counts_files_ignores_directories_and_keeps_path_errors(tmp_path):
    first = tmp_path / "first.json"
    first.write_bytes(b"12345")
    second = tmp_path / "sub" / "second.json"
    second.parent.mkdir()
    second.write_bytes(b"1234567")
    assert artifact_bytes(tmp_path.rglob("*")) == 12
    # Invalid paths still fail; the inventory only tolerates vanished files.
    with pytest.raises(ValueError):
        artifact_bytes([tmp_path / "\x00"])
