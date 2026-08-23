"""User management - admin only.

Adding a user here is the only way an account comes into existence: there is no
self-registration anywhere in the application, and the login endpoint refuses
any address without an active record in this collection.

Creating a user mints a one-time password, returned exactly once so the admin
can hand it over. The user must replace it at first login.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from bson import ObjectId
from bson.errors import InvalidId
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pymongo.errors import DuplicateKeyError

from .. import audit, db, security
from ..config import settings
from ..services import mailer
from ..deps import CurrentUser, admin_user
from ..models import UserCreate, UserOut, UserUpdate

log = logging.getLogger("users")

router = APIRouter(prefix="/api/users", tags=["users"])


def _oid(value: str) -> ObjectId:
    try:
        return ObjectId(value)
    except (InvalidId, TypeError):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "מזהה לא תקין")


def _is_locked(record: dict[str, Any]) -> bool:
    locked_until = record.get("locked_until")
    if not locked_until:
        return False
    if locked_until.tzinfo is None:
        locked_until = locked_until.replace(tzinfo=timezone.utc)
    return locked_until > datetime.now(timezone.utc)


def _serialise(
    record: dict[str, Any],
    survey_count: int = 0,
    one_time_password: str | None = None,
) -> UserOut:
    return UserOut(
        id=str(record["_id"]),
        email=record["email"],
        name=record.get("name", ""),
        role=record.get("role", "user"),
        active=record.get("active", True),
        created_at=record.get("created_at"),
        last_login=record.get("last_login"),
        survey_count=survey_count,
        must_change_password=record.get("must_change_password", False),
        locked=_is_locked(record),
        one_time_password=one_time_password,
    )


@router.get("", response_model=list[UserOut])
async def list_users(admin: CurrentUser = Depends(admin_user)) -> list[UserOut]:
    records = await db.users().find(
        {}, {"password_hash": 0, "reset_token_hash": 0}
    ).sort("email", 1).to_list(500)

    counts_cursor = db.surveys().aggregate([
        {"$match": {"deleted": False}},
        {"$group": {"_id": "$owner_email", "n": {"$sum": 1}}},
    ])
    counts = {row["_id"]: row["n"] async for row in counts_cursor}

    return [_serialise(r, counts.get(r["email"], 0)) for r in records]


@router.post("", response_model=UserOut, status_code=status.HTTP_201_CREATED)
async def create_user(
    payload: UserCreate,
    request: Request,
    admin: CurrentUser = Depends(admin_user),
) -> UserOut:
    email = payload.email.strip().lower()

    # The configured owner address is always the admin, never a plain user
    role = "admin" if email == settings.admin_email else payload.role

    # The account is born with a one-time password. It is returned exactly once,
    # in this response, for the admin to hand over - it is never stored in clear
    # text and can never be read back.
    one_time = security.generate_one_time_password()

    document = {
        "email": email,
        "name": security.clean_text(payload.name, 100) or email.split("@")[0],
        "role": role,
        "active": payload.active,
        "password_hash": security.hash_password(one_time),
        "must_change_password": True,
        "created_at": datetime.now(timezone.utc),
        "created_by": admin.email,
        "failed_attempts": 0,
    }
    try:
        result = await db.users().insert_one(document)
    except DuplicateKeyError:
        raise HTTPException(status.HTTP_409_CONFLICT, "המשתמש כבר קיים במערכת")

    document["_id"] = result.inserted_id
    await audit.record("user.create", admin.email, request, target=email, details={"role": role})

    # Best effort: mail it too, so the admin does not have to relay it by hand.
    if mailer.is_configured():
        try:
            await mailer.send_mail(
                email,
                "פרטי גישה — מערכת סקרים עירוניים",
                f"שלום {document['name']},\n\n"
                "נפתח עבורך חשבון במערכת הסקרים העירוניים.\n\n"
                f"כתובת: {settings.public_origin}\n"
                f"שם משתמש: {email}\n"
                f"סיסמה חד-פעמית: {one_time}\n\n"
                "בכניסה הראשונה תתבקש לבחור סיסמה קבועה משלך.\n",
            )
            await audit.record("user.credentials_mailed", admin.email, request, target=email)
        except Exception:
            log.warning("could not mail credentials to %s", email, exc_info=True)

    return _serialise(document, one_time_password=one_time)


@router.patch("/{user_id}", response_model=UserOut)
async def update_user(
    user_id: str,
    payload: UserUpdate,
    request: Request,
    admin: CurrentUser = Depends(admin_user),
) -> UserOut:
    record = await db.users().find_one({"_id": _oid(user_id)})
    if not record:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "המשתמש לא נמצא")

    updates: dict[str, Any] = {}
    if payload.name is not None:
        updates["name"] = security.clean_text(payload.name, 100)
    if payload.role is not None:
        updates["role"] = payload.role
    if payload.active is not None:
        updates["active"] = payload.active

    # The owner address cannot be demoted or disabled - that would lock
    # everyone out of the admin panel permanently.
    if record["email"] == settings.admin_email:
        if updates.get("role") not in (None, "admin") or updates.get("active") is False:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                "לא ניתן לשנות את ההרשאות או להשבית את חשבון הבעלים",
            )
    if record["email"] == admin.email and updates.get("role") == "user":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "לא ניתן להסיר לעצמך הרשאות מנהל")

    if updates:
        await db.users().update_one({"_id": record["_id"]}, {"$set": updates})
        record.update(updates)
        # Losing access must take effect immediately, not at session expiry
        if updates.get("active") is False or updates.get("role") == "user":
            await security.revoke_all_sessions(record["email"])
        await audit.record("user.update", admin.email, request, target=record["email"],
                           details={"changes": list(updates.keys())})

    return _serialise(record)


@router.delete("/{user_id}")
async def delete_user(
    user_id: str,
    request: Request,
    admin: CurrentUser = Depends(admin_user),
) -> dict[str, Any]:
    record = await db.users().find_one({"_id": _oid(user_id)})
    if not record:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "המשתמש לא נמצא")
    if record["email"] == settings.admin_email:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "לא ניתן למחוק את חשבון הבעלים")
    if record["email"] == admin.email:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "לא ניתן למחוק את החשבון שלך")

    survey_count = await db.surveys().count_documents({"owner_email": record["email"], "deleted": False})

    await db.users().delete_one({"_id": record["_id"]})
    await security.revoke_all_sessions(record["email"])
    await audit.record("user.delete", admin.email, request, target=record["email"],
                       details={"orphaned_surveys": survey_count})

    # Surveys are business records - they stay, tagged with the original owner.
    return {"status": "ok", "kept_surveys": survey_count}


@router.post("/{user_id}/reset-password", response_model=UserOut)
async def reset_user_password(
    user_id: str,
    request: Request,
    admin: CurrentUser = Depends(admin_user),
) -> UserOut:
    """Issue a fresh one-time password for a user who is locked out or forgot.

    Every active session for that account is dropped immediately, so a stolen
    session cannot outlive the reset.
    """
    record = await db.users().find_one({"_id": _oid(user_id)})
    if not record:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "המשתמש לא נמצא")

    one_time = security.generate_one_time_password()
    await db.users().update_one(
        {"_id": record["_id"]},
        {"$set": {
            "password_hash": security.hash_password(one_time),
            "must_change_password": True,
            "failed_attempts": 0,
        },
         "$unset": {"locked_until": "", "reset_token_hash": "", "reset_expires_at": ""}},
    )
    await security.revoke_all_sessions(record["email"])
    await audit.record("user.password_reset", admin.email, request, target=record["email"])

    if mailer.is_configured():
        try:
            await mailer.send_mail(
                record["email"],
                "אופסה הסיסמה שלך — מערכת סקרים עירוניים",
                f"שלום {record.get('name') or ''},\n\n"
                "מנהל המערכת אתחל את הסיסמה שלך.\n\n"
                f"סיסמה חד-פעמית חדשה: {one_time}\n\n"
                "בכניסה הבאה תתבקש לבחור סיסמה קבועה משלך.\n",
            )
        except Exception:
            log.warning("could not mail new credentials to %s", record["email"], exc_info=True)

    record["must_change_password"] = True
    record.pop("locked_until", None)
    return _serialise(record, one_time_password=one_time)


@router.post("/{user_id}/unlock", response_model=UserOut)
async def unlock_user(
    user_id: str,
    request: Request,
    admin: CurrentUser = Depends(admin_user),
) -> UserOut:
    """Clear a lockout without changing the password."""
    record = await db.users().find_one({"_id": _oid(user_id)})
    if not record:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "המשתמש לא נמצא")
    await db.users().update_one(
        {"_id": record["_id"]},
        {"$set": {"failed_attempts": 0}, "$unset": {"locked_until": ""}},
    )
    await audit.record("user.unlock", admin.email, request, target=record["email"])
    record.pop("locked_until", None)
    return _serialise(record)
