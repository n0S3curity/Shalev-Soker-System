"""Email + password authentication.

Accounts are created by an admin only - there is no self-registration endpoint
anywhere in this application. A new account is issued a one-time password which
the admin hands to the user; the user must replace it before the account can do
anything, and a forgotten password is recovered by emailed reset link.

Login flow
    POST /api/auth/login
        -> {"status": "ok"}                       session cookie set
        -> {"status": "password_change_required", "change_token": "..."}
    POST /api/auth/first-login    (with change_token + chosen password)
        -> {"status": "ok"}                       session cookie set

Recovery flow
    POST /api/auth/forgot   {email}               always returns the same reply
    POST /api/auth/reset    {token, new_password}
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

import jwt
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse

from .. import audit, db, security
from ..config import settings
from ..deps import CurrentUser, current_user
from ..models import (
    ChangePasswordRequest,
    FirstLoginRequest,
    ForgotPasswordRequest,
    LoginRequest,
    ResetPasswordRequest,
)
from ..services import mailer

log = logging.getLogger("auth")

router = APIRouter(prefix="/api/auth", tags=["auth"])

# Deliberately identical for a wrong address and a wrong password so the login
# form cannot be used to discover which addresses exist (OWASP A07).
BAD_CREDENTIALS = "כתובת המייל או הסיסמה שגויים"
CHANGE_TOKEN_TTL_MINUTES = 15


# =========================================================================
#  Cookies
# =========================================================================
def _set_session_cookies(response: Response, token: str, csrf: str) -> None:
    max_age = settings.session_ttl_hours * 3600
    response.set_cookie(
        security.SESSION_COOKIE, token, max_age=max_age, httponly=True,
        secure=settings.cookie_secure, samesite="strict", path="/",
    )
    # Readable by JS on purpose: echoed back in the X-CSRF-Token header
    response.set_cookie(
        security.CSRF_COOKIE, csrf, max_age=max_age, httponly=False,
        secure=settings.cookie_secure, samesite="strict", path="/",
    )


def _clear_session_cookies(response: Response) -> None:
    for name in (security.SESSION_COOKIE, security.CSRF_COOKIE):
        response.delete_cookie(name, path="/", samesite="strict", secure=settings.cookie_secure)


# =========================================================================
#  Lockout helpers
# =========================================================================
def _is_locked(record: dict[str, Any]) -> bool:
    locked_until = record.get("locked_until")
    if not locked_until:
        return False
    if locked_until.tzinfo is None:
        locked_until = locked_until.replace(tzinfo=timezone.utc)
    return locked_until > datetime.now(timezone.utc)


async def _register_failure(record: dict[str, Any], request: Request) -> None:
    attempts = int(record.get("failed_attempts", 0)) + 1
    update: dict[str, Any] = {"failed_attempts": attempts}
    if attempts >= settings.max_login_attempts:
        update["locked_until"] = (
            datetime.now(timezone.utc) + timedelta(minutes=settings.lockout_minutes)
        )
    await db.users().update_one({"_id": record["_id"]}, {"$set": update})
    await audit.record("login.failed", record["email"], request, success=False,
                       details={"attempts": attempts})


# =========================================================================
#  Short-lived token for the forced first password change
# =========================================================================
def _issue_change_token(email: str) -> str:
    now = datetime.now(timezone.utc)
    return jwt.encode(
        {
            "sub": email,
            "purpose": "password_change",
            "iat": int(now.timestamp()),
            "exp": int((now + timedelta(minutes=CHANGE_TOKEN_TTL_MINUTES)).timestamp()),
            "iss": settings.public_origin,
        },
        settings.session_secret,
        algorithm="HS256",
    )


def _read_change_token(token: str) -> str:
    try:
        claims = jwt.decode(
            token, settings.session_secret, algorithms=["HS256"],
            issuer=settings.public_origin, options={"require": ["exp", "sub"]},
        )
    except jwt.PyJWTError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED,
                            "פג תוקף בקשת שינוי הסיסמה. התחבר שוב עם הסיסמה החד-פעמית.")
    if claims.get("purpose") != "password_change":
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "אסימון לא תקין")
    return claims["sub"]


# =========================================================================
#  Session issuing
# =========================================================================
async def _finish_login(record: dict[str, Any], request: Request) -> Response:
    name = record.get("name") or record["email"].split("@")[0]
    token, csrf = await security.issue_session(
        record["email"], record.get("role", "user"), name, request
    )
    await db.users().update_one(
        {"_id": record["_id"]},
        {"$set": {"last_login": datetime.now(timezone.utc), "failed_attempts": 0},
         "$unset": {"locked_until": ""}},
    )
    await audit.record("login.success", record["email"], request,
                       details={"role": record.get("role")})

    response = JSONResponse({
        "status": "ok",
        "user": {"email": record["email"], "name": name, "role": record.get("role", "user")},
    })
    _set_session_cookies(response, token, csrf)
    return response


# =========================================================================
#  Routes
# =========================================================================
@router.get("/config")
async def auth_config() -> dict[str, Any]:
    """Public bootstrap data for the login screen."""
    return {
        "app_name": settings.app_name,
        "password_min_length": settings.password_min_length,
        "password_reset_available": mailer.is_configured(),
    }


@router.post("/login")
async def login(payload: LoginRequest, request: Request) -> Any:
    await security.rate_limit(request, "login", settings.rate_login)
    await security.rate_limit(request, "login-id", settings.rate_login_account,
                              identity=payload.email)

    record = await db.users().find_one({"email": payload.email})

    # Unknown or disabled account: burn the same CPU, return the same message.
    if not record or not record.get("active", False) or not record.get("password_hash"):
        security.dummy_verify()
        await audit.record("login.denied", payload.email, request, success=False,
                           details={"reason": "unknown_or_inactive"})
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, BAD_CREDENTIALS)

    if _is_locked(record):
        await audit.record("login.locked", payload.email, request, success=False)
        raise HTTPException(
            status.HTTP_423_LOCKED,
            f"החשבון נעול זמנית עקב ניסיונות שגויים. נסה שוב בעוד {settings.lockout_minutes} דקות.",
        )

    if not security.verify_password(record["password_hash"], payload.password):
        await _register_failure(record, request)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, BAD_CREDENTIALS)

    # Correct password, but it is still the admin-issued one-time password.
    if record.get("must_change_password"):
        await db.users().update_one(
            {"_id": record["_id"]},
            {"$set": {"failed_attempts": 0}, "$unset": {"locked_until": ""}},
        )
        await audit.record("login.password_change_required", record["email"], request)
        return {
            "status": "password_change_required",
            "email": record["email"],
            "change_token": _issue_change_token(record["email"]),
        }

    return await _finish_login(record, request)


@router.post("/first-login")
async def first_login(payload: FirstLoginRequest, request: Request) -> Any:
    """Replace the one-time password with one the user chooses."""
    # Its own bucket: this call is already gated by the change token, and it
    # must not be starved by ordinary sign-in traffic from the same address.
    await security.rate_limit(request, "first-login", settings.rate_first_login)

    email = _read_change_token(payload.change_token)
    record = await db.users().find_one({"email": email})
    if not record or not record.get("active", False):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "החשבון אינו פעיל")

    if payload.new_password != payload.confirm_password:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "הסיסמאות אינן תואמות")

    problems = security.password_problems(payload.new_password, current_email=email)
    if problems:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, " · ".join(problems))

    # The new password must not be the one-time password again.
    if security.verify_password(record.get("password_hash", ""), payload.new_password):
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            "יש לבחור סיסמה חדשה, שונה מהסיסמה החד-פעמית")

    await db.users().update_one(
        {"_id": record["_id"]},
        {"$set": {
            "password_hash": security.hash_password(payload.new_password),
            "must_change_password": False,
            "password_set_at": datetime.now(timezone.utc),
            "failed_attempts": 0,
        },
         "$unset": {"locked_until": "", "reset_token_hash": "", "reset_expires_at": ""}},
    )
    await audit.record("password.first_set", email, request)

    record = await db.users().find_one({"_id": record["_id"]}) or record
    return await _finish_login(record, request)


@router.post("/forgot")
async def forgot_password(payload: ForgotPasswordRequest, request: Request) -> dict[str, Any]:
    """Send a reset link. The reply never reveals whether the address exists."""
    await security.rate_limit(request, "reset", settings.rate_reset)
    await security.rate_limit(request, "reset-id", settings.rate_reset, identity=payload.email)

    generic = {
        "status": "ok",
        "message": "אם הכתובת קיימת במערכת, נשלח אליה קישור לאיפוס הסיסמה.",
    }

    record = await db.users().find_one({"email": payload.email})
    if not record or not record.get("active", False):
        await audit.record("password.forgot_unknown", payload.email, request, success=False)
        return generic

    if not mailer.is_configured():
        await audit.record("password.forgot_no_smtp", payload.email, request, success=False)
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "שחזור סיסמה בדוא\"ל אינו מוגדר במערכת. פנה למנהל לקבלת סיסמה חדשה.",
        )

    token, token_hash = security.generate_reset_token()
    expires = datetime.now(timezone.utc) + timedelta(minutes=settings.reset_token_ttl_minutes)
    await db.users().update_one(
        {"_id": record["_id"]},
        {"$set": {"reset_token_hash": token_hash, "reset_expires_at": expires}},
    )

    link = f"{settings.public_origin.rstrip('/')}/?reset={token}"
    body = (
        f"שלום {record.get('name') or ''},\n\n"
        "התקבלה בקשה לאיפוס הסיסמה שלך במערכת הסקרים העירוניים.\n\n"
        f"לחץ על הקישור הבא כדי לבחור סיסמה חדשה:\n{link}\n\n"
        f"הקישור תקף ל-{settings.reset_token_ttl_minutes} דקות וניתן לשימוש פעם אחת בלבד.\n\n"
        "אם לא ביקשת לאפס את הסיסמה, אפשר להתעלם מההודעה — הסיסמה הנוכחית תישאר בתוקף.\n"
    )
    try:
        await mailer.send_mail(record["email"], "איפוס סיסמה — מערכת סקרים עירוניים", body)
    except Exception as exc:
        log.exception("reset mail failed")
        await audit.record("password.forgot_send_failed", payload.email, request,
                           success=False, details={"error": str(exc)[:200]})
        raise HTTPException(status.HTTP_502_BAD_GATEWAY,
                            "שליחת המייל נכשלה. פנה למנהל המערכת.")

    await audit.record("password.forgot_sent", record["email"], request)
    return generic


@router.post("/reset")
async def reset_password(payload: ResetPasswordRequest, request: Request) -> dict[str, str]:
    await security.rate_limit(request, "reset", settings.rate_reset)

    if payload.new_password != payload.confirm_password:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "הסיסמאות אינן תואמות")

    token_hash = security.hash_reset_token(payload.token)
    record = await db.users().find_one({"reset_token_hash": token_hash})
    if not record or not record.get("active", False):
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            "קישור האיפוס אינו תקף. בקש קישור חדש.")

    expires = record.get("reset_expires_at")
    if expires and expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    if not expires or expires < datetime.now(timezone.utc):
        await db.users().update_one(
            {"_id": record["_id"]},
            {"$unset": {"reset_token_hash": "", "reset_expires_at": ""}},
        )
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            "פג תוקף קישור האיפוס. בקש קישור חדש.")

    problems = security.password_problems(payload.new_password, current_email=record["email"])
    if problems:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, " · ".join(problems))

    await db.users().update_one(
        {"_id": record["_id"]},
        {"$set": {
            "password_hash": security.hash_password(payload.new_password),
            "must_change_password": False,
            "password_set_at": datetime.now(timezone.utc),
            "failed_attempts": 0,
        },
         # Token is single use, and every existing session is dropped in case
         # the reset was triggered by a compromise.
         "$unset": {"reset_token_hash": "", "reset_expires_at": "", "locked_until": ""}},
    )
    await security.revoke_all_sessions(record["email"])
    await audit.record("password.reset", record["email"], request)
    return {"status": "ok", "message": "הסיסמה עודכנה. אפשר להתחבר עם הסיסמה החדשה."}


@router.post("/password")
async def change_password(
    payload: ChangePasswordRequest,
    request: Request,
    user: CurrentUser = Depends(current_user),
) -> dict[str, str]:
    """Change your own password while signed in. Available to every role."""
    await security.rate_limit(request, "change-pw", settings.rate_login_account,
                              identity=user.email)

    record = await db.users().find_one({"email": user.email})
    stored = (record or {}).get("password_hash")
    if not stored or not security.verify_password(stored, payload.current_password):
        await audit.record("password.change_failed", user.email, request, success=False)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "הסיסמה הנוכחית שגויה")

    problems = security.password_problems(payload.new_password, current_email=user.email)
    if problems:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, " · ".join(problems))
    if security.verify_password(stored, payload.new_password):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "הסיסמה החדשה זהה לנוכחית")

    await db.users().update_one(
        {"email": user.email},
        {"$set": {
            "password_hash": security.hash_password(payload.new_password),
            "must_change_password": False,
            "password_set_at": datetime.now(timezone.utc),
        }},
    )
    await audit.record("password.changed", user.email, request)
    return {"status": "ok"}


@router.get("/me")
async def me(request: Request) -> dict[str, Any]:
    from ..deps import optional_user

    user = await optional_user(request)
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "נדרשת התחברות")
    return {
        "email": user.email,
        "name": user.name,
        "role": user.role,
        "admin_email": settings.admin_email,
    }


@router.post("/logout")
async def logout(request: Request) -> Response:
    from ..deps import optional_user

    user = await optional_user(request)
    if user:
        await security.revoke_session(user.jti)
        await audit.record("logout", user.email, request)

    response = JSONResponse({"status": "ok"})
    _clear_session_cookies(response)
    return response
