"""
Configuration, read from environment variables or a .env file.

Every course-specific value lives here; see .env.example and the README.
"""
from typing import List, Tuple
import ipaddress
import os
from zoneinfo import ZoneInfo

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from . import timeutil

SECRET_KEY_PLACEHOLDER = "change-me"
MIN_SECRET_LENGTH = 32
ALGORITHM = "HS256"  # fixed, not configurable


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    # Security. Required: startup fails if missing or a placeholder.
    SECRET_KEY: str = ""
    ACCESS_TOKEN_EXPIRE_HOURS: int = 8

    # Database (any SQLAlchemy URL)
    DATABASE_URL: str = "sqlite:///./attendance.db"

    # Server
    HOST: str = "0.0.0.0"
    PORT: int = 8000

    # Course
    COURSE_NAME: str = "My Course"
    TIMEZONE: str = "UTC"
    # Raw strings; parsed by the properties below (pydantic would JSON-decode list types).
    CHECKIN_WINDOWS: str = ""  # empty = always open
    CHECKIN_CODE_TTL_SECONDS: int = 300
    TEST_CODE_TTL_HOURS: int = 24
    GRADE_SCALE: str = "85:10,75:8,50:5,0:0"
    CORS_ORIGINS: str = ""  # comma-separated; empty = same-origin only

    # Rate limiting (in memory, resets on restart). See the README.
    RATE_LIMIT_FREE_FAILURES: int = 2  # wrong attempts per (account, IP) before delays start
    RATE_LIMIT_BACKOFF_MAX_SECONDS: int = 300  # delays double from 1 s up to this
    RATE_LIMIT_UNKNOWN_PER_IP: int = 60  # failed attempts on unknown accounts per IP per window
    RATE_LIMIT_WINDOW_SECONDS: int = 60
    # Reverse proxies whose X-Forwarded-For header may be believed (IPs or CIDRs,
    # comma-separated). Empty = use the socket peer address.
    TRUSTED_PROXIES: str = ""

    @field_validator("SECRET_KEY")
    @classmethod
    def _secret_key_must_be_set(cls, value: str) -> str:
        if (not value.strip() or value.strip().startswith(SECRET_KEY_PLACEHOLDER)
                or len(value) < MIN_SECRET_LENGTH):
            raise ValueError(
                f"SECRET_KEY is unset, still the placeholder, or shorter than {MIN_SECRET_LENGTH} "
                "characters. Generate one with: "
                "python -c \"import secrets; print(secrets.token_urlsafe(48))\" "
                "and put it in .env (SECRET_KEY=...) or the environment."
            )
        return value

    @field_validator("TRUSTED_PROXIES")
    @classmethod
    def _proxies_must_parse(cls, value: str) -> str:
        for item in value.split(","):
            if item.strip():
                try:
                    ipaddress.ip_network(item.strip(), strict=False)
                except ValueError:
                    raise ValueError(f"TRUSTED_PROXIES entry '{item.strip()}' is not an IP address or CIDR range")
        return value

    @field_validator("TIMEZONE")
    @classmethod
    def _timezone_must_exist(cls, value: str) -> str:
        timeutil.get_timezone(value)
        return value

    @field_validator("CHECKIN_WINDOWS")
    @classmethod
    def _windows_must_parse(cls, value: str) -> str:
        timeutil.parse_windows(value)
        return value

    @field_validator("GRADE_SCALE")
    @classmethod
    def _grade_scale_must_parse(cls, value: str) -> str:
        timeutil.parse_grade_scale(value)
        return value

    @field_validator("CHECKIN_CODE_TTL_SECONDS", "TEST_CODE_TTL_HOURS",
                     "RATE_LIMIT_BACKOFF_MAX_SECONDS", "RATE_LIMIT_UNKNOWN_PER_IP",
                     "RATE_LIMIT_WINDOW_SECONDS")
    @classmethod
    def _must_be_positive(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("must be a positive number")
        return value

    @property
    def trusted_proxies(self):
        return [ipaddress.ip_network(i.strip(), strict=False)
                for i in self.TRUSTED_PROXIES.split(",") if i.strip()]

    @property
    def tz(self) -> ZoneInfo:
        return timeutil.get_timezone(self.TIMEZONE)

    @property
    def windows(self) -> List[timeutil.Window]:
        return timeutil.parse_windows(self.CHECKIN_WINDOWS)

    @property
    def grade_scale(self) -> List[Tuple[float, int]]:
        return timeutil.parse_grade_scale(self.GRADE_SCALE)

    @property
    def cors_origins(self) -> List[str]:
        return [o.strip() for o in self.CORS_ORIGINS.split(",") if o.strip()]

    model_config = SettingsConfigDict(
        env_file=os.environ.get("ENV_FILE", ".env"),
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
        validate_default=True,
    )


try:
    settings = Settings()
except Exception as exc:  # surface a clear startup message
    raise SystemExit(f"Configuration error: {exc}") from None
