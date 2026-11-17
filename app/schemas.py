"""
Pydantic schemas for request/response validation.
"""
from datetime import date, datetime
from typing import List, Optional

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

MIN_PASSWORD_LENGTH = 8

# Identifiers are stripped once here, so the rate limiter and the database lookup see the same value.
StudentId = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=64)]
Username = Annotated[str, StringConstraints(strip_whitespace=True, max_length=128)]


def _check_password(value: str) -> str:
    if len(value) < MIN_PASSWORD_LENGTH:
        raise ValueError(f"Password must be at least {MIN_PASSWORD_LENGTH} characters")
    if len(value.encode("utf-8")) > 72:  # bcrypt ignores or rejects anything longer
        raise ValueError("Password must be at most 72 bytes")
    return value


# ============================================================================
# Authentication
# ============================================================================

class StudentRegisterRequest(BaseModel):
    student_id: StudentId
    code: str = Field(..., min_length=1, max_length=64)
    password: str

    @field_validator("password")
    @classmethod
    def _pw(cls, v):
        return _check_password(v)


class StudentLoginRequest(BaseModel):
    student_id: StudentId
    password: str = Field(..., max_length=256)


class StudentResetPasswordRequest(BaseModel):
    student_id: StudentId
    code: str = Field(..., min_length=1, max_length=64)
    new_password: str

    @field_validator("new_password")
    @classmethod
    def _pw(cls, v):
        return _check_password(v)


class AdminLoginRequest(BaseModel):
    username: Username
    password: str = Field(..., max_length=256)


# ============================================================================
# Sessions and check-in codes
# ============================================================================

class SessionCreate(BaseModel):
    date: date
    is_test_session: bool = False


class SessionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    date: date
    is_test_session: bool
    created_at: datetime


class TokenGenerate(BaseModel):
    session_id: int


# ============================================================================
# Attendance
# ============================================================================

class AttendanceMarkRequest(BaseModel):
    session_id: int
    token: str = Field(..., min_length=6, max_length=6)  # the 6-digit check-in code


class AttendanceResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    student_id: int
    session_id: int
    marked_at: datetime


class AttendanceRecord(BaseModel):
    student_id: str
    student_name: str
    session_date: date
    marked_at: datetime


# ============================================================================
# Statistics
# ============================================================================

class StudentStats(BaseModel):
    student_id: str
    name: str
    is_registered: bool
    total_sessions: int
    attended_sessions: int
    attendance_percentage: float
    grade_points: int


class DashboardStats(BaseModel):
    total_students: int
    total_registered_students: int
    total_sessions: int
    total_attendances: int
    today_session: Optional[SessionResponse]
    recent_attendances: List[AttendanceRecord]


# ============================================================================
# Settings and enrollment codes
# ============================================================================

class SettingsUpdate(BaseModel):
    disable_time_restrictions: bool


class SettingsResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    disable_time_restrictions: bool
    updated_at: Optional[datetime] = None


class CodesRequest(BaseModel):
    student_ids: List[str] = []
    all_unregistered: bool = False
    # Also replace codes that were already issued and not yet used (voids the old ones).
    replace_outstanding: bool = False
    # Report what would happen without changing anything.
    dry_run: bool = False
