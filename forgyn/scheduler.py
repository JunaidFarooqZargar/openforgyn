"""Cron and interval scheduler for recurring skill execution."""

from __future__ import annotations

import asyncio
import logging
import sqlite3
from datetime import datetime, timedelta, timezone

from croniter import croniter

from forgyn.db import get_due_schedules, save_schedule, update_schedule

log = logging.getLogger(__name__)

POLL_INTERVAL = 30  # seconds


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def compute_next_run(schedule_type: str, schedule_value: str) -> str | None:
    """Compute the next run time based on schedule type and value."""
    now = datetime.now(timezone.utc)
    if schedule_type == "cron":
        cron = croniter(schedule_value, now)
        return cron.get_next(datetime).isoformat()
    elif schedule_type == "interval":
        ms = int(schedule_value)
        return (now + timedelta(milliseconds=ms)).isoformat()
    elif schedule_type == "once":
        return None  # No next run after a once schedule fires
    return None


class Scheduler:
    """Polls for due schedules and fires skill execution through a callback."""

    def __init__(
        self,
        db: sqlite3.Connection,
        execute_fn=None,
        push_fn=None,
    ):
        self.db = db
        self.execute_fn = execute_fn  # async fn(skill_name, args) -> str
        self.push_fn = push_fn  # async fn(text) -> None
        self._running = False
        self._task: asyncio.Task | None = None

    def schedule(
        self,
        skill_name: str,
        schedule_type: str,
        schedule_value: str,
        message: str | None = None,
    ) -> int:
        """Register a new schedule. Returns the schedule ID."""
        if schedule_type == "once":
            next_run = schedule_value  # The value IS the run time
        else:
            next_run = compute_next_run(schedule_type, schedule_value)
        return save_schedule(self.db, skill_name, schedule_type, schedule_value, next_run, message=message)

    def cancel(self, schedule_id: int) -> None:
        """Cancel a schedule by marking it paused."""
        update_schedule(self.db, schedule_id, status="paused")

    async def tick(self) -> int:
        """Single poll iteration: fire all due schedules. Returns count fired."""
        due = get_due_schedules(self.db)
        fired = 0
        for sched in due:
            text = sched.get("message")
            try:
                if not text and self.execute_fn:
                    text = await self.execute_fn(sched["skill_name"], {})
                if self.push_fn and text:
                    await self.push_fn(text)
                fired += 1
            except Exception as e:
                log.error("Schedule %s failed: %s", sched["id"], e)

            # Update next_run or mark completed
            stype = sched["schedule_type"]
            if stype == "once":
                update_schedule(self.db, sched["id"], status="completed", next_run=None)
            else:
                next_run = compute_next_run(stype, sched["schedule_value"])
                update_schedule(self.db, sched["id"], next_run=next_run)

        return fired

    async def start(self) -> None:
        """Begin the polling loop."""
        self._running = True
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        """Gracefully stop the polling loop."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _loop(self) -> None:
        while self._running:
            try:
                await self.tick()
            except Exception as e:
                log.error("Scheduler tick error: %s", e)
            await asyncio.sleep(POLL_INTERVAL)
