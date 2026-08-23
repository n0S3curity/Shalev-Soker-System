"""Request dependencies: session resolution, role gates and CSRF enforcement.

Access control is deny-by-default: a route without an explicit dependency has
no access to any user context, and every authenticated dependency re-reads the
user record so that a deactivated or deleted account loses access immediately
(OWASP A01 - Broken Access Control).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from fastapi import Depends, HTTPException, Request, status

from . import db, security
from .config import settings


@dataclass(frozen=True)
class CurrentUser:
    email: str
    name: str
    role: str
    jti: str
    csrf: str

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"


async def optional_user(request: Request) -> Optional[CurrentUser]:
    claims = await security.read_session(request)
    if not claims:
        return None
    record = await db.users().find_one({"email": claims["sub"]})
    if not record or not record.get("active", False):
        # Account disabled or removed while the session was alive
        await security.revoke_session(claims["jti"])
        return None
    return CurrentUser(
        email=record["email"],
        name=record.get("name") or claims.get("name", ""),
        role=record.get("role", "user"),
        jti=claims["jti"],
        csrf=claims.get("csrf", ""),
    )


async def current_user(request: Request) -> CurrentUser:
    user = await optional_user(request)
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "נדרשת התחברות")
    security.verify_csrf(request, {"csrf": user.csrf})
    return user


async def admin_user(user: CurrentUser = Depends(current_user)) -> CurrentUser:
    if not user.is_admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "נדרשות הרשאות מנהל")
    return user


def can_edit_survey(user: CurrentUser, survey: dict) -> bool:
    """Admins edit everything, surveyors edit only what they created."""
    if user.is_admin:
        return True
    return survey.get("owner_email") == user.email


def require_edit_permission(user: CurrentUser, survey: dict) -> None:
    if not can_edit_survey(user, survey):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "אין הרשאה לערוך סקר שמולא על ידי משתמש אחר",
        )


async def api_rate_limit(request: Request) -> None:
    await security.rate_limit(request, "api", settings.rate_api)
