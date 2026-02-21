"""SQLite persistence for conversations, skills, and schedules."""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS conversations (
    id         TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    title      TEXT
);

CREATE TABLE IF NOT EXISTS messages (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id TEXT NOT NULL REFERENCES conversations(id),
    role            TEXT NOT NULL,
    content         TEXT NOT NULL,
    tool_calls      TEXT,
    tool_call_id    TEXT,
    created_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS skills (
    name       TEXT PRIMARY KEY,
    status     TEXT NOT NULL DEFAULT 'active',
    manifest   TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS schedules (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    skill_name     TEXT NOT NULL REFERENCES skills(name),
    schedule_type  TEXT NOT NULL,
    schedule_value TEXT NOT NULL,
    next_run       TEXT,
    status         TEXT NOT NULL DEFAULT 'active',
    created_at     TEXT NOT NULL
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def init_db(path: Path) -> sqlite3.Connection:
    """Create database and tables. Returns a connection with WAL mode and foreign keys."""
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(SCHEMA)
    conn.commit()
    return conn


# --- Conversations ---

def create_conversation(db: sqlite3.Connection, title: str | None = None) -> str:
    """Create a new conversation. Returns its UUID."""
    cid = str(uuid.uuid4())
    db.execute(
        "INSERT INTO conversations (id, created_at, title) VALUES (?, ?, ?)",
        (cid, _now(), title),
    )
    db.commit()
    return cid


def add_message(
    db: sqlite3.Connection,
    conversation_id: str,
    role: str,
    content: str,
    tool_calls: list | None = None,
    tool_call_id: str | None = None,
) -> int:
    """Insert a message into a conversation. Returns the message row ID."""
    cur = db.execute(
        "INSERT INTO messages (conversation_id, role, content, tool_calls, tool_call_id, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        (
            conversation_id,
            role,
            content,
            json.dumps(tool_calls) if tool_calls else None,
            tool_call_id,
            _now(),
        ),
    )
    db.commit()
    return cur.lastrowid


def get_messages(
    db: sqlite3.Connection, conversation_id: str, limit: int | None = None
) -> list[dict]:
    """Fetch messages for a conversation in chronological order."""
    query = "SELECT * FROM messages WHERE conversation_id = ? ORDER BY id ASC"
    params: list = [conversation_id]
    if limit:
        query += " LIMIT ?"
        params.append(limit)
    rows = db.execute(query, params).fetchall()
    return [_row_to_dict(r) for r in rows]


# --- Skills ---

def save_skill(db: sqlite3.Connection, name: str, manifest: dict, status: str = "active") -> None:
    """Insert or update a skill record."""
    now = _now()
    db.execute(
        "INSERT INTO skills (name, status, manifest, created_at, updated_at)"
        " VALUES (?, ?, ?, ?, ?)"
        " ON CONFLICT(name) DO UPDATE SET status=?, manifest=?, updated_at=?",
        (name, status, json.dumps(manifest), now, now, status, json.dumps(manifest), now),
    )
    db.commit()


def get_skill(db: sqlite3.Connection, name: str) -> dict | None:
    """Fetch a single skill by name."""
    row = db.execute("SELECT * FROM skills WHERE name = ?", (name,)).fetchone()
    return _row_to_dict(row) if row else None


def list_skills(db: sqlite3.Connection, status: str | None = None) -> list[dict]:
    """List all skills, optionally filtered by status."""
    if status:
        rows = db.execute("SELECT * FROM skills WHERE status = ? ORDER BY name", (status,)).fetchall()
    else:
        rows = db.execute("SELECT * FROM skills ORDER BY name").fetchall()
    return [_row_to_dict(r) for r in rows]


# --- Schedules ---

def save_schedule(
    db: sqlite3.Connection,
    skill_name: str,
    schedule_type: str,
    schedule_value: str,
    next_run: str | None,
) -> int:
    """Create a schedule. Returns the schedule row ID."""
    cur = db.execute(
        "INSERT INTO schedules (skill_name, schedule_type, schedule_value, next_run, status, created_at)"
        " VALUES (?, ?, ?, ?, 'active', ?)",
        (skill_name, schedule_type, schedule_value, next_run, _now()),
    )
    db.commit()
    return cur.lastrowid


def get_due_schedules(db: sqlite3.Connection) -> list[dict]:
    """Get all active schedules whose next_run is in the past."""
    now = _now()
    rows = db.execute(
        "SELECT * FROM schedules WHERE status = 'active' AND next_run IS NOT NULL AND next_run <= ?"
        " ORDER BY next_run ASC",
        (now,),
    ).fetchall()
    return [_row_to_dict(r) for r in rows]


def update_schedule(db: sqlite3.Connection, schedule_id: int, **fields) -> None:
    """Update fields on a schedule (next_run, status, etc.)."""
    sets = ", ".join(f"{k} = ?" for k in fields)
    vals = list(fields.values()) + [schedule_id]
    db.execute(f"UPDATE schedules SET {sets} WHERE id = ?", vals)
    db.commit()


# --- Helpers ---

def _row_to_dict(row: sqlite3.Row) -> dict:
    return dict(row)
