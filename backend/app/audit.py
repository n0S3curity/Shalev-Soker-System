"""Append-only audit trail (OWASP A09 - Logging and Monitoring Failures)."""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import Request

from . import db, security

log = logging.getLogger("audit")


async def record(
    action: str,
    actor: str,
    request: Optional[Request] = None,
    target: str = "",
    details: Optional[dict[str, Any]] = None,
    success: bool = True,
) -> None:
    entry = {
        "ts": datetime.now(timezone.utc),
        "action": action,
        "actor": actor or "anonymous",
        "target": target,
        "success": success,
        "details": details or {},
        "ip": security.client_ip(request) if request else "",
        "user_agent": (request.headers.get("user-agent", "")[:200] if request else ""),
    }
    try:
        await db.audit().insert_one(entry)
    except Exception:  # auditing must never break the request
        log.exception("failed to write audit entry for %s", action)
    log.info("audit %s actor=%s target=%s success=%s", action, entry["actor"], target, success)
