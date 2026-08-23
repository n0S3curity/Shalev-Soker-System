"""SMTP delivery for the daily report."""
from __future__ import annotations

import logging
from email.message import EmailMessage
from typing import Iterable, Optional

import aiosmtplib

from ..config import settings

log = logging.getLogger("mailer")

XLSX_MIME = ("application", "vnd.openxmlformats-officedocument.spreadsheetml.sheet")


class MailNotConfigured(RuntimeError):
    pass


def is_configured() -> bool:
    return bool(settings.smtp_host and settings.smtp_user and settings.smtp_password)


async def send_mail(
    to: str,
    subject: str,
    body: str,
    attachments: Optional[Iterable[tuple[str, bytes]]] = None,
) -> None:
    if not is_configured():
        raise MailNotConfigured(
            "SMTP אינו מוגדר. הגדר SMTP_USER ו-SMTP_PASSWORD בקובץ ה-env."
        )

    message = EmailMessage()
    message["From"] = settings.mail_from or settings.smtp_user
    message["To"] = to
    message["Subject"] = subject
    message.set_content(body)

    for filename, payload in attachments or []:
        message.add_attachment(
            payload,
            maintype=XLSX_MIME[0],
            subtype=XLSX_MIME[1],
            filename=filename,
        )

    await aiosmtplib.send(
        message,
        hostname=settings.smtp_host,
        port=settings.smtp_port,
        username=settings.smtp_user,
        password=settings.smtp_password,
        start_tls=settings.smtp_port == 587,
        use_tls=settings.smtp_port == 465,
        timeout=60,
    )
    log.info("mail sent to %s subject=%s", to, subject)
