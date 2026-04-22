"""APScheduler integration for recurring scheduled tasks."""

from __future__ import annotations

import logging
import uuid
from typing import Any

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

logger = logging.getLogger(__name__)

_scheduler: AsyncIOScheduler | None = None
_schedules: dict[str, dict[str, Any]] = {}


def get_scheduler() -> AsyncIOScheduler:
    global _scheduler
    if _scheduler is None:
        _scheduler = AsyncIOScheduler()
    return _scheduler


def start() -> None:
    get_scheduler().start()
    logger.info("Task scheduler started.")


def stop() -> None:
    global _scheduler
    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=False)
        logger.info("Task scheduler stopped.")


def all_schedules() -> list[dict[str, Any]]:
    sched = get_scheduler()
    result = []
    for rec in _schedules.values():
        job = sched.get_job(rec["id"])
        rec = dict(rec)
        rec["next_run"] = job.next_run_time.isoformat() if (job and job.next_run_time) else None
        result.append(rec)
    return result


def add_schedule(
    name: str,
    task: str,
    agents: list[str] | None = None,
    model: str | None = None,
    cron: str | None = None,
    interval_minutes: int | None = None,
) -> dict[str, Any]:
    """
    Create a recurring schedule that auto-submits a job.

    Provide either *cron* (5-field: "min hour day month weekday")
    or *interval_minutes*.
    """
    if not cron and not interval_minutes:
        raise ValueError("Either 'cron' or 'interval_minutes' must be provided.")

    schedule_id = str(uuid.uuid4())

    def _trigger() -> None:
        from job_queue import submit_job
        submitted = submit_job(task, agents=agents, model=model)
        logger.info("Schedule '%s' triggered job %s.", name, submitted.id)

    if cron:
        parts = cron.strip().split()
        if len(parts) != 5:
            raise ValueError("'cron' must be a 5-field string: minute hour day month weekday")
        trigger: Any = CronTrigger(
            minute=parts[0],
            hour=parts[1],
            day=parts[2],
            month=parts[3],
            day_of_week=parts[4],
        )
    else:
        trigger = IntervalTrigger(minutes=interval_minutes)

    sched = get_scheduler()
    job = sched.add_job(_trigger, trigger=trigger, id=schedule_id, name=name)

    rec: dict[str, Any] = {
        "id": schedule_id,
        "name": name,
        "task": task,
        "agents": agents or [],
        "model": model,
        "cron": cron,
        "interval_minutes": interval_minutes,
        "next_run": job.next_run_time.isoformat() if job.next_run_time else None,
    }
    _schedules[schedule_id] = rec
    logger.info("Created schedule '%s' (%s).", name, schedule_id)
    return rec


def remove_schedule(schedule_id: str) -> bool:
    if schedule_id not in _schedules:
        return False
    try:
        get_scheduler().remove_job(schedule_id)
    except Exception as exc:
        logger.debug("Could not remove APScheduler job %s: %s", schedule_id, exc)
    del _schedules[schedule_id]
    logger.info("Removed schedule %s.", schedule_id)
    return True
