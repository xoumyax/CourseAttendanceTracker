import io
from datetime import date

import openpyxl

from app import models
from app.main import app

from .helpers import admin_headers, make_student, student_headers

PW = "hunter2-hunter2"


def test_public_config_endpoint(client, cfg):
    cfg(CHECKIN_WINDOWS="MON,WED,FRI 08:00-09:00", COURSE_NAME="Test Course 101")
    data = client.get("/api/config").json()
    assert data["course_name"] == "Test Course 101"
    assert data["timezone"] == "America/Chicago"
    assert data["checkin_windows"][0]["days"] == ["MON", "WED", "FRI"]
    assert data["checkin_windows"][0]["start"] == "08:00"
    assert "MON,WED,FRI 08:00-09:00" in data["checkin_windows_text"]
    assert data["always_open"] is False
    assert data["max_points"] == 10 and data["grade_scale"][0] == {"min_percent": 85.0, "points": 10}
    assert data["code_ttl_seconds"] == 300 and data["test_code_ttl_hours"] == 24
    assert "secret" not in str(data).lower()


def test_public_config_open_now_uses_server_clock(client, cfg, freeze):
    from datetime import datetime, timezone
    cfg(CHECKIN_WINDOWS="WED 08:00-09:00")
    assert client.get("/api/config").json()["open_now"] is True  # 08:30 Chicago
    freeze(datetime(2026, 3, 4, 20, 0, tzinfo=timezone.utc))
    assert client.get("/api/config").json()["open_now"] is False
    cfg(CHECKIN_WINDOWS="")
    assert client.get("/api/config").json()["always_open"] is True


def test_no_cors_by_default(client):
    r = client.options("/api/config", headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "GET"})
    assert "access-control-allow-origin" not in r.headers
    r = client.get("/api/config", headers={"Origin": "https://evil.example"})
    assert "access-control-allow-origin" not in r.headers


def test_cors_origins_setting_builds_middleware(tmp_path):
    import os, subprocess, sys
    from pathlib import Path
    code = ("from fastapi.testclient import TestClient\n"
            "from app.main import app\n"
            "c = TestClient(app)\n"
            "for o in ('https://ok.example', 'https://evil.example'):\n"
            "    r = c.get('/api/config', headers={'Origin': o})\n"
            "    print(o, r.headers.get('access-control-allow-origin'))\n")
    env = {**os.environ, "CORS_ORIGINS": "https://ok.example", "PYTHONPATH": str(Path(__file__).resolve().parent.parent),
           "DATABASE_URL": f"sqlite:///{tmp_path}/c.db"}
    out = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, cwd=tmp_path)
    assert "https://ok.example https://ok.example" in out.stdout, out.stderr
    assert "https://evil.example None" in out.stdout


def test_pages_render(client):
    for path in ["/", "/student/login", "/student/register", "/student/reset-password",
                 "/student/guide", "/attendance", "/admin/login", "/admin/dashboard"]:
        r = client.get(path)
        assert r.status_code == 200, path
        assert "text/html" in r.headers["content-type"]


def test_admin_can_create_sessions(client, db):
    headers = admin_headers(client, db)
    r = client.post("/api/admin/sessions", json={"date": "2026-03-09"}, headers=headers)
    assert r.status_code == 200 and r.json()["date"] == "2026-03-09"
    assert client.post("/api/admin/sessions", json={"date": "2026-03-09"}, headers=headers).status_code == 400
    assert client.post("/api/admin/sessions", json={"date": "2026-03-09", "is_test_session": True}, headers=headers).status_code == 200
    assert client.post("/api/admin/sessions", json={"date": "not-a-date"}, headers=headers).status_code == 422
    assert len(client.get("/api/admin/sessions", headers=headers).json()) == 2


def test_grades_and_my_records_follow_the_grade_scale(client, db, cfg):
    make_student(db, password=PW)
    make_student(db, "900000002", "Test Student 02")
    sessions = [models.Session(date=date(2026, 3, d), is_test_session=False) for d in (2, 4, 6, 9)]
    test = models.Session(date=date(2026, 3, 5), is_test_session=True)
    db.add_all(sessions + [test])
    db.commit()
    s1 = db.query(models.Student).filter_by(uin="900000001").one()
    for s in sessions[:3] + [test]:
        db.add(models.Attendance(student_id=s1.id, session_id=s.id))
    db.commit()

    rows = client.get("/api/admin/students/grades", headers=admin_headers(client, db)).json()
    by_id = {r["student_id"]: r for r in rows}
    assert by_id["900000001"]["attended_sessions"] == 3 and by_id["900000001"]["total_sessions"] == 4
    assert by_id["900000001"]["attendance_percentage"] == 75.0 and by_id["900000001"]["grade_points"] == 8
    assert by_id["900000001"]["is_registered"] is True and by_id["900000002"]["is_registered"] is False
    assert by_id["900000002"]["grade_points"] == 0

    mine = client.get("/api/student/attendance/my-records", headers=student_headers(client)).json()
    assert mine["statistics"]["grade_points"] == 8 and mine["statistics"]["attended_test_sessions"] == 1
    assert mine["student"] == {"student_id": "900000001", "name": "Test Student 01"}

    cfg(GRADE_SCALE="70:4,0:1")
    rows = client.get("/api/admin/students/grades", headers=admin_headers(client, db)).json()
    assert {r["student_id"]: r["grade_points"] for r in rows} == {"900000001": 4, "900000002": 1}


def test_excel_export_is_a_download_with_the_right_content(client, db):
    make_student(db, "900000001", "Test Student 01", password=PW)
    make_student(db, "900000002", "=HYPERLINK(\"x\")")
    session = models.Session(date=date(2026, 3, 4), is_test_session=False)
    db.add(session)
    db.commit()
    db.add(models.Attendance(student_id=1, session_id=session.id))
    db.commit()

    assert client.get("/api/admin/export/excel").status_code == 401
    r = client.get("/api/admin/export/excel", headers=admin_headers(client, db))
    assert r.status_code == 200
    assert "spreadsheetml" in r.headers["content-type"]
    assert "attachment" in r.headers["content-disposition"]
    assert "/" not in r.headers["content-disposition"].split("filename=")[1].strip('"')

    ws = openpyxl.load_workbook(io.BytesIO(r.content)).active
    rows = list(ws.iter_rows(values_only=True))
    assert rows[0][0] == "Student ID" and rows[0][-1] == "Grade Points"
    body = {row[0]: row for row in rows[1:]}
    assert body["900000001"][1] == "Test Student 01" and body["900000001"][-1] == 10
    assert body["900000002"][1].startswith("'=")  # formula neutralised


def test_front_end_has_no_hard_coded_course_details():
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent
    banned = ["UIN", "8 AM", "9 AM", "8:00", "test123", "Canvas", "999999", "/10"]
    for path in list((root / "templates").glob("*.html")) + list((root / "static" / "js").glob("*.js")):
        text = path.read_text()
        for word in banned:
            assert word not in text, f"{path.name} contains {word!r}"


def test_future_sessions_do_not_count_against_students(client, db, freeze, cfg):
    cfg(ACCESS_TOKEN_EXPIRE_HOURS=24 * 365 * 30)  # JWT expiry uses real time; the frozen clock is in the past
    make_student(db, password=PW)
    past = models.Session(date=date(2026, 3, 2), is_test_session=False)
    today = models.Session(date=date(2026, 3, 4), is_test_session=False)
    future = [models.Session(date=date(2026, 3, d), is_test_session=False) for d in (11, 18, 25)]
    db.add_all([past, today] + future)
    db.commit()
    db.add(models.Attendance(student_id=1, session_id=past.id))
    db.add(models.Attendance(student_id=1, session_id=today.id))
    db.commit()
    headers = admin_headers(client, db)
    row = client.get("/api/admin/students/grades", headers=headers).json()[0]
    assert (row["attended_sessions"], row["total_sessions"]) == (2, 2)
    assert row["attendance_percentage"] == 100.0 and row["grade_points"] == 10
    # the day after tomorrow's session becomes "held" once its date arrives
    from datetime import datetime, timezone
    freeze(datetime(2026, 3, 11, 15, 0, tzinfo=timezone.utc))
    row = client.get("/api/admin/students/grades", headers=headers).json()[0]
    assert (row["attended_sessions"], row["total_sessions"]) == (2, 3)

    ws = openpyxl.load_workbook(io.BytesIO(client.get("/api/admin/export/excel", headers=headers).content)).active
    rows = list(ws.iter_rows(values_only=True))
    assert "to date" in rows[0][7] and rows[1][3] == 3


def test_every_admin_route_requires_a_login(client):
    open_routes = {("POST", "/api/admin/login"), ("GET", "/api/admin/settings")}
    checked = 0
    for route in app.routes:
        path = getattr(route, "path", "")
        if not path.startswith("/api/admin"):
            continue
        for method in route.methods - {"HEAD", "OPTIONS"}:
            if (method, path) in open_routes:
                continue
            url = path.replace("{session_id}", "1")
            r = client.request(method, url, json={})
            assert r.status_code == 401, (method, path, r.status_code)
            checked += 1
    assert checked >= 14


def test_settings_get_is_read_only(client, db):
    assert client.get("/api/admin/settings").json()["disable_time_restrictions"] is False
    assert db.query(models.AdminSettings).count() == 0


def test_dashboard_codes_dry_run_skip_and_replace(client, db):
    headers = admin_headers(client, db)
    make_student(db, "900000001", "Test Student 01")
    make_student(db, "900000002", "Test Student 02")
    post = lambda **body: client.post("/api/admin/codes", json=body, headers=headers).json()

    first = post(all_unregistered=True)
    assert len(first["codes"]) == 2
    preview = post(all_unregistered=True, dry_run=True)
    assert preview["dry_run"] and preview["would_issue"] == 0 and preview["skipped_outstanding"] == 2 and "codes" not in preview
    again = post(all_unregistered=True)
    assert again["codes"] == [] and again["skipped_outstanding"] == 2
    # the handed-out codes still work
    code1 = next(c["code"] for c in first["codes"] if c["student_id"] == "900000001")
    assert client.post("/api/student/register", json={"student_id": "900000001", "code": code1, "password": PW}).status_code == 200

    preview = post(student_ids=["900000002"], replace_outstanding=True, dry_run=True)
    assert preview["would_issue"] == 1 and preview["would_void"] == 1
    replaced = post(student_ids=["900000002"], replace_outstanding=True)
    old = next(c["code"] for c in first["codes"] if c["student_id"] == "900000002")
    assert client.post("/api/student/register", json={"student_id": "900000002", "code": old, "password": PW}).status_code == 400
    assert client.post("/api/student/register", json={"student_id": "900000002", "code": replaced["codes"][0]["code"], "password": PW}).status_code == 200


def test_secret_key_must_be_long_enough(tmp_path):
    from .test_config import _run_import
    short = _run_import(tmp_path, SECRET_KEY="a-short-key-1234")
    assert short.returncode != 0 and "32 characters" in short.stderr and "token_urlsafe" in short.stderr
    assert _run_import(tmp_path, SECRET_KEY="x" * 32).returncode == 0
