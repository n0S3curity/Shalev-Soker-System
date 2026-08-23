"""Daily report composition - builds both workbooks and mails them."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from .. import db
from ..config import settings
from . import excel, mailer

log = logging.getLogger("reports")

DEFAULT_SETTINGS: dict[str, Any] = {
    "_id": "app",
    "daily_email_enabled": False,
    "daily_email_time": "08:00",
    "daily_email_recipient": settings.report_recipient,
    "daily_email_scope": "all",
    "last_sent_at": None,
    "last_send_status": "",
}


async def load_settings() -> dict[str, Any]:
    record = await db.app_settings().find_one({"_id": "app"})
    if not record:
        record = dict(DEFAULT_SETTINGS)
        await db.app_settings().insert_one(record)
    merged = dict(DEFAULT_SETTINGS)
    merged.update(record)
    return merged


async def save_settings(updates: dict[str, Any]) -> dict[str, Any]:
    await db.app_settings().update_one({"_id": "app"}, {"$set": updates}, upsert=True)
    return await load_settings()


def local_now() -> datetime:
    return datetime.now(ZoneInfo(settings.timezone))


async def build_report() -> tuple[list[tuple[str, bytes]], dict[str, Any]]:
    """Return (attachments, stats) for the current report settings."""
    config = await load_settings()
    since = None
    if config.get("daily_email_scope") == "last24h":
        since = datetime.now(timezone.utc) - timedelta(hours=24)

    records = await excel.fetch_records(since=since)
    stamp = local_now().strftime("%Y-%m-%d")

    full_bytes = await excel.build_full_workbook(records)
    calc_bytes = excel.build_calc_workbook(records)

    attachments = [
        (f"נתונים_מלאים_{stamp}.xlsx", full_bytes),
        (f"תחשיב_{stamp}.xlsx", calc_bytes),
    ]
    cities = sorted({r.get("city", "") for r in records if r.get("city")})
    stats = {
        "records": len(records),
        "cities": cities,
        "scope": config.get("daily_email_scope", "all"),
        "recipient": config.get("daily_email_recipient") or settings.report_recipient,
    }
    return attachments, stats


async def send_daily_report(trigger: str = "schedule") -> dict[str, Any]:
    config = await load_settings()
    recipient = config.get("daily_email_recipient") or settings.report_recipient

    attachments, stats = await build_report()
    stamp = local_now().strftime("%d/%m/%Y %H:%M")
    scope_label = "כל הנתונים במערכת" if stats["scope"] == "all" else "סקרים מהיממה האחרונה"

    body = (
        "שלום,\n\n"
        f"מצורף דוח הסקרים העירוניים ליום {stamp}.\n\n"
        f"היקף הדוח: {scope_label}\n"
        f"מספר סקרים: {stats['records']}\n"
        f"ערים בדוח: {', '.join(stats['cities']) or 'אין'}\n\n"
        "מצורפים שני קבצים:\n"
        "  1. קובץ נתונים מלא - כל שדות הסקר, כולל תמונות וחתימות\n"
        "  2. קובץ לתחשיב - פירוט כלי אצירה ונוסחאות חישוב\n\n"
        "הודעה זו נשלחה אוטומטית ממערכת הסקרים העירוניים.\n"
    )

    status_text = "ok"
    try:
        await mailer.send_mail(recipient, f"דוח סקרים עירוניים - {stamp}", body, attachments)
    except Exception as exc:
        status_text = f"error: {exc}"
        log.exception("daily report failed")
        await save_settings({
            "last_send_status": status_text[:300],
            "last_attempt_at": datetime.now(timezone.utc),
        })
        raise

    await save_settings({
        "last_sent_at": datetime.now(timezone.utc),
        "last_send_status": status_text,
        "last_attempt_at": datetime.now(timezone.utc),
    })
    log.info("daily report sent to %s (%s records, trigger=%s)", recipient, stats["records"], trigger)
    return {"status": "ok", "recipient": recipient, **stats}
