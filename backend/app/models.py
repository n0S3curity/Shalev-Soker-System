"""Pydantic request and response models.

Everything the client sends passes through these models first: unknown fields
are rejected, strings are length capped and enum-like fields are constrained to
a known allowlist. That is the primary defence against injection and mass
assignment (OWASP A03 / A08).
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Annotated, Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from .constants import (
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
from .security import clean_text

Str100 = Annotated[str, Field(max_length=100)]
Str200 = Annotated[str, Field(max_length=200)]
Str4000 = Annotated[str, Field(max_length=4000)]

OBJECT_ID_RE = re.compile(r"^[0-9a-fA-F]{24}$")
TIME_RE = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


# =========================================================================
#  Auth
# =========================================================================
class LoginRequest(StrictModel):
    email: EmailStr
    password: Annotated[str, Field(min_length=1, max_length=200)]

    @field_validator("email")
    @classmethod
    def _normalise(cls, v: str) -> str:
        return v.strip().lower()


class FirstLoginRequest(StrictModel):
    """Exchange the one-time password for a password of the user's choosing."""

    change_token: Annotated[str, Field(min_length=10, max_length=4000)]
    new_password: Annotated[str, Field(min_length=1, max_length=200)]
    confirm_password: Annotated[str, Field(min_length=1, max_length=200)]


class ChangePasswordRequest(StrictModel):
    current_password: Annotated[str, Field(min_length=1, max_length=200)]
    new_password: Annotated[str, Field(min_length=1, max_length=200)]


class ForgotPasswordRequest(StrictModel):
    email: EmailStr

    @field_validator("email")
    @classmethod
    def _normalise(cls, v: str) -> str:
        return v.strip().lower()


class ResetPasswordRequest(StrictModel):
    token: Annotated[str, Field(min_length=20, max_length=200)]
    new_password: Annotated[str, Field(min_length=1, max_length=200)]
    confirm_password: Annotated[str, Field(min_length=1, max_length=200)]


class SessionUser(BaseModel):
    email: str
    name: str
    role: Literal["admin", "user"]
    csrf: str


# =========================================================================
#  Users
# =========================================================================
class UserCreate(StrictModel):
    email: EmailStr
    name: Str100 = ""
    role: Literal["admin", "user"] = "user"
    active: bool = True

    @field_validator("email")
    @classmethod
    def _normalise(cls, v: str) -> str:
        return v.strip().lower()


class UserUpdate(StrictModel):
    name: Optional[Str100] = None
    role: Optional[Literal["admin", "user"]] = None
    active: Optional[bool] = None


class UserOut(BaseModel):
    id: str
    email: str
    name: str
    role: str
    active: bool
    created_at: Optional[datetime] = None
    last_login: Optional[datetime] = None
    survey_count: int = 0
    must_change_password: bool = False
    locked: bool = False
    # Only ever populated in the response that creates or resets an account.
    one_time_password: Optional[str] = None


# =========================================================================
#  Cities
# =========================================================================
class CityCreate(StrictModel):
    name: Annotated[str, Field(min_length=1, max_length=60)]

    @field_validator("name")
    @classmethod
    def _clean(cls, v: str) -> str:
        name = clean_text(v, 60)
        if not name:
            raise ValueError("שם עיר לא תקין")
        return name


class CityUpdate(StrictModel):
    name: Optional[Annotated[str, Field(min_length=1, max_length=60)]] = None
    active: Optional[bool] = None


class CityOut(BaseModel):
    id: str
    name: str
    active: bool
    survey_count: int = 0


# =========================================================================
#  Survey
# =========================================================================
class ContainerEntry(StrictModel):
    ctype: str
    vol: Optional[str] = None
    qty: Optional[int] = Field(default=None, ge=1, le=999)
    freq: str = ""
    freqOther: Annotated[str, Field(max_length=120)] = ""
    ownership: str = ""
    usage: str = ""

    @field_validator("ctype")
    @classmethod
    def _ctype(cls, v: str) -> str:
        if v not in CONTAINER_ORDER:
            raise ValueError("סוג כלי אצירה לא מוכר")
        return v

    @field_validator("freq")
    @classmethod
    def _freq(cls, v: str) -> str:
        if v and v not in FREQ_OPTIONS:
            raise ValueError("תדירות פינוי לא מוכרת")
        return v

    @field_validator("ownership")
    @classmethod
    def _ownership(cls, v: str) -> str:
        if v and v not in OWNERSHIP_OPTIONS:
            raise ValueError("בעלות לא מוכרת")
        return v

    @field_validator("usage")
    @classmethod
    def _usage(cls, v: str) -> str:
        if v and v not in USAGE_OPTIONS:
            raise ValueError("שימוש לא מוכר")
        return v

    def model_post_init(self, _context: Any) -> None:
        if self.ctype in NO_VOLUME_TYPES:
            object.__setattr__(self, "vol", None)
        else:
            allowed = CONTAINER_VOLUMES.get(self.ctype, [])
            if self.vol is not None and self.vol not in allowed:
                raise ValueError(f"נפח לא חוקי עבור {self.ctype}")


class FileRef(BaseModel):
    id: str
    name: str
    content_type: str
    size: int = 0
    kind: Literal["image", "doc", "signature"] = "doc"


class SurveyPayload(StrictModel):
    """Fields a surveyor may write. Ownership and timestamps are server-set."""

    city: Annotated[str, Field(min_length=1, max_length=60)]
    biz_name: Annotated[str, Field(min_length=1, max_length=160)]
    biznum: Annotated[str, Field(min_length=1, max_length=40)]
    rep_name: Str100 = ""
    role: Str100 = ""
    rep_phone: Annotated[str, Field(max_length=30)] = ""
    biz_phone: Annotated[str, Field(max_length=30)] = ""
    address: Annotated[str, Field(min_length=1, max_length=200)]
    biz_type: Str100 = ""
    biz_type_std: Str100 = ""
    sector: str = ""
    kitchen: str = ""
    yard: str = ""
    yard_size: Annotated[str, Field(max_length=20)] = ""
    containers: Annotated[list[ContainerEntry], Field(max_length=60)] = []
    wet: str = ""
    cardboard: str = ""
    decl_given: str = ""
    decl_ret: str = ""
    emp_count: Annotated[str, Field(max_length=10)] = ""
    notes: Str4000 = ""
    image_ids: Annotated[list[str], Field(max_length=40)] = []
    doc_ids: Annotated[list[str], Field(max_length=40)] = []
    signature_id: Optional[str] = None

    @field_validator("biznum")
    @classmethod
    def _biznum(cls, v: str) -> str:
        value = re.sub(r"[^0-9A-Za-z\-]", "", v).strip()
        if not value:
            raise ValueError("מספר ח.פ/ע.מ/ת.ז לא תקין")
        return value

    @field_validator("rep_phone", "biz_phone")
    @classmethod
    def _phone(cls, v: str) -> str:
        return re.sub(r"[^0-9+\-() ]", "", v).strip()

    @field_validator("yard_size", "emp_count")
    @classmethod
    def _numeric_text(cls, v: str) -> str:
        return re.sub(r"[^0-9.]", "", v).strip()

    @field_validator("sector")
    @classmethod
    def _sector(cls, v: str) -> str:
        if v and v not in SECTOR_OPTIONS:
            raise ValueError("מרכיב התאמה לא מוכר")
        return v

    @field_validator("kitchen", "yard", "decl_given", "decl_ret")
    @classmethod
    def _yes_no(cls, v: str) -> str:
        if v and v not in ("yes", "no"):
            raise ValueError("ערך חייב להיות כן או לא")
        return v

    @field_validator("wet")
    @classmethod
    def _wet(cls, v: str) -> str:
        if v and v not in WET_OPTIONS:
            raise ValueError("גורם מפנה לא מוכר")
        return v

    @field_validator("cardboard")
    @classmethod
    def _cardboard(cls, v: str) -> str:
        if v and v not in CARDBOARD_OPTIONS:
            raise ValueError("גורם מפנה קרטון לא מוכר")
        return v

    @field_validator("image_ids", "doc_ids")
    @classmethod
    def _ids(cls, v: list[str]) -> list[str]:
        for item in v:
            if not OBJECT_ID_RE.match(item):
                raise ValueError("מזהה קובץ לא תקין")
        return v

    @field_validator("signature_id")
    @classmethod
    def _sig(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and not OBJECT_ID_RE.match(v):
            raise ValueError("מזהה חתימה לא תקין")
        return v

    @field_validator("biz_name", "rep_name", "role", "address", "biz_type", "biz_type_std", "notes")
    @classmethod
    def _text(cls, v: str) -> str:
        return clean_text(v, 4000)


class SurveyOut(BaseModel):
    id: str
    city: str
    biz_name: str
    biznum: str
    rep_name: str = ""
    role: str = ""
    rep_phone: str = ""
    biz_phone: str = ""
    address: str = ""
    biz_type: str = ""
    biz_type_std: str = ""
    sector: str = ""
    kitchen: str = ""
    yard: str = ""
    yard_size: str = ""
    containers: list[dict[str, Any]] = []
    wet: str = ""
    cardboard: str = ""
    decl_given: str = ""
    decl_ret: str = ""
    emp_count: str = ""
    notes: str = ""
    images: list[FileRef] = []
    docs: list[FileRef] = []
    signature: Optional[FileRef] = None
    owner_email: str = ""
    owner_name: str = ""
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    updated_by: str = ""
    can_edit: bool = False


# =========================================================================
#  Settings
# =========================================================================
class AppSettingsUpdate(StrictModel):
    daily_email_enabled: Optional[bool] = None
    daily_email_time: Optional[str] = None
    daily_email_recipient: Optional[EmailStr] = None
    daily_email_scope: Optional[Literal["all", "last24h"]] = None

    @field_validator("daily_email_time")
    @classmethod
    def _time(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        if not TIME_RE.match(v):
            raise ValueError("שעה חייבת להיות בפורמט HH:MM")
        return v


class AppSettingsOut(BaseModel):
    daily_email_enabled: bool = False
    daily_email_time: str = "08:00"
    daily_email_recipient: str = ""
    daily_email_scope: str = "all"
    timezone: str = "Asia/Jerusalem"
    last_sent_at: Optional[datetime] = None
    last_send_status: str = ""
    smtp_configured: bool = False
