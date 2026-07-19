"""AdvancedSQLiteSession — memory and session management.

See SPEC §3.4.8 (Context & Memory management).

Key design rules:
  - Builtin subagents (gen_sql, schema_linking, ...): memory=false by default
  - Chat node + user subagents: memory=true by default
  - Sub-agent: :memory: ephemeral, top-level: persistent to disk
  - Memory isolated by node_name — different node types never interfere
"""

from __future__ import annotations

import json
import sqlite3
import time
from collections.abc import Callable
from pathlib import Path

# ── SQL schema ────────────────────────────────────────────────────────────

CREATE_SESSIONS_SQL = """
CREATE TABLE IF NOT EXISTS sessions (
    node_name       TEXT NOT NULL,
    session_id      TEXT NOT NULL,
    summary         TEXT NOT NULL DEFAULT '',
    created_at      REAL NOT NULL,
    last_active_at  REAL NOT NULL,
    PRIMARY KEY (node_name, session_id)
);
"""

CREATE_TURNS_SQL = """
CREATE TABLE IF NOT EXISTS turns (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    node_name   TEXT NOT NULL,
    session_id  TEXT NOT NULL,
    turn_data   TEXT NOT NULL,
    created_at  REAL NOT NULL
);
"""

INDEX_TURNS_SESSION = (
    "CREATE INDEX IF NOT EXISTS idx_turns_session ON turns(node_name, session_id);"
)


class AdvancedSQLiteSession:
    """Manages conversation session state with a SQLite backend.

    Supports two modes:
      - ``:memory:``   — ephemeral, zero persistence, for sub-agents;
        all data is gone after :meth:`close`
      - path on disk — persistent, for top-level chat interactions;
        parent directories are created automatically

    Storage is isolated by ``node_name``: multiple instances sharing the
    same database file but constructed with different node names never see
    each other's sessions or turns.

    Sessions expire after ``session_ttl`` seconds of inactivity (checked by
    :meth:`is_active`). A ``clock`` callable can be injected for testing;
    it defaults to :func:`time.time`.
    """

    def __init__(
        self,
        db_path: str = ":memory:",
        auto_save: bool = True,
        session_ttl: int = 3600,
        node_name: str = "default",
        clock: Callable[[], float] | None = None,
    ):
        self.db_path = db_path
        self.auto_save = auto_save
        self.session_ttl = session_ttl
        self.node_name = node_name
        self._clock = clock or time.time
        self._is_ephemeral = db_path == ":memory:"

        if not self._is_ephemeral:
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)

        self._conn: sqlite3.Connection | None = sqlite3.connect(
            db_path, check_same_thread=False
        )
        self._conn.row_factory = sqlite3.Row
        self._ensure_schema()

    # ── internals ──────────────────────────────────────────────────────

    def _get_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            raise RuntimeError("Session is closed")
        return self._conn

    def _ensure_schema(self) -> None:
        conn = self._get_conn()
        conn.execute(CREATE_SESSIONS_SQL)
        conn.execute(CREATE_TURNS_SQL)
        conn.execute(INDEX_TURNS_SESSION)
        conn.commit()

    def _maybe_commit(self, conn: sqlite3.Connection) -> None:
        if self.auto_save:
            conn.commit()

    def _touch_session(self, conn: sqlite3.Connection, session_id: str) -> None:
        """Create the session row if missing and refresh its activity timestamp."""
        now = self._clock()
        conn.execute(
            """
            INSERT INTO sessions (node_name, session_id, summary, created_at, last_active_at)
            VALUES (?, ?, '', ?, ?)
            ON CONFLICT (node_name, session_id)
            DO UPDATE SET last_active_at = excluded.last_active_at
            """,
            (self.node_name, session_id, now, now),
        )

    # ── public API ─────────────────────────────────────────────────────

    def is_active(self, session_id: str) -> bool:
        """Check if a session exists and hasn't expired (TTL on last activity)."""
        row = self._get_conn().execute(
            "SELECT last_active_at FROM sessions WHERE node_name = ? AND session_id = ?",
            (self.node_name, session_id),
        ).fetchone()
        if row is None:
            return False
        return (self._clock() - row["last_active_at"]) <= self.session_ttl

    def get_summary(self, session_id: str) -> str:
        """Return a compressed summary of the session history ('' if none)."""
        row = self._get_conn().execute(
            "SELECT summary FROM sessions WHERE node_name = ? AND session_id = ?",
            (self.node_name, session_id),
        ).fetchone()
        return row["summary"] if row is not None else ""

    def set_summary(self, session_id: str, summary: str) -> None:
        """Set the summary for a session, creating the session if needed."""
        conn = self._get_conn()
        self._touch_session(conn, session_id)
        conn.execute(
            "UPDATE sessions SET summary = ? WHERE node_name = ? AND session_id = ?",
            (summary, self.node_name, session_id),
        )
        self._maybe_commit(conn)

    def save_turn(self, session_id: str, turn_data: dict) -> None:
        """Save a conversation turn (JSON-serialized) and refresh session activity."""
        conn = self._get_conn()
        self._touch_session(conn, session_id)
        conn.execute(
            "INSERT INTO turns (node_name, session_id, turn_data, created_at) VALUES (?, ?, ?, ?)",
            (
                self.node_name,
                session_id,
                json.dumps(turn_data, ensure_ascii=False),
                self._clock(),
            ),
        )
        self._maybe_commit(conn)

    def get_turns(self, session_id: str) -> list[dict]:
        """Return all turns of a session in insertion order."""
        rows = self._get_conn().execute(
            "SELECT turn_data FROM turns WHERE node_name = ? AND session_id = ? ORDER BY id",
            (self.node_name, session_id),
        ).fetchall()
        return [json.loads(row["turn_data"]) for row in rows]

    def clear(self, session_id: str) -> None:
        """Delete a session and all of its turns (this node_name only)."""
        conn = self._get_conn()
        conn.execute(
            "DELETE FROM turns WHERE node_name = ? AND session_id = ?",
            (self.node_name, session_id),
        )
        conn.execute(
            "DELETE FROM sessions WHERE node_name = ? AND session_id = ?",
            (self.node_name, session_id),
        )
        self._maybe_commit(conn)

    def close(self) -> None:
        """Close the connection (flush for persistent mode; :memory: data is lost).

        Idempotent — safe to call multiple times.
        """
        if self._conn is None:
            return
        self._conn.commit()
        self._conn.close()
        self._conn = None
