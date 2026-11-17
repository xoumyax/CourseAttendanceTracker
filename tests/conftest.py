"""
Test setup. Environment variables are set BEFORE any `app` import because the
settings object and the database engine are created at import time.
"""
import os
import tempfile

_TMP = tempfile.mkdtemp(prefix="attendance-tests-")
os.environ.update(
    SECRET_KEY="test-secret-key-not-for-production-0123456789",
    DATABASE_URL=f"sqlite:///{_TMP}/test.db",
    COURSE_NAME="Test Course 101",
    TIMEZONE="America/Chicago",
    CHECKIN_WINDOWS="",
    CHECKIN_CODE_TTL_SECONDS="300",
    TEST_CODE_TTL_HOURS="24",
    GRADE_SCALE="85:10,75:8,50:5,0:0",
    CORS_ORIGINS="",
    RATE_LIMIT_FREE_FAILURES="2",
    RATE_LIMIT_UNKNOWN_PER_IP="20",
    RATE_LIMIT_WINDOW_SECONDS="60",
)

import pytest  # noqa: E402
from datetime import datetime, timezone  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import timeutil  # noqa: E402
from app.config import settings  # noqa: E402
from app.database import Base, SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402
from app import auth  # noqa: E402

auth.pwd_context.update(bcrypt__rounds=4)  # fast hashing for tests only


@pytest.fixture(autouse=True)
def fresh_db():
    """Every test starts with empty tables and an empty rate limiter."""
    from app import ratelimit
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    ratelimit.limiter.reset()
    yield


@pytest.fixture
def db():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def cfg(monkeypatch):
    """Change settings for one test: cfg(CHECKIN_WINDOWS='MON 08:00-09:00')."""
    def apply(**values):
        for key, value in values.items():
            monkeypatch.setattr(settings, key, value)
    return apply


@pytest.fixture
def freeze(monkeypatch):
    """Freeze the clock at a UTC datetime; returns a function to move it."""
    state = {"now": datetime(2026, 3, 4, 14, 30, tzinfo=timezone.utc)}  # a Wednesday

    def set_now(value=None, **kwargs):
        if value is not None:
            state["now"] = value
        return state["now"]

    monkeypatch.setattr(timeutil, "utcnow", lambda: state["now"])
    return set_now


@pytest.fixture
def rl_clock(monkeypatch):
    """Control the rate limiter's clock; call rl_clock(seconds) to move it forward."""
    from app import ratelimit
    now = [1000.0]
    monkeypatch.setattr(ratelimit.limiter, "_clock", lambda: now[0])

    def advance(seconds=0.0):
        now[0] += seconds
        return now[0]
    return advance
