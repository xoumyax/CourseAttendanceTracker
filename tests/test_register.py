from app import models
from app.auth import verify_password

from .helpers import admin_headers, issue, make_student

PW = "hunter2-hunter2"


def register(client, sid, code, password=PW):
    return client.post("/api/student/register", json={"student_id": sid, "code": code, "password": password})


def test_register_with_valid_code_then_login(client, db):
    student = make_student(db)
    code = issue(db, student)
    r = register(client, "900000001", code)
    assert r.status_code == 200 and r.json()["student_id"] == "900000001"
    login = client.post("/api/student/login", json={"student_id": "900000001", "password": PW})
    assert login.status_code == 200 and login.json()["user_info"]["student_id"] == "900000001"


def test_code_is_case_and_dash_insensitive(client, db):
    code = issue(db, make_student(db))
    assert register(client, "900000001", code.lower().replace("-", " ")).status_code == 200


def test_code_is_consumed(client, db):
    code = issue(db, make_student(db))
    assert register(client, "900000001", code).status_code == 200
    again = register(client, "900000001", code, password="another-password")
    assert again.status_code == 400
    db.expire_all()
    s = db.query(models.Student).one()
    assert verify_password(PW, s.hashed_password) and s.enrollment_code_hash is None


def test_wrong_code_and_unknown_student_look_the_same(client, db):
    issue(db, make_student(db))
    wrong = register(client, "900000001", "AAAA-AAAA")
    unknown = register(client, "900000099", "AAAA-AAAA")
    assert wrong.status_code == unknown.status_code == 400
    assert wrong.json() == unknown.json()
    assert not db.query(models.Student).one().is_registered


def test_id_alone_or_name_alone_is_not_enough(client, db):
    make_student(db)  # no code issued at all
    assert register(client, "900000001", "ANYTHING").status_code == 400
    assert client.post("/api/student/register", json={"student_id": "900000001", "password": PW}).status_code == 422


def test_code_is_bound_to_its_student(client, db):
    make_student(db, "900000001", "Test Student 01")
    other = make_student(db, "900000002", "Test Student 02")
    code_other = issue(db, other)
    assert register(client, "900000001", code_other).status_code == 400
    assert register(client, "900000002", code_other).status_code == 200


def test_reissuing_replaces_the_old_code(client, db):
    student = make_student(db)
    old = issue(db, student)
    new = issue(db, student)
    assert register(client, "900000001", old).status_code == 400
    assert register(client, "900000001", new).status_code == 200


def test_short_password_rejected(client, db):
    code = issue(db, make_student(db))
    assert register(client, "900000001", code, password="short").status_code == 422
    assert register(client, "900000001", code).status_code == 200  # code was not burned


def test_reset_needs_a_fresh_code_not_id_and_name(client, db):
    student = make_student(db, password=PW)
    # the old attack: ID + name + new password
    old_style = client.post("/api/student/reset-password", json={
        "student_id": "900000001", "name": "Test Student 01", "new_password": "attacker-password"})
    assert old_style.status_code == 422
    guess = client.post("/api/student/reset-password", json={
        "student_id": "900000001", "code": "AAAA-AAAA", "new_password": "attacker-password"})
    assert guess.status_code == 400
    assert client.post("/api/student/login", json={"student_id": "900000001", "password": PW}).status_code == 200

    code = issue(db, student)
    ok = client.post("/api/student/reset-password", json={
        "student_id": "900000001", "code": code, "new_password": "brand-new-password"})
    assert ok.status_code == 200
    assert client.post("/api/student/login", json={"student_id": "900000001", "password": PW}).status_code == 401
    assert client.post("/api/student/login", json={"student_id": "900000001", "password": "brand-new-password"}).status_code == 200
    # consumed
    again = client.post("/api/student/reset-password", json={
        "student_id": "900000001", "code": code, "new_password": "third-password-x"})
    assert again.status_code == 400


def test_reset_for_unregistered_student_is_refused(client, db):
    code = issue(db, make_student(db))
    r = client.post("/api/student/reset-password", json={
        "student_id": "900000001", "code": code, "new_password": PW})
    assert r.status_code == 400
    assert register(client, "900000001", code).status_code == 200  # code still usable


def test_register_refuses_already_registered_student_even_with_reissued_code(client, db):
    student = make_student(db, password=PW)
    code = issue(db, student)
    assert register(client, "900000001", code, password="attacker-password").status_code == 400
    assert client.post("/api/student/login", json={"student_id": "900000001", "password": PW}).status_code == 200


def test_admin_endpoint_issues_codes_for_unregistered_and_reissues(client, db):
    make_student(db, "900000001", "Test Student 01", password=PW)
    make_student(db, "900000002", "Test Student 02")
    headers = admin_headers(client, db)

    assert client.post("/api/admin/codes", json={}, headers=headers).status_code == 400
    assert client.post("/api/admin/codes", json={"all_unregistered": True}).status_code == 401

    r = client.post("/api/admin/codes", json={"all_unregistered": True}, headers=headers).json()
    assert [c["student_id"] for c in r["codes"]] == ["900000002"]
    assert register(client, "900000002", r["codes"][0]["code"]).status_code == 200

    r = client.post("/api/admin/codes", json={"student_ids": ["900000001", "999"]}, headers=headers).json()
    assert r["unknown_student_ids"] == ["999"]
    reset = client.post("/api/student/reset-password", json={
        "student_id": "900000001", "code": r["codes"][0]["code"], "new_password": "brand-new-password"})
    assert reset.status_code == 200
