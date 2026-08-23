"""Authentication, session handling, hashing, rate limiting and input hygiene."""
from __future__ import annotations

import hmac
import ipaddress
import logging
import re
import secrets
import time
import unicodedata
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from fastapi import HTTPException, Request, status
from . import db
from .config import settings

log = logging.getLogger("security")

SESSION_COOKIE = "sv_session"
CSRF_COOKIE = "sv_csrf"
CSRF_HEADER = "x-csrf-token"

_ph = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=2)

# Verified against on a missing account so that a wrong address and a wrong
# password take the same time - otherwise login timing leaks which emails exist.
_DUMMY_HASH = _ph.hash("timing-equalisation-placeholder")


# =========================================================================
#  Passwords
# =========================================================================
def hash_password(raw: str) -> str:
    return _ph.hash(raw)


def verify_password(stored_hash: str, raw: str) -> bool:
    try:
        _ph.verify(stored_hash, raw)
        return True
    except (VerifyMismatchError, InvalidHashError):
        return False
    except Exception:  # malformed hash, never leak details
        return False


# Passwords that show up in every breach list. Length alone does not save these.
_COMMON_PASSWORDS = {
    "password", "password1", "password123", "123456789", "1234567890",
    "qwertyuiop", "qwerty123", "iloveyou1", "admin12345", "administrator",
    "letmein123", "welcome123", "abc123456", "passw0rd1", "12345678910",
}


def password_problems(raw: str, *, current_email: str = "") -> list[str]:
    """Password policy applied to every account.

    Deliberately not a symbol-and-uppercase maze: these are field workers typing
    on phones. Length plus a letter and a digit, with the obvious guesses and the
    user's own address ruled out, buys more than complexity theatre does.
    """
    minimum = settings.password_min_length
    issues: list[str] = []
    if len(raw) < minimum:
        issues.append(f"הסיסמה חייבת להכיל לפחות {minimum} תווים")
    if len(raw) > 200:
        issues.append("הסיסמה ארוכה מדי")
    if not re.search(r"[A-Za-z]", raw):
        issues.append("הסיסמה חייבת להכיל אות באנגלית")
    if not re.search(r"\d", raw):
        issues.append("הסיסמה חייבת להכיל ספרה")
    if raw.lower() in _COMMON_PASSWORDS:
        issues.append("הסיסמה נפוצה מדי, בחר סיסמה אחרת")
    local_part = (current_email or "").split("@")[0].lower()
    if local_part and len(local_part) >= 4 and local_part in raw.lower():
        issues.append("הסיסמה לא יכולה להכיל את כתובת המייל שלך")
    return issues


# =========================================================================
#  One-time passwords and reset tokens
# =========================================================================
# Ambiguous characters (0/O, 1/l/I) are left out so a password read aloud or
# copied off a screen cannot be mistyped.
_OTP_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789"


def generate_one_time_password(length: int = 12) -> str:
    return "".join(secrets.choice(_OTP_ALPHABET) for _ in range(length))


def generate_reset_token() -> tuple[str, str]:
    """Return (token_for_the_user, hash_to_store)."""
    token = secrets.token_urlsafe(32)
    return token, hash_reset_token(token)


def hash_reset_token(token: str) -> str:
    """Reset tokens are high-entropy already, so a fast digest is appropriate."""
    import hashlib
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def constant_time_equal(left: str, right: str) -> bool:
    return hmac.compare_digest(left or "", right or "")


def dummy_verify() -> None:
    """Burn the same CPU as a real verification for an unknown address."""
    try:
        _ph.verify(_DUMMY_HASH, "not-the-password")
    except Exception:
        pass


# =========================================================================
#  Sessions - signed JWT in an HttpOnly cookie, revocable via Mongo
# =========================================================================
async def issue_session(email: str, role: str, name: str, request: Request) -> tuple[str, str]:
    """Return (session_jwt, csrf_token) and persist the session id."""
    now = datetime.now(timezone.utc)
    expires = now + timedelta(hours=settings.session_ttl_hours)
    jti = secrets.token_urlsafe(24)
    csrf = secrets.token_urlsafe(24)

    payload = {
        "sub": email,
        "role": role,
        "name": name,
        "jti": jti,
        "csrf": csrf,
        "iat": int(now.timestamp()),
        "exp": int(expires.timestamp()),
        "iss": settings.public_origin,
    }
    token = jwt.encode(payload, settings.session_secret, algorithm="HS256")

    await db.sessions().insert_one({
        "jti": jti,
        "email": email,
        "role": role,
        "ip": client_ip(request),
        "user_agent": (request.headers.get("user-agent") or "")[:300],
        "created_at": now,
        "expires_at": expires,
    })
    return token, csrf


async def revoke_session(jti: str) -> None:
    await db.sessions().delete_one({"jti": jti})


async def revoke_all_sessions(email: str) -> None:
    await db.sessions().delete_many({"email": email})


async def read_session(request: Request) -> Optional[dict[str, Any]]:
    raw = request.cookies.get(SESSION_COOKIE)
    if not raw:
        return None
    try:
        claims = jwt.decode(
            raw,
            settings.session_secret,
            algorithms=["HS256"],
            issuer=settings.public_origin,
            options={"require": ["exp", "sub", "jti"]},
        )
    except jwt.PyJWTError:
        return None

    # Session must still exist server side, which makes revocation possible
    live = await db.sessions().find_one({"jti": claims["jti"]})
    if not live:
        return None
    return claims


def verify_csrf(request: Request, claims: dict[str, Any]) -> None:
    """Double submit CSRF check for every state changing request."""
    if request.method in ("GET", "HEAD", "OPTIONS"):
        return
    sent = request.headers.get(CSRF_HEADER, "")
    if not sent or not hmac.compare_digest(sent, claims.get("csrf", "")):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "בקשה נדחתה (CSRF)")


# =========================================================================
#  Client IP - trust the Cloudflare header only, never a raw XFF chain
# =========================================================================
def _is_ip(value: str) -> bool:
    try:
        ipaddress.ip_address(value.strip())
        return True
    except ValueError:
        return False


def client_ip(request: Request) -> str:
    if settings.trust_cf_headers:
        cf = request.headers.get("cf-connecting-ip")
        if cf and _is_ip(cf):
            return cf
    return request.client.host if request.client else "unknown"


# =========================================================================
#  Rate limiting - Mongo backed so it holds across workers and restarts
# =========================================================================
def _parse_rule(rule: str) -> tuple[int, int]:
    limit, _, window = rule.partition("/")
    return int(limit), int(window)


async def rate_limit(request: Request, bucket: str, rule: str, identity: str | None = None) -> None:
    limit, window = _parse_rule(rule)
    ident = identity or client_ip(request)
    slot = int(time.time() // window)
    key = f"{bucket}:{ident}:{slot}"
    expires = datetime.now(timezone.utc) + timedelta(seconds=window + 5)

    doc = await db.rate_limits().find_one_and_update(
        {"key": key},
        {"$inc": {"count": 1}, "$setOnInsert": {"expires_at": expires}},
        upsert=True,
        return_document=True,
    )
    if doc and doc.get("count", 0) > limit:
        retry = window - int(time.time() % window)
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "יותר מדי בקשות, נסה שוב בעוד רגע",
            headers={"Retry-After": str(retry)},
        )


# =========================================================================
#  Input hygiene (OWASP A03 - injection)
# =========================================================================
_CONTROL_CHARS = dict.fromkeys(range(0, 32))
for _keep in (9, 10, 13):
    _CONTROL_CHARS.pop(_keep, None)

_MONGO_UNSAFE = re.compile(r"[$]")


def clean_text(value: Any, max_len: int = 500) -> str:
    """Normalise and strip control characters from any user supplied string."""
    if value is None:
        return ""
    text = str(value)
    text = unicodedata.normalize("NFC", text)
    text = text.translate(_CONTROL_CHARS)
    return text.strip()[:max_len]


def safe_query_value(value: str) -> str:
    """A value that can never be interpreted as a Mongo operator."""
    return _MONGO_UNSAFE.sub("", clean_text(value, 200))


def escape_regex(value: str) -> str:
    return re.escape(safe_query_value(value))


def reject_operator_keys(payload: Any, depth: int = 0) -> None:
    """Refuse documents containing Mongo operator keys (NoSQL injection)."""
    if depth > 12:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "מבנה נתונים עמוק מדי")
    if isinstance(payload, dict):
        for key, val in payload.items():
            if isinstance(key, str) and (key.startswith("$") or "." in key):
                raise HTTPException(status.HTTP_400_BAD_REQUEST, "שדה לא חוקי בבקשה")
            reject_operator_keys(val, depth + 1)
    elif isinstance(payload, list):
        for item in payload:
            reject_operator_keys(item, depth + 1)
