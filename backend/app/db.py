"""MongoDB connection, collections and index bootstrap."""
from __future__ import annotations

import logging
from typing import Optional

from motor.motor_asyncio import (
    AsyncIOMotorClient,
    AsyncIOMotorDatabase,
    AsyncIOMotorGridFSBucket,
)
from pymongo import ASCENDING, DESCENDING, IndexModel

from .config import settings

log = logging.getLogger("db")

_client: Optional[AsyncIOMotorClient] = None
_db: Optional[AsyncIOMotorDatabase] = None


def get_client() -> AsyncIOMotorClient:
    global _client
    if _client is None:
        _client = AsyncIOMotorClient(
            settings.mongo_uri,
            serverSelectionTimeoutMS=8000,
            uuidRepresentation="standard",
            maxPoolSize=60,
        )
    return _client


def get_db() -> AsyncIOMotorDatabase:
    global _db
    if _db is None:
        _db = get_client()[settings.mongo_db]
    return _db


def gridfs() -> AsyncIOMotorGridFSBucket:
    return AsyncIOMotorGridFSBucket(get_db(), bucket_name="uploads")


# ── Collection accessors ───────────────────────────────────────────────────
def users():
    return get_db()["users"]


def cities():
    return get_db()["cities"]


def surveys():
    return get_db()["surveys"]


def audit():
    return get_db()["audit"]


def app_settings():
    return get_db()["settings"]


def rate_limits():
    return get_db()["rate_limits"]


def sessions():
    return get_db()["sessions"]


async def init_indexes() -> None:
    """Create every index the app relies on. Safe to run repeatedly."""
    await users().create_indexes([
        IndexModel([("email", ASCENDING)], unique=True, name="uq_email"),
        IndexModel([("active", ASCENDING)], name="ix_active"),
        # Password reset looks an account up by token hash
        IndexModel(
            [("reset_token_hash", ASCENDING)],
            name="ix_reset_token",
            sparse=True,
        ),
    ])

    await cities().create_indexes([
        IndexModel([("name", ASCENDING)], unique=True, name="uq_city_name"),
    ])

    await surveys().create_indexes([
        # A business number identifies one business globally, but the same
        # business may have several branches - one survey per branch address.
        IndexModel(
            [("biznum", ASCENDING), ("city", ASCENDING), ("address_key", ASCENDING)],
            unique=True,
            name="uq_biznum_city_branch",
            partialFilterExpression={"deleted": False},
        ),
        IndexModel([("biznum", ASCENDING)], name="ix_biznum"),
        IndexModel([("city", ASCENDING), ("updated_at", DESCENDING)], name="ix_city_updated"),
        IndexModel([("owner_email", ASCENDING), ("updated_at", DESCENDING)], name="ix_owner"),
        IndexModel([("updated_at", DESCENDING)], name="ix_updated"),
        IndexModel([("deleted", ASCENDING)], name="ix_deleted"),
        IndexModel(
            [("biz_name", "text"), ("biznum", "text"), ("address", "text")],
            name="tx_search",
            default_language="none",
        ),
    ])

    await audit().create_indexes([
        IndexModel([("ts", DESCENDING)], name="ix_ts"),
        IndexModel([("actor", ASCENDING), ("ts", DESCENDING)], name="ix_actor_ts"),
        # keep two years of audit trail, then expire automatically
        IndexModel([("ts", ASCENDING)], name="ttl_ts", expireAfterSeconds=63072000),
    ])

    await rate_limits().create_indexes([
        IndexModel([("key", ASCENDING)], unique=True, name="uq_key"),
        IndexModel([("expires_at", ASCENDING)], name="ttl_exp", expireAfterSeconds=0),
    ])

    await sessions().create_indexes([
        IndexModel([("jti", ASCENDING)], unique=True, name="uq_jti"),
        IndexModel([("email", ASCENDING)], name="ix_email"),
        IndexModel([("expires_at", ASCENDING)], name="ttl_exp", expireAfterSeconds=0),
    ])

    # Orphaned upload cleanup relies on this
    await get_db()["uploads.files"].create_indexes([
        IndexModel([("metadata.survey_id", ASCENDING)], name="ix_survey"),
        IndexModel([("uploadDate", DESCENDING)], name="ix_uploaded"),
    ])

    log.info("indexes ready")


async def close() -> None:
    global _client, _db
    if _client is not None:
        _client.close()
    _client = None
    _db = None
