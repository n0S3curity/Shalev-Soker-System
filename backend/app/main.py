"""FastAPI application entry point."""
from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
from pymongo.errors import DuplicateKeyError
from starlette.middleware.trustedhost import TrustedHostMiddleware

from . import db
from .config import settings
from .routers import auth, cities, exports, files, surveys, users

logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
log = logging.getLogger("app")

FRONTEND_DIR = os.environ.get("FRONTEND_DIR", "/app/frontend")

DEFAULT_CITIES = ["רחובות", "גדרה"]


# =========================================================================
#  Bootstrap
# =========================================================================
async def bootstrap() -> None:
    """Ensure the owner account and a starting city list exist."""
    now = datetime.now(timezone.utc)

    from . import security

    # Every step below is an idempotent upsert on purpose: uvicorn runs several
    # workers and they all execute this at once, so a read-then-insert would
    # race and kill whichever worker lost.
    existing = await db.users().find_one({"email": settings.admin_email})
    if existing:
        if existing.get("role") != "admin" or not existing.get("active"):
            await db.users().update_one(
                {"email": settings.admin_email},
                {"$set": {"role": "admin", "active": True}},
            )
    else:
        # The owner needs a way in on a brand new deployment. Either the operator
        # supplies ADMIN_INITIAL_PASSWORD, or one is generated and printed once
        # to the container log. Either way it must be replaced at first login.
        initial = settings.admin_initial_password or security.generate_one_time_password(14)
        supplied = bool(settings.admin_initial_password)

        try:
            result = await db.users().update_one(
                {"email": settings.admin_email},
                {"$setOnInsert": {
                    "email": settings.admin_email,
                    "name": "מנהל המערכת",
                    "role": "admin",
                    "active": True,
                    "password_hash": security.hash_password(initial),
                    "must_change_password": True,
                    "created_at": now,
                    "created_by": "system",
                    "failed_attempts": 0,
                }},
                upsert=True,
            )
            created = result.upserted_id is not None
        except DuplicateKeyError:
            # Another worker won the race; its password is the live one.
            created = False

        if created:
            if supplied:
                log.info("owner account provisioned: %s (password from ADMIN_INITIAL_PASSWORD)",
                         settings.admin_email)
            else:
                banner = "=" * 68
                log.warning(
                    "\n%s\n  OWNER ACCOUNT CREATED\n  email    : %s\n"
                    "  password : %s\n"
                    "  This one-time password is shown only now and must be changed\n"
                    "  at first sign-in. Copy it before clearing the logs.\n%s",
                    banner, settings.admin_email, initial, banner,
                )

    for name in DEFAULT_CITIES:
        try:
            await db.cities().update_one(
                {"name": name},
                {"$setOnInsert": {"name": name, "active": True,
                                  "created_at": now, "created_by": "system"}},
                upsert=True,
            )
        except DuplicateKeyError:
            pass

    from .services.reports import DEFAULT_SETTINGS

    await db.app_settings().update_one(
        {"_id": "app"}, {"$setOnInsert": dict(DEFAULT_SETTINGS)}, upsert=True
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings.validate_secrets()
    await db.init_indexes()
    await bootstrap()
    log.info("application ready (env=%s, origin=%s)", settings.environment, settings.public_origin)
    yield
    await db.close()


app = FastAPI(
    title=settings.app_name,
    version="1.0.0",
    lifespan=lifespan,
    # The interactive docs are an information-disclosure surface in production
    docs_url="/api/docs" if settings.environment != "production" else None,
    redoc_url=None,
    openapi_url="/api/openapi.json" if settings.environment != "production" else None,
)


# =========================================================================
#  Middleware
# =========================================================================
if settings.environment == "production":
    from urllib.parse import urlparse

    host = urlparse(settings.public_origin).hostname or "localhost"
    app.add_middleware(
        TrustedHostMiddleware,
        allowed_hosts=[host, "localhost", "127.0.0.1", "api"],
    )


@app.middleware("http")
async def limit_body_size(request: Request, call_next):
    """Reject oversized bodies before reading them (OWASP A04 - resource abuse)."""
    declared = request.headers.get("content-length")
    if declared and declared.isdigit():
        ceiling = settings.max_upload_bytes + (2 * 1024 * 1024)
        if int(declared) > ceiling:
            return JSONResponse(
                {"detail": f"הבקשה גדולה מדי (מקסימום {settings.max_upload_mb} MB)"},
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            )
    return await call_next(request)


CSP = "; ".join([
    "default-src 'self'",
    "base-uri 'self'",
    "object-src 'none'",
    "frame-ancestors 'none'",
    "form-action 'self'",
    "img-src 'self' data: blob:",
    "media-src 'self' blob:",
    # No third-party script origins at all now that sign-in is local
    "script-src 'self'",
    "style-src 'self' 'unsafe-inline'",
    "connect-src 'self'",
    "frame-src 'none'",
    "font-src 'self'",
    "worker-src 'self' blob:",
    "upgrade-insecure-requests" if settings.cookie_secure else "",
]).strip("; ")


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers.setdefault("Content-Security-Policy", CSP)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
    response.headers.setdefault("Cross-Origin-Resource-Policy", "same-origin")
    # Camera is required for in-form photo capture; nothing else is
    response.headers.setdefault(
        "Permissions-Policy",
        "camera=(self), microphone=(), geolocation=(), payment=(), usb=(), interest-cohort=()",
    )
    if "server" in response.headers:
        del response.headers["server"]
    if settings.cookie_secure:
        response.headers.setdefault(
            "Strict-Transport-Security", "max-age=31536000; includeSubDomains"
        )
    if request.url.path.startswith("/api/"):
        response.headers.setdefault("Cache-Control", "no-store")
    return response


# =========================================================================
#  Error handling - never leak internals to the client
# =========================================================================
@app.exception_handler(StarletteHTTPException)
async def http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    return JSONResponse(
        {"detail": exc.detail},
        status_code=exc.status_code,
        headers=getattr(exc, "headers", None),
    )


@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
    first = exc.errors()[0] if exc.errors() else {}
    field = ".".join(str(p) for p in first.get("loc", []) if p not in ("body",))
    message = first.get("msg", "נתונים לא תקינים")
    if message.startswith("Value error, "):
        message = message[len("Value error, "):]
    return JSONResponse(
        {"detail": f"{field}: {message}" if field else message},
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
    )


@app.exception_handler(Exception)
async def unhandled_error(request: Request, exc: Exception) -> JSONResponse:
    log.exception("unhandled error on %s %s", request.method, request.url.path)
    return JSONResponse(
        {"detail": "אירעה שגיאה בשרת. נסה שוב או פנה למנהל המערכת."},
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
    )


# =========================================================================
#  Routes
# =========================================================================
app.include_router(auth.router)
app.include_router(users.router)
app.include_router(cities.router)
app.include_router(surveys.router)
app.include_router(files.router)
app.include_router(exports.router)


@app.get("/api/health")
async def health() -> dict[str, Any]:
    try:
        await db.get_client().admin.command("ping")
        database = "up"
    except Exception:
        database = "down"
    return {"status": "ok" if database == "up" else "degraded", "database": database}


# ── Static frontend (mounted last so /api wins) ───────────────────────────
class RevalidatingStaticFiles(StaticFiles):
    """Serve assets with must-revalidate.

    Without this a browser keeps the previous app.js/app.css after an update
    while loading the new index.html, producing a half-updated UI. StaticFiles
    still emits ETag/Last-Modified, so revalidation costs one 304 per file.
    """

    def file_response(self, *args, **kwargs):
        response = super().file_response(*args, **kwargs)
        response.headers["Cache-Control"] = "no-cache, must-revalidate"
        return response


if os.path.isdir(FRONTEND_DIR):
    app.mount("/static", RevalidatingStaticFiles(directory=FRONTEND_DIR), name="static")

    @app.get("/", include_in_schema=False)
    async def index() -> FileResponse:
        return FileResponse(
            os.path.join(FRONTEND_DIR, "index.html"),
            headers={"Cache-Control": "no-cache"},
        )

    @app.get("/{path:path}", include_in_schema=False)
    async def spa_fallback(path: str) -> Any:
        if path.startswith("api/"):
            return JSONResponse({"detail": "לא נמצא"}, status_code=404)
        root = os.path.realpath(FRONTEND_DIR)
        candidate = os.path.realpath(os.path.join(root, path))
        if candidate.startswith(root + os.sep) and os.path.isfile(candidate):
            return FileResponse(candidate)
        return FileResponse(
            os.path.join(FRONTEND_DIR, "index.html"),
            headers={"Cache-Control": "no-cache"},
        )
