"""Excel exports and the daily-report settings screen (admin only)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from zoneinfo import ZoneInfo
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import Response

from .. import audit, db, security
from ..config import settings
from ..deps import CurrentUser, admin_user
from ..models import AppSettingsOut, AppSettingsUpdate
from ..services import excel, mailer, reports

router = APIRouter(prefix="/api", tags=["exports"])

NONE_LABEL = "—"
REMOVED_LABEL = "הוסר"

XLSX_MEDIA = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _xlsx_response(payload: bytes, filename: str) -> Response:
    return Response(
        content=payload,
        media_type=XLSX_MEDIA,
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}",
            "Content-Length": str(len(payload)),
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "no-store",
        },
    )


async def _records_for(city: Optional[str]) -> list[dict[str, Any]]:
    clean_city = security.safe_query_value(city) if city else None
    records = await excel.fetch_records(city=clean_city)
    if not records:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "אין נתונים לייצוא")
    return records


@router.get("/export/full")
async def export_full(
    request: Request,
    city: Optional[str] = Query(default=None, max_length=60),
    images: bool = Query(default=True),
    admin: CurrentUser = Depends(admin_user),
) -> Response:
    await security.rate_limit(request, "export", settings.rate_export, identity=admin.email)
    records = await _records_for(city)
    payload = await excel.build_full_workbook(records, embed_images=images)

    stamp = reports.local_now().strftime("%Y-%m-%d")
    suffix = f"_{city}" if city else ""
    await audit.record("export.full", admin.email, request,
                       details={"city": city or "all", "records": len(records)})
    return _xlsx_response(payload, f"נתונים_מלאים{suffix}_{stamp}.xlsx")


@router.get("/export/calc")
async def export_calc(
    request: Request,
    city: Optional[str] = Query(default=None, max_length=60),
    admin: CurrentUser = Depends(admin_user),
) -> Response:
    await security.rate_limit(request, "export", settings.rate_export, identity=admin.email)
    records = await _records_for(city)
    payload = excel.build_calc_workbook(records)

    stamp = reports.local_now().strftime("%Y-%m-%d")
    suffix = f"_{city}" if city else ""
    await audit.record("export.calc", admin.email, request,
                       details={"city": city or "all", "records": len(records)})
    return _xlsx_response(payload, f"תחשיב{suffix}_{stamp}.xlsx")


# =========================================================================
#  Daily report settings
# =========================================================================
@router.get("/settings", response_model=AppSettingsOut)
async def get_settings_(admin: CurrentUser = Depends(admin_user)) -> AppSettingsOut:
    config = await reports.load_settings()
    return AppSettingsOut(
        daily_email_enabled=config.get("daily_email_enabled", False),
        daily_email_time=config.get("daily_email_time", "08:00"),
        daily_email_recipient=config.get("daily_email_recipient") or settings.report_recipient,
        daily_email_scope=config.get("daily_email_scope", "all"),
        timezone=settings.timezone,
        last_sent_at=config.get("last_sent_at"),
        last_send_status=config.get("last_send_status", ""),
        smtp_configured=mailer.is_configured(),
    )


@router.patch("/settings", response_model=AppSettingsOut)
async def update_settings(
    payload: AppSettingsUpdate,
    request: Request,
    admin: CurrentUser = Depends(admin_user),
) -> AppSettingsOut:
    updates: dict[str, Any] = {}
    if payload.daily_email_enabled is not None:
        updates["daily_email_enabled"] = payload.daily_email_enabled
    if payload.daily_email_time is not None:
        updates["daily_email_time"] = payload.daily_email_time
    if payload.daily_email_recipient is not None:
        updates["daily_email_recipient"] = str(payload.daily_email_recipient).lower()
    if payload.daily_email_scope is not None:
        updates["daily_email_scope"] = payload.daily_email_scope

    if updates.get("daily_email_enabled") and not mailer.is_configured():
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "לא ניתן להפעיל שליחה יומית לפני הגדרת SMTP בקובץ ה-env",
        )

    if updates:
        await reports.save_settings(updates)
        await audit.record("settings.update", admin.email, request, details=updates)

    return await get_settings_(admin)


@router.post("/settings/send-now")
async def send_now(request: Request, admin: CurrentUser = Depends(admin_user)) -> dict[str, Any]:
    await security.rate_limit(request, "export", settings.rate_export, identity=admin.email)
    if not mailer.is_configured():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "SMTP אינו מוגדר")
    try:
        result = await reports.send_daily_report(trigger="manual")
    except Exception as exc:
        await audit.record("report.send_failed", admin.email, request, success=False,
                           details={"error": str(exc)[:200]})
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"שליחת הדוח נכשלה: {exc}")
    await audit.record("report.send_manual", admin.email, request, details=result)
    return result


# =========================================================================
#  Audit log viewer
# =========================================================================
@router.get("/audit")
async def list_audit(
    admin: CurrentUser = Depends(admin_user),
    limit: int = Query(default=100, ge=1, le=500),
    action: Optional[str] = Query(default=None, max_length=60),
) -> dict[str, Any]:
    query: dict[str, Any] = {}
    if action:
        query["action"] = security.safe_query_value(action)
    records = await db.audit().find(query).sort("ts", -1).to_list(limit)
    return {
        "items": [
            {
                "ts": r.get("ts"),
                "action": r.get("action", ""),
                "actor": r.get("actor", ""),
                "target": r.get("target", ""),
                "success": r.get("success", True),
                "ip": r.get("ip", ""),
                "details": r.get("details", {}),
            }
            for r in records
        ]
    }


@router.get("/stats")
async def dashboard_stats(admin: CurrentUser = Depends(admin_user)) -> dict[str, Any]:
    """Everything the admin dashboard draws, in one round trip.

    Day buckets are computed in the configured timezone rather than in UTC, so
    "today" on the dashboard means the same day it means to the surveyor.
    """
    tz = settings.timezone
    live = {"deleted": False}
    now = datetime.now(timezone.utc)
    day_start = now - timedelta(days=1)
    week_start = now - timedelta(days=7)
    month_start = now - timedelta(days=30)

    total = await db.surveys().count_documents(live)
    users_count = await db.users().count_documents({"active": True})
    cities_count = await db.cities().count_documents({"active": True})
    today_count = await db.surveys().count_documents({**live, "created_at": {"$gte": day_start}})
    week_count = await db.surveys().count_documents({**live, "created_at": {"$gte": week_start}})
    month_count = await db.surveys().count_documents({**live, "created_at": {"$gte": month_start}})
    signed_count = await db.surveys().count_documents({**live, "signature_id": {"$ne": None}})

    async def grouped(field: str, limit: int = 0) -> list[dict[str, Any]]:
        stages: list[dict[str, Any]] = [
            {"$match": live},
            {"$group": {"_id": f"${field}", "n": {"$sum": 1}}},
            {"$sort": {"n": -1}},
        ]
        if limit:
            stages.append({"$limit": limit})
        return [
            {"label": r["_id"] or NONE_LABEL, "count": r["n"]}
            async for r in db.surveys().aggregate(stages)
        ]

    # ── attachment and container totals in one pass ────────────────────────
    totals = await db.surveys().aggregate([
        {"$match": live},
        {"$group": {
            "_id": None,
            "images": {"$sum": {"$size": {"$ifNull": ["$image_ids", []]}}},
            "docs": {"$sum": {"$size": {"$ifNull": ["$doc_ids", []]}}},
            "container_rows": {"$sum": {"$size": {"$ifNull": ["$containers", []]}}},
            "container_qty": {"$sum": {
                "$reduce": {
                    "input": {"$ifNull": ["$containers", []]},
                    "initialValue": 0,
                    "in": {"$add": ["$$value", {"$ifNull": ["$$this.qty", 0]}]},
                },
            }},
            "with_images": {"$sum": {"$cond": [
                {"$gt": [{"$size": {"$ifNull": ["$image_ids", []]}}, 0]}, 1, 0]}},
        }},
    ]).to_list(length=1)
    agg = totals[0] if totals else {}

    # ── surveys per day for the last 30 days, zero filled so the line is even ─
    per_day = db.surveys().aggregate([
        {"$match": {**live, "created_at": {"$gte": month_start}}},
        {"$group": {
            "_id": {"$dateToString": {"format": "%Y-%m-%d", "date": "$created_at", "timezone": tz}},
            "n": {"$sum": 1},
        }},
    ])
    counted = {r["_id"]: r["n"] async for r in per_day}
    today_local = now.astimezone(ZoneInfo(tz)).date()
    by_day = []
    for offset in range(29, -1, -1):
        key = (today_local - timedelta(days=offset)).isoformat()
        by_day.append({"day": key, "count": counted.get(key, 0)})

    # ── container mix: surveys using each type, and total units deployed ───
    by_container = [
        {"label": r["_id"] or NONE_LABEL, "count": r["surveys"], "qty": r["qty"]}
        async for r in db.surveys().aggregate([
            {"$match": live},
            {"$unwind": "$containers"},
            {"$group": {
                "_id": "$containers.ctype",
                "surveys": {"$addToSet": "$_id"},
                "qty": {"$sum": {"$ifNull": ["$containers.qty", 0]}},
            }},
            {"$project": {"surveys": {"$size": "$surveys"}, "qty": 1}},
            {"$sort": {"qty": -1}},
        ])
    ]

    # ── per surveyor detail, merged onto the user list in Python ───────────
    by_owner = {
        r["_id"]: r
        async for r in db.surveys().aggregate([
            {"$match": live},
            {"$group": {
                "_id": "$owner_email",
                "total": {"$sum": 1},
                "d7": {"$sum": {"$cond": [{"$gte": ["$created_at", week_start]}, 1, 0]}},
                "d30": {"$sum": {"$cond": [{"$gte": ["$created_at", month_start]}, 1, 0]}},
                "signed": {"$sum": {"$cond": [{"$ne": ["$signature_id", None]}, 1, 0]}},
                "images": {"$sum": {"$size": {"$ifNull": ["$image_ids", []]}}},
                "cities": {"$addToSet": "$city"},
                "last_survey_at": {"$max": "$created_at"},
            }},
        ])
    }

    def person(email: str, name: str, role: str, active: bool, last_login, stat) -> dict[str, Any]:
        return {
            "email": email,
            "name": name,
            "role": role,
            "active": active,
            "last_login": last_login,
            "total": stat.get("total", 0),
            "d7": stat.get("d7", 0),
            "d30": stat.get("d30", 0),
            "signed": stat.get("signed", 0),
            "images": stat.get("images", 0),
            "cities": sorted(c for c in stat.get("cities", []) if c),
            "last_survey_at": stat.get("last_survey_at"),
        }

    people = []
    async for record in db.users().find({}, {"password": 0}):
        email = record.get("email", "")
        people.append(person(
            email,
            record.get("name") or email,
            record.get("role", "user"),
            bool(record.get("active", True)),
            record.get("last_login"),
            by_owner.pop(email, {}),
        ))
    # surveys whose author has since been deleted still deserve a row
    for email, stat in by_owner.items():
        label = email or NONE_LABEL
        people.append(person(label, f"{label} ({REMOVED_LABEL})", "user", False, None, stat))
    people.sort(key=lambda p: (-p["total"], p["name"]))

    by_user = [
        {"user": r["_id"] or NONE_LABEL, "count": r["n"]}
        async for r in db.surveys().aggregate([
            {"$match": live},
            {"$group": {"_id": "$owner_name", "n": {"$sum": 1}}},
            {"$sort": {"n": -1}},
            {"$limit": 20},
        ])
    ]

    return {
        "total_surveys": total,
        "active_users": users_count,
        "active_cities": cities_count,
        "surveys_today": today_count,
        "surveys_7d": week_count,
        "surveys_30d": month_count,
        "signed_surveys": signed_count,
        "surveys_with_images": agg.get("with_images", 0),
        "total_images": agg.get("images", 0),
        "total_docs": agg.get("docs", 0),
        "container_rows": agg.get("container_rows", 0),
        "container_units": agg.get("container_qty", 0),
        "by_city": [{"city": r["label"], "count": r["count"]} for r in await grouped("city")],
        "by_user": by_user,
        "by_day": by_day,
        "by_sector": await grouped("sector"),
        "by_biz_type": await grouped("biz_type_std", limit=10),
        "by_kitchen": await grouped("kitchen"),
        "by_yard": await grouped("yard"),
        "by_wet": await grouped("wet"),
        "by_cardboard": await grouped("cardboard"),
        "by_decl_given": await grouped("decl_given"),
        "by_decl_returned": await grouped("decl_ret"),
        "by_container": by_container,
        "users": people,
    }
