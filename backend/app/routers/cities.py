"""Municipality (city) catalogue. Everyone reads it, only the admin changes it."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from bson import ObjectId
from bson.errors import InvalidId
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pymongo.errors import DuplicateKeyError

from .. import audit, db, security
from ..deps import CurrentUser, admin_user, current_user
from ..models import CityCreate, CityOut, CityUpdate

router = APIRouter(prefix="/api/cities", tags=["cities"])


def _oid(value: str) -> ObjectId:
    try:
        return ObjectId(value)
    except (InvalidId, TypeError):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "מזהה לא תקין")


@router.get("", response_model=list[CityOut])
async def list_cities(
    include_inactive: bool = False,
    user: CurrentUser = Depends(current_user),
) -> list[CityOut]:
    query: dict[str, Any] = {}
    if not (include_inactive and user.is_admin):
        query["active"] = True

    records = await db.cities().find(query).sort("name", 1).to_list(500)

    counts_cursor = db.surveys().aggregate([
        {"$match": {"deleted": False}},
        {"$group": {"_id": "$city", "n": {"$sum": 1}}},
    ])
    counts = {row["_id"]: row["n"] async for row in counts_cursor}

    return [
        CityOut(
            id=str(r["_id"]),
            name=r["name"],
            active=r.get("active", True),
            survey_count=counts.get(r["name"], 0),
        )
        for r in records
    ]


@router.post("", response_model=CityOut, status_code=status.HTTP_201_CREATED)
async def create_city(
    payload: CityCreate,
    request: Request,
    admin: CurrentUser = Depends(admin_user),
) -> CityOut:
    document = {
        "name": payload.name,
        "active": True,
        "created_at": datetime.now(timezone.utc),
        "created_by": admin.email,
    }
    try:
        result = await db.cities().insert_one(document)
    except DuplicateKeyError:
        raise HTTPException(status.HTTP_409_CONFLICT, "העיר כבר קיימת ברשימה")

    await audit.record("city.create", admin.email, request, target=payload.name)
    return CityOut(id=str(result.inserted_id), name=payload.name, active=True, survey_count=0)


@router.patch("/{city_id}", response_model=CityOut)
async def update_city(
    city_id: str,
    payload: CityUpdate,
    request: Request,
    admin: CurrentUser = Depends(admin_user),
) -> CityOut:
    record = await db.cities().find_one({"_id": _oid(city_id)})
    if not record:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "העיר לא נמצאה")

    updates: dict[str, Any] = {}
    new_name = None
    if payload.name is not None:
        new_name = security.clean_text(payload.name, 60)
        if not new_name:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "שם עיר לא תקין")
        updates["name"] = new_name
    if payload.active is not None:
        updates["active"] = payload.active

    if updates:
        try:
            await db.cities().update_one({"_id": record["_id"]}, {"$set": updates})
        except DuplicateKeyError:
            raise HTTPException(status.HTTP_409_CONFLICT, "כבר קיימת עיר בשם הזה")

        # Surveys store the city by name, so a rename has to follow through
        if new_name and new_name != record["name"]:
            await db.surveys().update_many(
                {"city": record["name"]}, {"$set": {"city": new_name}}
            )
        await audit.record("city.update", admin.email, request, target=record["name"],
                           details={"changes": updates})
        record.update(updates)

    count = await db.surveys().count_documents({"city": record["name"], "deleted": False})
    return CityOut(
        id=str(record["_id"]),
        name=record["name"],
        active=record.get("active", True),
        survey_count=count,
    )


@router.delete("/{city_id}")
async def delete_city(
    city_id: str,
    request: Request,
    admin: CurrentUser = Depends(admin_user),
) -> dict[str, Any]:
    record = await db.cities().find_one({"_id": _oid(city_id)})
    if not record:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "העיר לא נמצאה")

    count = await db.surveys().count_documents({"city": record["name"], "deleted": False})
    if count:
        # Never orphan survey data - deactivate instead of deleting
        await db.cities().update_one({"_id": record["_id"]}, {"$set": {"active": False}})
        await audit.record("city.deactivate", admin.email, request, target=record["name"],
                           details={"surveys": count})
        return {
            "status": "deactivated",
            "surveys": count,
            "message": f"לעיר משויכים {count} סקרים, לכן היא הועברה למצב לא פעיל במקום להימחק",
        }

    await db.cities().delete_one({"_id": record["_id"]})
    await audit.record("city.delete", admin.email, request, target=record["name"])
    return {"status": "deleted"}
