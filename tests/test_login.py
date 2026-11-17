from app import models

from .helpers import admin_headers, make_admin, make_student

PW = "hunter2-hunter2"


def login(client, sid="900000001", pw=PW):
    return client.post("/api/student/login", json={"student_id": sid, "password": pw})


def test_login_success_and_failures(client, db):
    make_student(db, password=PW)
    make_student(db, "900000002", "Test Student 02")  # unregistered
    ok = login(client)
    assert ok.status_code == 200 and ok.json()["access_token"]
    assert login(client, pw="wrong-password").status_code == 401
    assert login(client, sid="900000099").status_code == 401
    assert login(client, sid="900000002", pw="anything-at-all").status_code == 401


def test_error_does_not_reveal_whether_the_id_exists(client, db):
    make_student(db, password=PW)
    assert login(client, pw="wrong-password").json() == login(client, sid="900000099").json()


def test_admin_login_and_token_checks(client, db):
    username, password = make_admin(db)
    assert client.post("/api/admin/login", json={"username": username, "password": password}).status_code == 200
    assert client.post("/api/admin/login", json={"username": username, "password": "nope-nope-nope"}).status_code == 401
    assert client.post("/api/admin/login", json={"username": "ghost", "password": password}).status_code == 401
    assert client.get("/api/admin/dashboard").status_code == 401


def test_student_token_cannot_open_admin_api(client, db):
    make_student(db, password=PW)
    token = login(client).json()["access_token"]
    assert client.get("/api/admin/dashboard", headers={"Authorization": f"Bearer {token}"}).status_code == 401


def test_deleted_admin_token_stops_working(client, db):
    headers = admin_headers(client, db)
    assert client.get("/api/admin/dashboard", headers=headers).status_code == 200
    db.query(models.Admin).delete()
    db.commit()
    assert client.get("/api/admin/dashboard", headers=headers).status_code == 401
