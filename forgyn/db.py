"""SQLite persistence for conversations, skills, and schedules."""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
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
    message        TEXT,
    status         TEXT NOT NULL DEFAULT 'active',
    created_at     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS outbox (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    channel    TEXT,
    recipient  TEXT,
    text       TEXT NOT NULL,
    created_at TEXT NOT NULL,
    delivered  INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS memories (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    key        TEXT NOT NULL UNIQUE,
    value      TEXT NOT NULL,
    category   TEXT NOT NULL DEFAULT 'knowledge',
    embedding  TEXT,
    source     TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""

# Category-based TTL in days (None = never expires)
MEMORY_TTL = {
    "identity": None,
    "preference": 90,
    "context": 7,
    "knowledge": 30,
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def init_db(path: Path) -> sqlite3.Connection:
    """Create database and tables. Returns a connection with WAL mode and foreign keys."""
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(SCHEMA)
    _migrate(conn)
    conn.commit()
    return conn


def _migrate(conn: sqlite3.Connection) -> None:
    """Apply incremental schema migrations for existing databases."""
    cols = {row[1] for row in conn.execute("PRAGMA table_info(schedules)").fetchall()}
    if "message" not in cols:
        conn.execute("ALTER TABLE schedules ADD COLUMN message TEXT")
    if "channel" not in cols:
        conn.execute("ALTER TABLE schedules ADD COLUMN channel TEXT")
    if "recipient" not in cols:
        conn.execute("ALTER TABLE schedules ADD COLUMN recipient TEXT")
    # Ensure the _system pseudo-skill exists for message-only schedules (reminders)
    conn.execute(
        "INSERT OR IGNORE INTO skills (name, status, manifest, created_at, updated_at)"
        " VALUES ('_system', 'active', '{}', ?, ?)",
        (_now(), _now()),
    )


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
    """List all user-facing skills (excludes internal _system skill)."""
    if status:
        rows = db.execute(
            "SELECT * FROM skills WHERE status = ? AND name != '_system' ORDER BY name", (status,),
        ).fetchall()
    else:
        rows = db.execute(
            "SELECT * FROM skills WHERE name != '_system' ORDER BY name"
        ).fetchall()
    return [_row_to_dict(r) for r in rows]


# --- Schedules ---

def save_schedule(
    db: sqlite3.Connection,
    skill_name: str,
    schedule_type: str,
    schedule_value: str,
    next_run: str | None,
    message: str | None = None,
    channel: str | None = None,
    recipient: str | None = None,
) -> int:
    """Create a schedule. Returns the schedule row ID."""
    cur = db.execute(
        "INSERT INTO schedules (skill_name, schedule_type, schedule_value, next_run, message, channel, recipient, status, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, 'active', ?)",
        (skill_name, schedule_type, schedule_value, next_run, message, channel, recipient, _now()),
    )
    db.commit()
    return cur.lastrowid


def list_schedules(db: sqlite3.Connection) -> list[dict]:
    """List all schedules (active, completed, paused)."""
    rows = db.execute(
        "SELECT * FROM schedules ORDER BY created_at DESC"
    ).fetchall()
    return [_row_to_dict(r) for r in rows]


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


# --- Memories ---

def save_memory(
    db: sqlite3.Connection,
    key: str,
    value: str,
    category: str = "knowledge",
    embedding: list[float] | None = None,
    source: str | None = None,
) -> None:
    """Insert or update a memory."""
    now = _now()
    emb_json = json.dumps(embedding) if embedding else None
    db.execute(
        "INSERT INTO memories (key, value, category, embedding, source, created_at, updated_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)"
        " ON CONFLICT(key) DO UPDATE SET value=?, category=?, embedding=?, source=?, updated_at=?",
        (key, value, category, emb_json, source, now, now,
         value, category, emb_json, source, now),
    )
    db.commit()


def get_memory(db: sqlite3.Connection, key: str) -> dict | None:
    """Fetch a single memory by key."""
    row = db.execute("SELECT * FROM memories WHERE key = ?", (key,)).fetchone()
    return _row_to_dict(row) if row else None


def list_memories(db: sqlite3.Connection, category: str | None = None) -> list[dict]:
    """List all memories, optionally filtered by category."""
    if category:
        rows = db.execute(
            "SELECT * FROM memories WHERE category = ? ORDER BY updated_at DESC", (category,)
        ).fetchall()
    else:
        rows = db.execute("SELECT * FROM memories ORDER BY updated_at DESC").fetchall()
    return [_row_to_dict(r) for r in rows]


def delete_memory(db: sqlite3.Connection, key: str) -> bool:
    """Delete a memory by key. Returns True if a row was deleted."""
    cur = db.execute("DELETE FROM memories WHERE key = ?", (key,))
    db.commit()
    return cur.rowcount > 0


def delete_expired_memories(db: sqlite3.Connection) -> int:
    """Delete memories past their category TTL. Returns count of deleted rows."""
    now = datetime.now(timezone.utc)
    total = 0
    for category, ttl_days in MEMORY_TTL.items():
        if ttl_days is None:
            continue
        cutoff = (now - timedelta(days=ttl_days)).isoformat()
        cur = db.execute(
            "DELETE FROM memories WHERE category = ? AND updated_at < ?",
            (category, cutoff),
        )
        total += cur.rowcount
    db.commit()
    return total


def get_memories_with_embeddings(db: sqlite3.Connection) -> list[dict]:
    """Fetch context + knowledge memories that have embeddings for similarity search."""
    rows = db.execute(
        "SELECT * FROM memories WHERE category IN ('context', 'knowledge') AND embedding IS NOT NULL"
        " ORDER BY updated_at DESC"
    ).fetchall()
    return [_row_to_dict(r) for r in rows]


# --- Outbox ---

def push_outbox(
    db: sqlite3.Connection, text: str, channel: str | None = None, recipient: str | None = None,
) -> int:
    """Insert a pending outbox message. Returns the row ID."""
    cur = db.execute(
        "INSERT INTO outbox (channel, recipient, text, created_at) VALUES (?, ?, ?, ?)",
        (channel, recipient, text, _now()),
    )
    db.commit()
    return cur.lastrowid


def drain_outbox(db: sqlite3.Connection, channel: str | None = None) -> list[dict]:
    """Fetch and mark delivered all undelivered messages for a channel (or all)."""
    if channel:
        rows = db.execute(
            "SELECT * FROM outbox WHERE delivered = 0 AND (channel IS NULL OR channel = ?) ORDER BY id",
            (channel,),
        ).fetchall()
    else:
        rows = db.execute(
            "SELECT * FROM outbox WHERE delivered = 0 ORDER BY id"
        ).fetchall()
    if rows:
        ids = [r["id"] for r in rows]
        placeholders = ",".join("?" * len(ids))
        db.execute(f"UPDATE outbox SET delivered = 1 WHERE id IN ({placeholders})", ids)
        db.commit()
    return [_row_to_dict(r) for r in rows]


def clear_delivered(db: sqlite3.Connection) -> int:
    """Delete old delivered outbox messages. Returns count deleted."""
    cur = db.execute("DELETE FROM outbox WHERE delivered = 1")
    db.commit()
    return cur.rowcount


# --- Helpers ---

def _row_to_dict(row: sqlite3.Row) -> dict:
    return dict(row)
