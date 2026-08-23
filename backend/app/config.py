"""Application configuration.

Every secret is required at boot: the app refuses to start with placeholder or
missing values so a misconfigured deployment fails loudly instead of running
with a guessable key (OWASP A05 - Security Misconfiguration).
"""
from __future__ import annotations

import sys
from functools import lru_cache
from typing import List

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PLACEHOLDERS = {
    "", "changeme", "change-me", "CHANGE_ME", "replace-me",
    "your-secret-here", "secret", "password",
}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", case_sensitive=False)

    # ── Core ────────────────────────────────────────────────────────────────
    app_name: str = "Municipal Surveys"
    environment: str = Field(default="production")
    log_level: str = Field(default="INFO")

    # Public origin the browser uses, e.g. https://surveys.example.com
    public_origin: str = Field(default="http://localhost:8080")

    # ── Mongo ───────────────────────────────────────────────────────────────
    mongo_host: str = Field(default="mongo")
    mongo_port: int = Field(default=27017)
    mongo_db: str = Field(default="surveys")
    mongo_app_user: str = Field(default="surveys_app")
    mongo_app_password: str = Field(default="")

    # ── Auth ────────────────────────────────────────────────────────────────
    admin_email: str = Field(default="mosseriy1@gmail.com")
    session_secret: str = Field(default="")
    session_ttl_hours: int = Field(default=12)
    # Optional: seed the owner password instead of reading it from the log.
    admin_initial_password: str = Field(default="")
    password_min_length: int = Field(default=10)
    reset_token_ttl_minutes: int = Field(default=60)
    max_login_attempts: int = Field(default=6)
    lockout_minutes: int = Field(default=15)

    # ── Mail ────────────────────────────────────────────────────────────────
    smtp_host: str = Field(default="smtp.gmail.com")
    smtp_port: int = Field(default=587)
    smtp_user: str = Field(default="")
    smtp_password: str = Field(default="")
    mail_from: str = Field(default="")
    report_recipient: str = Field(default="mosseriy1@gmail.com")
    timezone: str = Field(default="Asia/Jerusalem")

    # ── Uploads ─────────────────────────────────────────────────────────────
    max_upload_mb: int = Field(default=15)
    max_files_per_survey: int = Field(default=40)

    # ── Rate limits (requests / window seconds) ─────────────────────────────
    # Per source address. Generous, because a whole crew can share one office
    # NAT address and would otherwise lock each other out.
    rate_login: str = Field(default="40/300")
    # Per account. This is the bucket that actually stops password guessing,
    # backed up by the lockout after max_login_attempts failures.
    rate_login_account: str = Field(default="12/300")
    # The first-login exchange is already authenticated by the change token.
    rate_first_login: str = Field(default="20/300")
    rate_reset: str = Field(default="5/900")
    rate_api: str = Field(default="300/60")
    rate_export: str = Field(default="10/300")
    rate_upload: str = Field(default="120/300")

    # ── Proxy / Cloudflare ──────────────────────────────────────────────────
    # Only trust client-IP headers when the request arrives through the tunnel.
    trust_cf_headers: bool = Field(default=True)
    cf_access_audience: str = Field(default="")  # optional Cloudflare Access AUD
    cf_access_team_domain: str = Field(default="")

    @property
    def mongo_uri(self) -> str:
        from urllib.parse import quote_plus
        user = quote_plus(self.mongo_app_user)
        pwd = quote_plus(self.mongo_app_password)
        return (
            f"mongodb://{user}:{pwd}@{self.mongo_host}:{self.mongo_port}"
            f"/{self.mongo_db}?authSource={self.mongo_db}&retryWrites=true&w=majority"
        )

    @property
    def cookie_secure(self) -> bool:
        return self.public_origin.startswith("https://")

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024

    @property
    def allowed_origins(self) -> List[str]:
        return [self.public_origin.rstrip("/")]

    @field_validator("admin_email", "report_recipient", mode="before")
    @classmethod
    def _lower(cls, v: str) -> str:
        return (v or "").strip().lower()

    def validate_secrets(self) -> None:
        problems: List[str] = []
        if self.session_secret.strip() in PLACEHOLDERS or len(self.session_secret) < 32:
            problems.append("SESSION_SECRET must be set to a random string of >= 32 chars")
        if self.mongo_app_password.strip() in PLACEHOLDERS:
            problems.append("MONGO_APP_PASSWORD must be set")
        if not self.admin_email:
            problems.append("ADMIN_EMAIL must be set")
        if problems:
            for p in problems:
                print(f"[config] FATAL: {p}", file=sys.stderr)
            raise SystemExit(1)


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
