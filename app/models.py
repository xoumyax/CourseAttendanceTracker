"""
SQLAlchemy database models.

All datetimes are stored as UTC (see timeutil.UTCDateTime). The column named
`uin` holds the student ID; the name is kept to avoid a schema migration.
"""
from sqlalchemy import Column, Integer, String, Date, Boolean, ForeignKey, UniqueConstraint
from sqlalchemy.orm import relationship

from . import timeutil
from .database import Base
from .timeutil import UTCDateTime


def _now():
    # Looked up at call time so tests can freeze timeutil.utcnow.
    return timeutil.utcnow()


class Student(Base):
    """A student from the imported roster."""
    __tablename__ = "students"

    id = Column(Integer, primary_key=True, index=True)
    uin = Column(String, unique=True, index=True, nullable=False)  # the student ID
    name = Column(String, nullable=False)
    hashed_password = Column(String, nullable=False, default="")
    is_registered = Column(Boolean, default=False)  # True after the student sets a password
    # Keyed hash of the outstanding one-time enrollment code (None when there is none).
    enrollment_code_hash = Column(String, nullable=True)
    enrollment_code_issued_at = Column(UTCDateTime, nullable=True)
    created_at = Column(UTCDateTime, default=_now)

    attendances = relationship("Attendance", back_populates="student", cascade="all, delete-orphan")


class Session(Base):
    """A class meeting. `date` is the calendar date in the course TIMEZONE."""
    __tablename__ = "sessions"

    id = Column(Integer, primary_key=True, index=True)
    date = Column(Date, nullable=False, index=True)
    is_test_session = Column(Boolean, default=False)
    created_at = Column(UTCDateTime, default=_now)

    tokens = relationship("SessionToken", back_populates="session", cascade="all, delete-orphan")
    attendances = relationship("Attendance", back_populates="session", cascade="all, delete-orphan")


class SessionToken(Base):
    """A check-in code shown to the class for one session."""
    __tablename__ = "session_tokens"

    id = Column(Integer, primary_key=True, index=True)
    session_id = Column(Integer, ForeignKey("sessions.id"), nullable=False, index=True)
    token = Column(String(6), nullable=False, index=True)
    expires_at = Column(UTCDateTime, nullable=False)
    is_active = Column(Boolean, default=True)
    created_at = Column(UTCDateTime, default=_now)

    session = relationship("Session", back_populates="tokens")


class Attendance(Base):
    """One student checked in to one session."""
    __tablename__ = "attendances"

    id = Column(Integer, primary_key=True, index=True)
    student_id = Column(Integer, ForeignKey("students.id"), nullable=False, index=True)
    session_id = Column(Integer, ForeignKey("sessions.id"), nullable=False, index=True)
    marked_at = Column(UTCDateTime, default=_now)

    student = relationship("Student", back_populates="attendances")
    session = relationship("Session", back_populates="attendances")

    __table_args__ = (
        UniqueConstraint("student_id", "session_id", name="unique_student_session"),
    )


class AdminSettings(Base):
    """Runtime switch that lets an admin open check-in outside the windows (for testing)."""
    __tablename__ = "admin_settings"

    id = Column(Integer, primary_key=True)
    disable_time_restrictions = Column(Boolean, default=False)
    updated_at = Column(UTCDateTime, default=_now, onupdate=_now)


class Admin(Base):
    """Admin account. Managed with `python -m app.cli admin`."""
    __tablename__ = "admins"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True, index=True, nullable=False)
    hashed_password = Column(String, nullable=False)
    created_at = Column(UTCDateTime, default=_now)
