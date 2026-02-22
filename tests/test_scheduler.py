"""Tests for scheduler (Step 3.1)."""

from datetime import datetime, timedelta, timezone

import pytest

from forgyn.db import init_db, save_skill
from forgyn.scheduler import Scheduler, compute_next_run


@pytest.fixture
def db(tmp_db_path):
    conn = init_db(tmp_db_path)
    # Create a dummy skill for schedule references
    save_skill(conn, "echo", {"name": "echo", "description": "test"})
    return conn


@pytest.fixture
def scheduler(db):
    return Scheduler(db=db)


# --- compute_next_run ---


def test_cron_next_run():
    result = compute_next_run("cron", "*/5 * * * *")
    assert result is not None
    next_dt = datetime.fromisoformat(result)
    assert next_dt > datetime.now(timezone.utc)


def test_interval_next_run():
    result = compute_next_run("interval", "60000")  # 60 seconds
    assert result is not None
    next_dt = datetime.fromisoformat(result)
    now = datetime.now(timezone.utc)
    diff = (next_dt - now).total_seconds()
    assert 55 < diff < 65  # Roughly 60 seconds from now


def test_once_next_run():
    result = compute_next_run("once", "2099-01-01T00:00:00+00:00")
    assert result is None  # once schedules have no "next" after firing


# --- Scheduler.schedule ---


def test_schedule_cron(scheduler):
    sid = scheduler.schedule("echo", "cron", "*/5 * * * *")
    assert sid > 0


def test_schedule_interval(scheduler):
    sid = scheduler.schedule("echo", "interval", "60000")
    assert sid > 0


def test_schedule_once(scheduler):
    future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    sid = scheduler.schedule("echo", "once", future)
    assert sid > 0


# --- Scheduler.tick ---


@pytest.mark.asyncio
async def test_tick_fires_due_task(db):
    fired_skills = []

    async def mock_execute(skill_name, args):
        fired_skills.append(skill_name)
        return f"Executed {skill_name}"

    sched = Scheduler(db=db, execute_fn=mock_execute)
    # Create a past-due schedule
    past = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    from forgyn.db import save_schedule
    save_schedule(db, "echo", "interval", "60000", past)

    count = await sched.tick()
    assert count == 1
    assert fired_skills == ["echo"]


@pytest.mark.asyncio
async def test_tick_skips_future_task(db):
    fired_skills = []

    async def mock_execute(skill_name, args):
        fired_skills.append(skill_name)

    sched = Scheduler(db=db, execute_fn=mock_execute)
    # Create a future schedule
    future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    from forgyn.db import save_schedule
    save_schedule(db, "echo", "interval", "60000", future)

    count = await sched.tick()
    assert count == 0
    assert fired_skills == []


@pytest.mark.asyncio
async def test_tick_skips_paused(db):
    fired_skills = []

    async def mock_execute(skill_name, args):
        fired_skills.append(skill_name)

    sched = Scheduler(db=db, execute_fn=mock_execute)
    past = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    from forgyn.db import save_schedule, update_schedule
    sid = save_schedule(db, "echo", "interval", "60000", past)
    update_schedule(db, sid, status="paused")

    count = await sched.tick()
    assert count == 0


@pytest.mark.asyncio
async def test_once_completes_after_fire(db):
    async def mock_execute(skill_name, args):
        pass

    sched = Scheduler(db=db, execute_fn=mock_execute)
    past = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    from forgyn.db import save_schedule
    sid = save_schedule(db, "echo", "once", past, past)

    await sched.tick()

    # Verify schedule is completed
    row = db.execute("SELECT status FROM schedules WHERE id = ?", (sid,)).fetchone()
    assert row["status"] == "completed"


@pytest.mark.asyncio
async def test_error_doesnt_crash(db):
    """A failing skill execution shouldn't crash the scheduler."""
    async def failing_execute(skill_name, args):
        raise RuntimeError("Boom!")

    sched = Scheduler(db=db, execute_fn=failing_execute)
    past = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    from forgyn.db import save_schedule
    save_schedule(db, "echo", "interval", "60000", past)

    # Should not raise
    count = await sched.tick()
    assert count == 0  # Error means not counted as fired


@pytest.mark.asyncio
async def test_cancel(db):
    sched = Scheduler(db=db)
    sid = sched.schedule("echo", "interval", "60000")

    sched.cancel(sid)
    row = db.execute("SELECT status FROM schedules WHERE id = ?", (sid,)).fetchone()
    assert row["status"] == "paused"


@pytest.mark.asyncio
async def test_scheduler_pushes_to_outbox(db):
    """When execute_fn returns text, push_fn should be called with that text."""
    pushed = []

    async def mock_execute(skill_name, args):
        return "Result from skill"

    async def mock_push(text):
        pushed.append(text)

    sched = Scheduler(db=db, execute_fn=mock_execute, push_fn=mock_push)
    past = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    from forgyn.db import save_schedule
    save_schedule(db, "echo", "interval", "60000", past)

    await sched.tick()
    assert pushed == ["Result from skill"]


@pytest.mark.asyncio
async def test_scheduler_pushes_message_without_execute(db):
    """A schedule with a message should push directly without calling execute_fn."""
    pushed = []

    async def mock_push(text):
        pushed.append(text)

    sched = Scheduler(db=db, push_fn=mock_push)
    past = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    from forgyn.db import save_schedule
    save_schedule(db, "echo", "once", past, past, message="Drink water")

    await sched.tick()
    assert pushed == ["Drink water"]


@pytest.mark.asyncio
async def test_schedule_with_message_param(db):
    sched = Scheduler(db=db)
    sid = sched.schedule("echo", "once", "2099-01-01T00:00:00+00:00", message="Test reminder")
    row = db.execute("SELECT message FROM schedules WHERE id = ?", (sid,)).fetchone()
    assert row["message"] == "Test reminder"
