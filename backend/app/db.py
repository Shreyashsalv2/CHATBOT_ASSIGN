"""SQLite storage for sessions and their message history."""

import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Iterator

SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  TEXT NOT NULL,
    role        TEXT NOT NULL,      -- 'user' or 'assistant'
    content     TEXT NOT NULL,
    metadata    TEXT,               -- JSON string, assistant messages only
    created_at  TEXT NOT NULL       -- server time, ISO-8601
);
CREATE INDEX IF NOT EXISTS idx_messages_session ON messages (session_id, id);

CREATE TABLE IF NOT EXISTS sessions (
    session_id     TEXT PRIMARY KEY,
    system_prompt  TEXT,            -- per-session override; NULL means the default prompt
    created_at     TEXT NOT NULL
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Database:
    def __init__(self, path: str):
        self.path = path

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        try:
            with conn:  # commits on success, rolls back on error
                yield conn
        finally:
            conn.close()

    def init_schema(self) -> None:
        directory = os.path.dirname(os.path.abspath(self.path))
        os.makedirs(directory, exist_ok=True)
        with self._connect() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript(SCHEMA)

    def ping(self) -> bool:
        try:
            with self._connect() as conn:
                conn.execute("SELECT 1").fetchone()
            return True
        except sqlite3.Error:
            return False

    def get_system_prompt(self, session_id: str) -> str | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT system_prompt FROM sessions WHERE session_id = ?", (session_id,)
            ).fetchone()
        return row["system_prompt"] if row else None

    def session_exists(self, session_id: str) -> bool:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM messages WHERE session_id = ? LIMIT 1", (session_id,)
            ).fetchone()
        return row is not None

    def get_history(self, session_id: str) -> list[dict[str, Any]]:
        """Oldest-first messages of a session, including parsed assistant metadata."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT role, content, metadata FROM messages WHERE session_id = ? ORDER BY id",
                (session_id,),
            ).fetchall()
        history = []
        for row in rows:
            message: dict[str, Any] = {"role": row["role"], "content": row["content"]}
            if row["metadata"] is not None:
                message["metadata"] = json.loads(row["metadata"])
            history.append(message)
        return history

    def save_turn(
        self,
        session_id: str,
        system_prompt: str | None,
        user_content: str,
        assistant_content: str,
        metadata: dict[str, Any],
    ) -> None:
        """Store a successful user/assistant exchange atomically."""
        now = _now()
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO sessions (session_id, system_prompt, created_at) VALUES (?, ?, ?) "
                "ON CONFLICT(session_id) DO UPDATE SET "
                "system_prompt = COALESCE(excluded.system_prompt, sessions.system_prompt)",
                (session_id, system_prompt, now),
            )
            conn.execute(
                "INSERT INTO messages (session_id, role, content, metadata, created_at) "
                "VALUES (?, 'user', ?, NULL, ?)",
                (session_id, user_content, now),
            )
            conn.execute(
                "INSERT INTO messages (session_id, role, content, metadata, created_at) "
                "VALUES (?, 'assistant', ?, ?, ?)",
                (session_id, assistant_content, json.dumps(metadata), _now()),
            )
