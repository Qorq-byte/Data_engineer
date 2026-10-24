"""Shared test fixtures."""

import os
import shutil
import sqlite3
import sys
from pathlib import Path

import pytest

# Keep the suite hermetic: never let real API keys (.env / environment)
# cause tests to hit live LLM endpoints — the router factory honors this.
os.environ["LLM_MOCK_MODE"] = "1"
# Audit tests explicitly enable temporary files; never write a host-configured audit DB.
os.environ["LLM_AUDIT_PATH"] = ""
os.environ["LLM_AUDIT_CANDIDATE_DIGESTS"] = "false"
os.environ["JWT_SECRET"] = "unit-test-signing-secret-never-used-outside-tests-2026"

# Ensure app is importable
sys.path.insert(0, str(Path(__file__).parent.parent))


@pytest.fixture(scope="session", autouse=True)
def isolated_api_domains(tmp_path_factory):
    """CRUD tests must never write generated domains into source configuration."""
    from app.api import domains

    original = domains.DOMAINS_DIR
    isolated = tmp_path_factory.mktemp("api_domains")
    for source in original.glob("*.yml"):
        if not source.name.startswith("test_"):
            shutil.copy2(source, isolated / source.name)
    original_manager_dir = domains._domain_manager._domain_dir
    domains.DOMAINS_DIR = isolated
    domains._domain_manager._domain_dir = isolated
    domains._domain_manager.reload()
    yield
    domains.DOMAINS_DIR = original
    domains._domain_manager._domain_dir = original_manager_dir


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
