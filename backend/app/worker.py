"""Background worker: daily report scheduler and housekeeping.

Runs as its own container so the API can be scaled horizontally without the
scheduled job firing once per replica.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from . import db
from .config import settings
from .services import reports

logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
log = logging.getLogger("worker")

REPORT_JOB_ID = "daily-report"
ORPHAN_AGE_HOURS = 24


async def daily_report_job() -> None:
    config = await reports.load_settings()
    if not config.get("daily_email_enabled"):
        log.info("daily report is disabled, skipping")
        return
    try:
        await reports.send_daily_report(trigger="schedule")
    except Exception:
        log.exception("scheduled report failed")


async def cleanup_orphan_uploads() -> None:
    """Delete uploads that were never attached to a saved survey."""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=ORPHAN_AGE_HOURS)
    cursor = db.get_db()["uploads.files"].find({
        "metadata.survey_id": None,
        "uploadDate": {"$lt": cutoff},
    })
    bucket = db.gridfs()
    removed = 0
    async for record in cursor:
        try:
            await bucket.delete(record["_id"])
            removed += 1
        except Exception:
            log.warning("could not delete orphan %s", record.get("_id"))
    if removed:
        log.info("removed %d orphaned uploads", removed)


async def purge_expired_sessions() -> None:
    """Belt and braces alongside the TTL index."""
    await db.sessions().delete_many({"expires_at": {"$lt": datetime.now(timezone.utc)}})


async def sync_schedule(scheduler: AsyncIOScheduler) -> None:
    """Re-read the admin's chosen time and reschedule when it changes."""
    config = await reports.load_settings()
    hour, _, minute = (config.get("daily_email_time") or "08:00").partition(":")

    trigger = CronTrigger(
        hour=int(hour),
        minute=int(minute),
        timezone=ZoneInfo(settings.timezone),
    )
    job = scheduler.get_job(REPORT_JOB_ID)
    if job is None:
        scheduler.add_job(daily_report_job, trigger, id=REPORT_JOB_ID, replace_existing=True,
                          misfire_grace_time=3600, coalesce=True)
        log.info("daily report scheduled at %s:%s %s", hour, minute, settings.timezone)
    elif str(job.trigger) != str(trigger):
        scheduler.reschedule_job(REPORT_JOB_ID, trigger=trigger)
        log.info("daily report rescheduled to %s:%s %s", hour, minute, settings.timezone)


async def main() -> None:
    settings.validate_secrets()
    await db.init_indexes()

    scheduler = AsyncIOScheduler(timezone=ZoneInfo(settings.timezone))
    scheduler.add_job(cleanup_orphan_uploads, CronTrigger(hour=3, minute=15), id="cleanup-uploads")
    scheduler.add_job(purge_expired_sessions, CronTrigger(hour=3, minute=30), id="purge-sessions")
    scheduler.start()

    await sync_schedule(scheduler)
    log.info("worker started, timezone=%s", settings.timezone)

    # The admin can change the send time at any moment; pick it up promptly.
    while True:
        await asyncio.sleep(60)
        try:
            await sync_schedule(scheduler)
        except Exception:
            log.exception("failed to sync schedule")


if __name__ == "__main__":
    asyncio.run(main())
