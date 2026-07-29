"""Shared test fixtures."""

import os
import sqlite3
import sys
from pathlib import Path

import pytest

# Keep the suite hermetic: never let real API keys (.env / environment)
# cause tests to hit live LLM endpoints — the router factory honors this.
os.environ["LLM_MOCK_MODE"] = "1"

# Ensure app is importable
sys.path.insert(0, str(Path(__file__).parent.parent))


@pytest.fixture
def test_sqlite_db(tmp_path) -> str:
    """Create a temporary SQLite database with test schema."""
    db_path = tmp_path / "test.db"
    conn = sqlite3.connect(str(db_path))

    # Load and execute test schema
    schema_path = Path(__file__).parent / "fixtures" / "test_schema.sql"
    with open(schema_path) as f:
        conn.executescript(f.read())

    conn.commit()
    conn.close()
    return str(db_path)


@pytest.fixture
def sqlite_connection(test_sqlite_db):
    """Return a sqlite3 connection to the test database."""
    conn = sqlite3.connect(test_sqlite_db)
    conn.row_factory = sqlite3.Row
    yield conn
    conn.close()


@pytest.fixture
def in_memory_sqlite():
    """Return an in-memory SQLite connection."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    yield conn
    conn.close()
