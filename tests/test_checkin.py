from datetime import date, datetime, timedelta, timezone

import pytest

from app import models

from .helpers import admin_headers, make_student, student_headers

PW = "hunter2-hunter2"
# freeze() default: Wednesday 2026-03-04 14:30 UTC = 08:30 in America/Chicago


@pytest.fixture
def setup(client, db, freeze, cfg):
    # The frozen clock is in the past, and JWT expiry is checked against real time.
    cfg(ACCESS_TOKEN_EXPIRE_HOURS=24 * 365 * 30)
    make_student(db, password=PW)
    admin = admin_headers(client, db)
    session = models.Session(date=date(2026, 3, 4), is_test_session=False)
    db.add(session)
    db.commit()
    student = student_headers(client)

    def new_code(session_id=None):
        r = client.post("/api/admin/tokens/generate", json={"session_id": session_id or session.id}, headers=admin)
        assert r.status_code == 200
        return r.json()["token"]

    def check_in(code, session_id=None):
        return client.post("/api/student/attendance/mark", headers=student,
                           json={"session_id": session_id or session.id, "token": code})

    return type("S", (), dict(session=session, new_code=staticmethod(new_code),
                              check_in=staticmethod(check_in), admin=admin, student=student))


def test_valid_checkin(setup, db):
    r = setup.check_in(setup.new_code())
    assert r.status_code == 200
    assert db.query(models.Attendance).count() == 1
    assert r.json()["attendance"]["marked_at"].endswith("+00:00") or r.json()["attendance"]["marked_at"].endswith("Z")


def test_wrong_code(setup, db):
    code = setup.new_code()
    wrong = f"{(int(code) + 1) % 1000000:06d}"
    assert setup.check_in(wrong).status_code == 403
    assert db.query(models.Attendance).count() == 0


def test_expired_code(setup, freeze):
    code = setup.new_code()
    freeze(datetime(2026, 3, 4, 14, 30, tzinfo=timezone.utc) + timedelta(seconds=299))
    assert setup.check_in(code).status_code == 200  # still valid inside the TTL


def test_expired_code_rejected(setup, freeze, db):
    code = setup.new_code()
    freeze(datetime(2026, 3, 4, 14, 30, tzinfo=timezone.utc) + timedelta(seconds=301))
    r = setup.check_in(code)
    assert r.status_code == 403 and db.query(models.Attendance).count() == 0


def test_code_ttl_follows_config(setup, freeze, cfg):
    cfg(CHECKIN_CODE_TTL_SECONDS=30)
    code = setup.new_code()
    freeze(datetime(2026, 3, 4, 14, 30, 31, tzinfo=timezone.utc))
    assert setup.check_in(code).status_code == 403


def test_duplicate_checkin_refused(setup, db):
    code = setup.new_code()
    assert setup.check_in(code).status_code == 200
    again = setup.check_in(code)
    assert again.status_code == 400 and "already" in again.json()["detail"]
    assert db.query(models.Attendance).count() == 1


def test_code_for_another_session_does_not_work(setup, db):
    other = models.Session(date=date(2026, 3, 4), is_test_session=False)
    db.add(other)
    db.commit()
    code_other = setup.new_code(other.id)
    assert setup.check_in(code_other, setup.session.id).status_code == 403


def test_only_todays_sessions_in_course_timezone(setup, db, freeze):
    # 03:00 UTC on 2026-03-05 is still 21:00 on 2026-03-04 in Chicago
    freeze(datetime(2026, 3, 5, 3, 0, tzinfo=timezone.utc))
    assert setup.check_in(setup.new_code()).status_code == 200
    # 07:00 UTC on 2026-03-05 is 01:00 on 2026-03-05 in Chicago: session is now yesterday
    freeze(datetime(2026, 3, 5, 7, 0, tzinfo=timezone.utc))
    r = setup.check_in(setup.new_code())
    assert r.status_code in (400, 403)
    today = setup  # sessions/today follows the local date too
    assert client_sessions_today(today) == []


def client_sessions_today(s):
    from fastapi.testclient import TestClient
    from app.main import app
    return TestClient(app).get("/api/student/sessions/today", headers=s.student).json()["sessions"]


def test_outside_window_refused_then_allowed(setup, cfg, freeze):
    cfg(CHECKIN_WINDOWS="MON,WED,FRI 08:00-09:00")
    code = setup.new_code()
    freeze(datetime(2026, 3, 4, 16, 0, tzinfo=timezone.utc))  # 10:00 local
    r = setup.check_in(code)
    assert r.status_code == 403 and "closed" in r.json()["detail"] and "08:00-09:00" in r.json()["detail"]
    freeze(datetime(2026, 3, 4, 14, 45, tzinfo=timezone.utc))  # 08:45 local
    assert setup.check_in(setup.new_code()).status_code == 200


def test_wrong_weekday_is_outside_window(setup, cfg):
    cfg(CHECKIN_WINDOWS="MON,FRI 08:00-09:00")  # the frozen day is a Wednesday
    assert setup.check_in(setup.new_code()).status_code == 403


def test_test_session_and_admin_switch_bypass_window(setup, client, db, cfg, freeze):
    cfg(CHECKIN_WINDOWS="MON 08:00-09:00")
    test_session = models.Session(date=date(2026, 3, 4), is_test_session=True)
    db.add(test_session)
    db.commit()
    assert setup.check_in(setup.new_code(test_session.id), test_session.id).status_code == 200

    code = setup.new_code()
    assert setup.check_in(code).status_code == 403
    r = client.put("/api/admin/settings", json={"disable_time_restrictions": True}, headers=setup.admin)
    assert r.status_code == 200
    assert setup.check_in(code).status_code == 200


def test_wrong_codes_back_off_but_never_hard_lock(setup, db, rl_clock):
    good = setup.new_code()
    bad = f"{(int(good) + 1) % 1000000:06d}"
    assert setup.check_in(bad).status_code == 403  # two free typos
    assert setup.check_in(bad).status_code == 403
    assert setup.check_in(bad).status_code == 403  # third failure starts a 1 s delay
    blocked = setup.check_in(good)
    assert blocked.status_code == 429 and blocked.headers["Retry-After"] == "1"
    rl_clock(1.1)
    assert setup.check_in(bad).status_code == 403  # fourth failure: 2 s delay
    rl_clock(1.0)
    assert setup.check_in(good).status_code == 429
    rl_clock(1.1)
    assert setup.check_in(good).status_code == 200  # right code works once the delay passed
    assert db.query(models.Attendance).count() == 1


def test_unauthenticated_checkin_refused(client, setup):
    r = client.post("/api/student/attendance/mark", json={"session_id": setup.session.id, "token": "123456"})
    assert r.status_code == 401


def test_codes_come_from_secrets_not_random():
    import app.utils as utils
    import inspect
    source = inspect.getsource(utils)
    assert "import random" not in source and "import secrets" in source
    codes = {utils.generate_checkin_code() for _ in range(200)}
    assert all(len(c) == 6 and c.isdigit() for c in codes) and len(codes) > 150


def test_code_ttls_regular_and_test_sessions(setup, client, db):
    regular = client.post("/api/admin/tokens/generate", json={"session_id": setup.session.id}, headers=setup.admin).json()
    assert datetime.fromisoformat(regular["expires_at"]) == datetime(2026, 3, 4, 14, 35, tzinfo=timezone.utc)
    assert regular["expiry_info"] == "Valid for 5 minutes"

    test_session = models.Session(date=date(2026, 3, 4), is_test_session=True)
    db.add(test_session)
    db.commit()
    test = client.post("/api/admin/tokens/generate", json={"session_id": test_session.id}, headers=setup.admin).json()
    assert datetime.fromisoformat(test["expires_at"]) == datetime(2026, 3, 5, 14, 30, tzinfo=timezone.utc)
    assert test["expiry_info"] == "Valid for 24 hours"
