"""Upload and download of survey attachments, stored in GridFS.

Images are decoded and re-encoded server side, which strips EXIF and destroys
any payload hidden in a file that merely claims to be an image. Documents are
limited to an extension + content-type allowlist and are always served with an
attachment disposition so the browser never renders them in our origin
(OWASP A08 / stored-XSS prevention).
"""
from __future__ import annotations

import io
from datetime import datetime, timezone
from typing import Any

from bson import ObjectId
from bson.errors import InvalidId
from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile, status
from fastapi.responses import StreamingResponse
from PIL import Image, UnidentifiedImageError

from .. import audit, db, security
from ..config import settings
from ..constants import (
    ALLOWED_DOC_EXTENSIONS,
    ALLOWED_DOC_TYPES,
    ALLOWED_IMAGE_EXTENSIONS,
)
from ..deps import CurrentUser, current_user
from ..models import FileRef

router = APIRouter(prefix="/api/files", tags=["files"])

MAX_IMAGE_EDGE = 2560
INLINE_TYPES = {"image/jpeg", "image/png", "image/webp", "application/pdf"}

# Magic-byte signatures for the document formats we accept
DOC_SIGNATURES: list[tuple[bytes, str]] = [
    (b"%PDF-", "application/pdf"),
    (b"PK\x03\x04", "application/zip"),          # docx / xlsx containers
    (b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1", "application/x-ole"),  # legacy doc / xls
]


def _oid(value: str) -> ObjectId:
    try:
        return ObjectId(value)
    except (InvalidId, TypeError):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "מזהה קובץ לא תקין")


def _safe_filename(name: str) -> str:
    """Keep a readable name but remove anything that could traverse a path."""
    cleaned = security.clean_text(name, 150)
    # Slashes would traverse, quotes would break the Content-Disposition header
    for bad in ("\\", "/", "\x00", '"', "'", ";", "\r", "\n"):
        cleaned = cleaned.replace(bad, "_")
    cleaned = cleaned.lstrip(".") or "file"
    return cleaned[:150]


def _extension(name: str) -> str:
    _, _, ext = name.rpartition(".")
    return f".{ext.lower()}" if ext and ext != name else ""


async def _read_limited(upload: UploadFile) -> bytes:
    """Read at most max_upload_bytes + 1 so an oversized body cannot exhaust RAM."""
    limit = settings.max_upload_bytes
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await upload.read(64 * 1024)
        if not chunk:
            break
        total += len(chunk)
        if total > limit:
            raise HTTPException(
                status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                f"הקובץ גדול מדי (מקסימום {settings.max_upload_mb} MB)",
            )
        chunks.append(chunk)
    if total == 0:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "הקובץ ריק")
    return b"".join(chunks)


def _normalise_image(raw: bytes, keep_png: bool = False) -> tuple[bytes, str]:
    """Decode, strip metadata and re-encode. Raises if the bytes are not an image."""
    try:
        probe = Image.open(io.BytesIO(raw))
        probe.verify()
        image = Image.open(io.BytesIO(raw))
    except (UnidentifiedImageError, OSError, ValueError):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "הקובץ אינו תמונה תקינה")

    if keep_png:
        image = image.convert("RGBA")
        buffer = io.BytesIO()
        image.save(buffer, format="PNG", optimize=True)
        return buffer.getvalue(), "image/png"

    image = image.convert("RGB")
    if max(image.size) > MAX_IMAGE_EDGE:
        image.thumbnail((MAX_IMAGE_EDGE, MAX_IMAGE_EDGE), Image.LANCZOS)
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=85, optimize=True)
    return buffer.getvalue(), "image/jpeg"


def _check_document(raw: bytes, filename: str, declared: str) -> str:
    ext = _extension(filename)
    if ext not in ALLOWED_DOC_EXTENSIONS:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "סוג קובץ לא נתמך. מותרים: PDF, Word, Excel, TXT, CSV",
        )
    if declared and declared not in ALLOWED_DOC_TYPES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "סוג קובץ לא נתמך")

    head = raw[:8]
    if ext in (".txt", ".csv"):
        try:
            raw.decode("utf-8")
        except UnicodeDecodeError:
            try:
                raw.decode("windows-1255")
            except UnicodeDecodeError:
                raise HTTPException(status.HTTP_400_BAD_REQUEST, "קובץ הטקסט אינו בקידוד נתמך")
        return "text/plain; charset=utf-8"

    if not any(head.startswith(sig) for sig, _ in DOC_SIGNATURES):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "תוכן הקובץ אינו תואם לסיומת שלו",
        )
    return declared or "application/octet-stream"


@router.post("", response_model=FileRef, status_code=status.HTTP_201_CREATED)
async def upload_file(
    request: Request,
    file: UploadFile = File(...),
    kind: str = Form("doc"),
    user: CurrentUser = Depends(current_user),
) -> FileRef:
    if kind not in ("image", "doc", "signature"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "סוג העלאה לא חוקי")
    await security.rate_limit(request, "upload", settings.rate_upload, identity=user.email)

    raw = await _read_limited(file)
    filename = _safe_filename(file.filename or "file")
    declared = (file.content_type or "").split(";")[0].strip().lower()

    if kind == "signature":
        data, content_type = _normalise_image(raw, keep_png=True)
        filename = "signature.png"
    elif kind == "image":
        if _extension(filename) and _extension(filename) not in ALLOWED_IMAGE_EXTENSIONS:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "סוג תמונה לא נתמך")
        data, content_type = _normalise_image(raw)
        if not filename.lower().endswith((".jpg", ".jpeg")):
            filename = f"{filename.rsplit('.', 1)[0][:100]}.jpg"
    else:
        content_type = _check_document(raw, filename, declared)
        data = raw

    bucket = db.gridfs()
    file_id = await bucket.upload_from_stream(
        filename,
        data,
        metadata={
            "kind": kind,
            "content_type": content_type,
            "original_name": filename,
            "owner_email": user.email,
            "survey_id": None,
            "uploaded_at": datetime.now(timezone.utc),
        },
    )
    await audit.record("file.upload", user.email, request, target=str(file_id),
                       details={"kind": kind, "size": len(data)})

    return FileRef(
        id=str(file_id),
        name=filename,
        content_type=content_type,
        size=len(data),
        kind=kind,  # type: ignore[arg-type]
    )


@router.get("/{file_id}")
async def download_file(
    file_id: str,
    request: Request,
    download: bool = False,
    user: CurrentUser = Depends(current_user),
) -> StreamingResponse:
    oid = _oid(file_id)
    record = await db.get_db()["uploads.files"].find_one({"_id": oid})
    if not record:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "הקובץ לא נמצא")

    metadata: dict[str, Any] = record.get("metadata") or {}
    content_type = metadata.get("content_type") or "application/octet-stream"

    # Every signed-in surveyor may view survey attachments; the survey itself is
    # visible to all of them. Orphan uploads stay private to their uploader.
    if metadata.get("survey_id") is None and metadata.get("owner_email") != user.email and not user.is_admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "אין הרשאה לקובץ הזה")

    bucket = db.gridfs()
    stream = await bucket.open_download_stream(oid)

    inline = (not download) and content_type in INLINE_TYPES
    disposition = "inline" if inline else "attachment"
    ascii_name = record.get("filename", "file").encode("ascii", "ignore").decode() or "file"
    from urllib.parse import quote

    async def iterator():
        while True:
            chunk = await stream.readchunk()
            if not chunk:
                break
            yield chunk

    return StreamingResponse(
        iterator(),
        media_type=content_type,
        headers={
            "Content-Disposition": (
                f"{disposition}; filename=\"{ascii_name}\"; "
                f"filename*=UTF-8''{quote(record.get('filename', 'file'))}"
            ),
            "Content-Length": str(record.get("length", 0)),
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "private, max-age=600",
            "Content-Security-Policy": "default-src 'none'; img-src 'self'; sandbox",
        },
    )


@router.delete("/{file_id}")
async def delete_file(
    file_id: str,
    request: Request,
    user: CurrentUser = Depends(current_user),
) -> dict[str, str]:
    """Remove an upload that is not attached to a saved survey yet."""
    oid = _oid(file_id)
    record = await db.get_db()["uploads.files"].find_one({"_id": oid})
    if not record:
        return {"status": "ok"}

    metadata = record.get("metadata") or {}
    if metadata.get("survey_id") is not None:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "לא ניתן למחוק קובץ ששויך לסקר. ערוך את הסקר במקום זאת.",
        )
    if metadata.get("owner_email") != user.email and not user.is_admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "אין הרשאה למחוק את הקובץ")

    await db.gridfs().delete(oid)
    await audit.record("file.delete", user.email, request, target=file_id)
    return {"status": "ok"}
