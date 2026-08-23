"""Domain constants shared by validation, the UI catalogue endpoint and Excel export."""
from __future__ import annotations

CONTAINER_VOLUMES: dict[str, list[str]] = {
    "עגלה": ["240", "360", "770", "1100"],
    "דחסן": ["8", "10", "12", "14", "20", "26"],
    "מכולה": ["6", "8", "12"],
}

NO_VOLUME_TYPES: set[str] = {"טמון קרקע", "מונח קרקע"}

CONTAINER_ORDER: list[str] = ["עגלה", "דחסן", "מכולה", "טמון קרקע", "מונח קרקע"]

FREQ_OPTIONS: list[str] = [
    "6 פעמים בשבוע",
    "5 פעמים בשבוע",
    "4 פעמים בשבוע",
    "3 פעמים בשבוע",
    "פעמיים בשבוע",
    "פעם בשבוע",
    "פעם בשבועיים",
    "פעם בחודש",
    "אחר",
]

WEEKS_PER_MONTH = 4.33

FREQ_TO_MONTHLY: dict[str, float] = {
    "6 פעמים בשבוע": 6 * WEEKS_PER_MONTH,
    "5 פעמים בשבוע": 5 * WEEKS_PER_MONTH,
    "4 פעמים בשבוע": 4 * WEEKS_PER_MONTH,
    "3 פעמים בשבוע": 3 * WEEKS_PER_MONTH,
    "פעמיים בשבוע": 2 * WEEKS_PER_MONTH,
    "פעם בשבוע": 1 * WEEKS_PER_MONTH,
    "פעם בשבועיים": 0.5 * WEEKS_PER_MONTH,
    "פעם בחודש": 1.0,
}

OWNERSHIP_OPTIONS = ["רשות", "פרטי"]
USAGE_OPTIONS = ["משותף", "אישי"]
SECTOR_OPTIONS = ["מסחר", "תעשייה"]
YES_NO = ["yes", "no"]
WET_OPTIONS = ["רשות", "עצמי"]
CARDBOARD_OPTIONS = ["רשות", "עצמי", "לא מפנה"]

BUSINESS_TYPES: list[str] = [
    "מסעדה", "מכולת", "גלידריה", "פיצוצייה", "ירקן", "חנות פרחים", "מספרה",
    "בית מרקחת", "בית קפה", "חנות בגדים", "חנות נעליים", "סופרמרקט", "פיצרייה",
    "שווארמה", "פלאפל", "אוכל מהיר", "מאפייה", "בית מאפה", "מוסך", "פנצ'רייה",
    "קניון", "משרד", "בניין משרדים", "חנות צעצועים", "כלי בית", "טמבוריה",
    "חומרי בניין", "חנות רהיטים", "אולם תצוגת רכבים", "חנות אלקטרוניקה",
    "חנות טלפונים", "תחנת דלק", "מכבסה", "חנות צילום", "אטליז", "מעדניה",
    "חנות טבע", "תבלינים ופיצוחים", "מכירת אלכוהול", "תשמישי קדושה",
    "חנות תכשיטים", "תיווך", "חנות תאורה", "בית מלון", "בית גדרה", "בית אבות",
    "בית חולים", "אופטיקה", "חנות אופניים", 'מרלו"ג', "אביזרי רכב", "אחר",
]

# Uploads - allowlist by extension and sniffed content type (OWASP A08)
ALLOWED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp", "image/heic", "image/heif"}
ALLOWED_DOC_TYPES = {
    "application/pdf",
    "application/msword",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.ms-excel",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "text/plain",
    "text/csv",
}
ALLOWED_DOC_EXTENSIONS = {".pdf", ".doc", ".docx", ".xls", ".xlsx", ".txt", ".csv"}
ALLOWED_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif"}


def freq_to_monthly(freq: str | None) -> float | str:
    if not freq:
        return ""
    value = FREQ_TO_MONTHLY.get(freq)
    return round(value, 4) if value is not None else ""
