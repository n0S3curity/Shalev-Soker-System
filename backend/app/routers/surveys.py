"""Survey CRUD.

Visibility and editing are deliberately different: every signed-in surveyor can
read every survey (so the team sees the full picture), but only the surveyor who
created it - or an admin - may change it.

A business number identifies one business globally while the same business may
have several branches, so a survey is keyed by business number + city + address.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Optional

from bson import ObjectId
from bson.errors import InvalidId
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pymongo.errors import DuplicateKeyError

from .. import audit, db, security
from ..constants import (
    BUSINESS_TYPES,
    CARDBOARD_OPTIONS,
    CONTAINER_ORDER,
    CONTAINER_VOLUMES,
    FREQ_OPTIONS,
    NO_VOLUME_TYPES,
    OWNERSHIP_OPTIONS,
    SECTOR_OPTIONS,
    USAGE_OPTIONS,
    WET_OPTIONS,
)
from ..deps import CurrentUser, admin_user, can_edit_survey, current_user, require_edit_permission
from ..models import FileRef, SurveyOut, SurveyPayload

router = APIRouter(prefix="/api/surveys", tags=["surveys"])

WRITABLE_FIELDS = (
    "city", "biz_name", "biznum", "rep_name", "role", "rep_phone", "biz_phone",
    "address", "biz_type", "biz_type_std", "sector", "kitchen", "yard",
    "yard_size", "wet", "cardboard", "decl_given", "decl_ret", "emp_count", "notes",
)


def _oid(value: str) -> ObjectId:
    try:
        return ObjectId(value)
    except (InvalidId, TypeError):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "מזהה סקר לא תקין")


def address_key(address: str) -> str:
    """Normalised address used for branch uniqueness."""
    text = security.clean_text(address, 200).lower()
    text = re.sub(r"[\"'`,\.\-]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


async def _file_refs(ids: list[Any], kind: str) -> list[FileRef]:
    if not ids:
        return []
    object_ids = [i for i in ids if isinstance(i, ObjectId)]
    records = await db.get_db()["uploads.files"].find({"_id": {"$in": object_ids}}).to_list(100)
    by_id = {r["_id"]: r for r in records}
    refs: list[FileRef] = []
    for oid in object_ids:
        record = by_id.get(oid)
        if not record:
            continue
        metadata = record.get("metadata") or {}
        refs.append(FileRef(
            id=str(oid),
            name=record.get("filename", "file"),
            content_type=metadata.get("content_type", "application/octet-stream"),
            size=record.get("length", 0),
            kind=kind,  # type: ignore[arg-type]
        ))
    return refs


async def _serialise(record: dict[str, Any], user: CurrentUser) -> SurveyOut:
    signature_refs = await _file_refs([record.get("signature_id")] if record.get("signature_id") else [], "signature")
    return SurveyOut(
        id=str(record["_id"]),
        city=record.get("city", ""),
        biz_name=record.get("biz_name", ""),
        biznum=record.get("biznum", ""),
        rep_name=record.get("rep_name", ""),
        role=record.get("role", ""),
        rep_phone=record.get("rep_phone", ""),
        biz_phone=record.get("biz_phone", ""),
        address=record.get("address", ""),
        biz_type=record.get("biz_type", ""),
        biz_type_std=record.get("biz_type_std", ""),
        sector=record.get("sector", ""),
        kitchen=record.get("kitchen", ""),
        yard=record.get("yard", ""),
        yard_size=record.get("yard_size", ""),
        containers=record.get("containers", []),
        wet=record.get("wet", ""),
        cardboard=record.get("cardboard", ""),
        decl_given=record.get("decl_given", ""),
        decl_ret=record.get("decl_ret", ""),
        emp_count=record.get("emp_count", ""),
        notes=record.get("notes", ""),
        images=await _file_refs(record.get("image_ids", []), "image"),
        docs=await _file_refs(record.get("doc_ids", []), "doc"),
        signature=signature_refs[0] if signature_refs else None,
        owner_email=record.get("owner_email", ""),
        owner_name=record.get("owner_name", ""),
        created_at=record.get("created_at"),
        updated_at=record.get("updated_at"),
        updated_by=record.get("updated_by", ""),
        can_edit=can_edit_survey(user, record),
    )


async def _claim_files(ids: list[str], survey_id: ObjectId, user: CurrentUser, kind: str) -> list[ObjectId]:
    """Attach uploads to a survey after checking the caller may use them."""
    claimed: list[ObjectId] = []
    for raw_id in ids:
        oid = _oid(raw_id)
        record = await db.get_db()["uploads.files"].find_one({"_id": oid})
        if not record:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "אחד הקבצים לא נמצא")
        metadata = record.get("metadata") or {}
        existing = metadata.get("survey_id")
        if existing not in (None, survey_id):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "הקובץ משויך לסקר אחר")
        if existing is None and metadata.get("owner_email") != user.email and not user.is_admin:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "אין הרשאה לצרף את הקובץ")
        if metadata.get("kind") != kind:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "סוג הקובץ אינו תואם")
        await db.get_db()["uploads.files"].update_one(
            {"_id": oid}, {"$set": {"metadata.survey_id": survey_id}}
        )
        claimed.append(oid)
    return claimed


async def _drop_files(ids: list[ObjectId]) -> None:
    bucket = db.gridfs()
    for oid in ids:
        try:
            await bucket.delete(oid)
        except Exception:  # already gone
            pass


async def _ensure_city(name: str) -> str:
    city = await db.cities().find_one({"name": name, "active": True})
    if not city:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "העיר אינה קיימת או אינה פעילה")
    return city["name"]


# =========================================================================
#  Catalogue for the form UI
# =========================================================================
@router.get("/meta/catalog")
async def catalog(user: CurrentUser = Depends(current_user)) -> dict[str, Any]:
    return {
        "container_volumes": CONTAINER_VOLUMES,
        "no_volume_types": sorted(NO_VOLUME_TYPES),
        "container_order": CONTAINER_ORDER,
        "freq_options": FREQ_OPTIONS,
        "ownership_options": OWNERSHIP_OPTIONS,
        "usage_options": USAGE_OPTIONS,
        "sector_options": SECTOR_OPTIONS,
        "wet_options": WET_OPTIONS,
        "cardboard_options": CARDBOARD_OPTIONS,
        "business_types": BUSINESS_TYPES,
    }


# =========================================================================
#  Read
# =========================================================================
@router.get("", response_model=dict)
async def list_surveys(
    user: CurrentUser = Depends(current_user),
    city: Optional[str] = Query(default=None, max_length=60),
    q: Optional[str] = Query(default=None, max_length=120),
    mine: bool = False,
    owner: Optional[str] = Query(default=None, max_length=200),
    page: int = Query(default=1, ge=1, le=1000),
    page_size: int = Query(default=50, ge=1, le=200),
) -> dict[str, Any]:
    query: dict[str, Any] = {"deleted": False}

    if city:
        query["city"] = security.safe_query_value(city)
    if mine:
        query["owner_email"] = user.email
    elif owner and user.is_admin:
        query["owner_email"] = security.safe_query_value(owner).lower()

    if q:
        pattern = security.escape_regex(q)
        query["$or"] = [
            {"biz_name": {"$regex": pattern, "$options": "i"}},
            {"biznum": {"$regex": pattern, "$options": "i"}},
            {"address": {"$regex": pattern, "$options": "i"}},
            {"rep_name": {"$regex": pattern, "$options": "i"}},
        ]

    total = await db.surveys().count_documents(query)
    cursor = (
        db.surveys()
        .find(query)
        .sort("updated_at", -1)
        .skip((page - 1) * page_size)
        .limit(page_size)
    )
    records = await cursor.to_list(page_size)

    items = []
    for record in records:
        containers = record.get("containers", [])
        items.append({
            "id": str(record["_id"]),
            "city": record.get("city", ""),
            "biz_name": record.get("biz_name", ""),
            "biznum": record.get("biznum", ""),
            "address": record.get("address", ""),
            "biz_type": record.get("biz_type", ""),
            "owner_email": record.get("owner_email", ""),
            "owner_name": record.get("owner_name", ""),
            "updated_at": record.get("updated_at"),
            "container_summary": ", ".join(
                f"{c.get('ctype', '')} {c.get('vol') or ''}".strip() for c in containers
            ),
            "image_count": len(record.get("image_ids", [])),
            "doc_count": len(record.get("doc_ids", [])),
            "has_signature": bool(record.get("signature_id")),
            "can_edit": can_edit_survey(user, record),
        })

    return {"items": items, "total": total, "page": page, "page_size": page_size}


@router.get("/lookup")
async def lookup_by_biznum(
    biznum: str = Query(..., min_length=2, max_length=40),
    user: CurrentUser = Depends(current_user),
) -> dict[str, Any]:
    """Find every branch already recorded for a business number."""
    value = re.sub(r"[^0-9A-Za-z\-]", "", security.safe_query_value(biznum))
    if not value:
        return {"branches": []}

    records = await db.surveys().find(
        {"biznum": value, "deleted": False}
    ).sort("city", 1).to_list(50)

    return {
        "branches": [
            {
                "id": str(r["_id"]),
                "city": r.get("city", ""),
                "biz_name": r.get("biz_name", ""),
                "address": r.get("address", ""),
                "owner_name": r.get("owner_name", ""),
                "owner_email": r.get("owner_email", ""),
                "updated_at": r.get("updated_at"),
                "can_edit": can_edit_survey(user, r),
            }
            for r in records
        ]
    }


@router.get("/{survey_id}", response_model=SurveyOut)
async def get_survey(survey_id: str, user: CurrentUser = Depends(current_user)) -> SurveyOut:
    record = await db.surveys().find_one({"_id": _oid(survey_id), "deleted": False})
    if not record:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "הסקר לא נמצא")
    return await _serialise(record, user)


# =========================================================================
#  Write
# =========================================================================
@router.post("", response_model=SurveyOut, status_code=status.HTTP_201_CREATED)
async def create_survey(
    payload: SurveyPayload,
    request: Request,
    user: CurrentUser = Depends(current_user),
) -> SurveyOut:
    security.reject_operator_keys(payload.model_dump())
    city = await _ensure_city(payload.city)
    now = datetime.now(timezone.utc)

    document: dict[str, Any] = {field: getattr(payload, field) for field in WRITABLE_FIELDS}
    document.update({
        "city": city,
        "address_key": address_key(payload.address),
        "containers": [c.model_dump() for c in payload.containers],
        "image_ids": [],
        "doc_ids": [],
        "signature_id": None,
        "owner_email": user.email,
        "owner_name": user.name,
        "created_at": now,
        "updated_at": now,
        "updated_by": user.email,
        "deleted": False,
    })

    try:
        result = await db.surveys().insert_one(document)
    except DuplicateKeyError:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "כבר קיים סקר לעסק הזה באותה כתובת ובאותה עיר. חפש אותו ברשימת הסקרים כדי לערוך.",
        )

    survey_id = result.inserted_id
    image_ids = await _claim_files(payload.image_ids, survey_id, user, "image")
    doc_ids = await _claim_files(payload.doc_ids, survey_id, user, "doc")
    signature_ids = await _claim_files(
        [payload.signature_id] if payload.signature_id else [], survey_id, user, "signature"
    )

    await db.surveys().update_one(
        {"_id": survey_id},
        {"$set": {
            "image_ids": image_ids,
            "doc_ids": doc_ids,
            "signature_id": signature_ids[0] if signature_ids else None,
        }},
    )
    await audit.record("survey.create", user.email, request, target=str(survey_id),
                       details={"city": city, "biznum": payload.biznum})

    record = await db.surveys().find_one({"_id": survey_id})
    return await _serialise(record or document, user)


@router.put("/{survey_id}", response_model=SurveyOut)
async def update_survey(
    survey_id: str,
    payload: SurveyPayload,
    request: Request,
    user: CurrentUser = Depends(current_user),
) -> SurveyOut:
    security.reject_operator_keys(payload.model_dump())
    oid = _oid(survey_id)
    record = await db.surveys().find_one({"_id": oid, "deleted": False})
    if not record:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "הסקר לא נמצא")
    require_edit_permission(user, record)

    city = await _ensure_city(payload.city)

    # ── Signatures are final ───────────────────────────────────────────────
    existing_signature = record.get("signature_id")
    signature_id = existing_signature
    if existing_signature:
        sent = payload.signature_id
        if sent != str(existing_signature):
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                "לא ניתן לשנות חתימה שכבר נשמרה. פנה למנהל אם נדרש איפוס.",
            )
    elif payload.signature_id:
        claimed = await _claim_files([payload.signature_id], oid, user, "signature")
        signature_id = claimed[0] if claimed else None

    image_ids = await _claim_files(payload.image_ids, oid, user, "image")
    doc_ids = await _claim_files(payload.doc_ids, oid, user, "doc")

    removed = [
        oid_
        for oid_ in list(record.get("image_ids", [])) + list(record.get("doc_ids", []))
        if oid_ not in image_ids and oid_ not in doc_ids
    ]

    updates: dict[str, Any] = {field: getattr(payload, field) for field in WRITABLE_FIELDS}
    updates.update({
        "city": city,
        "address_key": address_key(payload.address),
        "containers": [c.model_dump() for c in payload.containers],
        "image_ids": image_ids,
        "doc_ids": doc_ids,
        "signature_id": signature_id,
        "updated_at": datetime.now(timezone.utc),
        "updated_by": user.email,
    })

    try:
        await db.surveys().update_one({"_id": oid}, {"$set": updates})
    except DuplicateKeyError:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "קיים כבר סקר אחר לעסק הזה באותה כתובת ובאותה עיר",
        )

    await _drop_files(removed)
    await audit.record("survey.update", user.email, request, target=survey_id,
                       details={"city": city, "removed_files": len(removed)})

    updated = await db.surveys().find_one({"_id": oid})
    return await _serialise(updated or record, user)


@router.delete("/{survey_id}")
async def delete_survey(
    survey_id: str,
    request: Request,
    user: CurrentUser = Depends(current_user),
) -> dict[str, str]:
    oid = _oid(survey_id)
    record = await db.surveys().find_one({"_id": oid, "deleted": False})
    if not record:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "הסקר לא נמצא")
    require_edit_permission(user, record)

    # Soft delete keeps the data recoverable and out of the unique index
    await db.surveys().update_one(
        {"_id": oid},
        {"$set": {
            "deleted": True,
            "deleted_at": datetime.now(timezone.utc),
            "deleted_by": user.email,
        }},
    )
    await audit.record("survey.delete", user.email, request, target=survey_id,
                       details={"biznum": record.get("biznum")})
    return {"status": "ok"}


@router.post("/{survey_id}/signature/reset")
async def reset_signature(
    survey_id: str,
    request: Request,
    admin: CurrentUser = Depends(admin_user),
) -> dict[str, str]:
    """Admin-only escape hatch when a signature was captured incorrectly."""
    oid = _oid(survey_id)
    record = await db.surveys().find_one({"_id": oid, "deleted": False})
    if not record:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "הסקר לא נמצא")

    old = record.get("signature_id")
    await db.surveys().update_one({"_id": oid}, {"$set": {"signature_id": None}})
    if old:
        await _drop_files([old])
    await audit.record("survey.signature_reset", admin.email, request, target=survey_id)
    return {"status": "ok"}
