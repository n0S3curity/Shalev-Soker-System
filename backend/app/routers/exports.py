"""Excel exports and the daily-report settings screen (admin only)."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import Response

from .. import audit, db, security
from ..config import settings
from ..deps import CurrentUser, admin_user
from ..models import AppSettingsOut, AppSettingsUpdate
from ..services import excel, mailer, reports

router = APIRouter(prefix="/api", tags=["exports"])

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
    total = await db.surveys().count_documents({"deleted": False})
    users_count = await db.users().count_documents({"active": True})
    cities_count = await db.cities().count_documents({"active": True})

    by_city_cursor = db.surveys().aggregate([
        {"$match": {"deleted": False}},
        {"$group": {"_id": "$city", "n": {"$sum": 1}}},
        {"$sort": {"n": -1}},
    ])
    by_user_cursor = db.surveys().aggregate([
        {"$match": {"deleted": False}},
        {"$group": {"_id": "$owner_name", "n": {"$sum": 1}}},
        {"$sort": {"n": -1}},
        {"$limit": 20},
    ])

    return {
        "total_surveys": total,
        "active_users": users_count,
        "active_cities": cities_count,
        "by_city": [{"city": r["_id"] or "—", "count": r["n"]} async for r in by_city_cursor],
        "by_user": [{"user": r["_id"] or "—", "count": r["n"]} async for r in by_user_cursor],
    }
