"""Tests for database layer (Step 1.2)."""

from datetime import datetime, timedelta, timezone

import pytest

from forgyn.db import (
    add_message,
    create_conversation,
    get_due_schedules,
    get_messages,
    get_skill,
    init_db,
    list_skills,
    save_schedule,
    save_skill,
)


@pytest.fixture
def db(tmp_db_path):
    return init_db(tmp_db_path)


def test_init_creates_tables(db):
    tables = db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    ).fetchall()
    names = {row["name"] for row in tables}
    assert "conversations" in names
    assert "messages" in names
    assert "skills" in names
    assert "schedules" in names


def test_wal_mode_enabled(db):
    mode = db.execute("PRAGMA journal_mode").fetchone()[0]
    assert mode == "wal"


def test_conversation_crud(db):
    cid = create_conversation(db, "Test chat")
    assert isinstance(cid, str) and len(cid) > 0

    mid = add_message(db, cid, "user", "Hello")
    assert isinstance(mid, int)

    msgs = get_messages(db, cid)
    assert len(msgs) == 1
    assert msgs[0]["role"] == "user"
    assert msgs[0]["content"] == "Hello"


def test_message_ordering(db):
    cid = create_conversation(db)
    add_message(db, cid, "user", "first")
    add_message(db, cid, "assistant", "second")
    add_message(db, cid, "user", "third")

    msgs = get_messages(db, cid)
    assert [m["content"] for m in msgs] == ["first", "second", "third"]


def test_message_with_tool_calls(db):
    cid = create_conversation(db)
    tool_calls = [{"id": "tc_1", "function": {"name": "test", "arguments": "{}"}}]
    add_message(db, cid, "assistant", "Using tool", tool_calls=tool_calls)
    add_message(db, cid, "tool", "Tool result", tool_call_id="tc_1")

    msgs = get_messages(db, cid)
    assert len(msgs) == 2
    assert msgs[0]["tool_calls"] is not None
    assert msgs[1]["tool_call_id"] == "tc_1"


def test_skill_crud(db):
    save_skill(db, "echo", {"description": "Echo skill", "permissions": []})
    skill = get_skill(db, "echo")
    assert skill is not None
    assert skill["name"] == "echo"
    assert skill["status"] == "active"


def test_skill_upsert(db):
    save_skill(db, "echo", {"description": "v1"})
    first = get_skill(db, "echo")

    save_skill(db, "echo", {"description": "v2"})
    second = get_skill(db, "echo")

    assert first["updated_at"] != second["updated_at"]


def test_list_skills_filter(db):
    save_skill(db, "echo", {"description": "Echo"}, status="active")
    save_skill(db, "broken", {"description": "Broken"}, status="failed")

    all_skills = list_skills(db)
    assert len(all_skills) == 2

    active = list_skills(db, status="active")
    assert len(active) == 1
    assert active[0]["name"] == "echo"


def test_skill_not_found(db):
    assert get_skill(db, "nonexistent") is None


def test_schedule_crud(db):
    save_skill(db, "echo", {"description": "Echo"})
    sid = save_schedule(db, "echo", "cron", "*/5 * * * *", "2025-01-01T00:00:00+00:00")
    assert isinstance(sid, int)


def test_due_schedules_filters_correctly(db):
    save_skill(db, "echo", {"description": "Echo"})

    past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()

    save_schedule(db, "echo", "cron", "*/5 * * * *", past)
    save_schedule(db, "echo", "interval", "300000", future)

    due = get_due_schedules(db)
    assert len(due) == 1
    assert due[0]["schedule_value"] == "*/5 * * * *"


def test_foreign_key_enforcement(db):
    with pytest.raises(Exception):
        add_message(db, "nonexistent-conversation", "user", "Hello")
