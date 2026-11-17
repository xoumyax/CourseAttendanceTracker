"""Small helpers shared by the tests."""
from pathlib import Path

from app import enrollment, models
from app.auth import hash_password

FIXTURES = Path(__file__).parent / "fixtures"


def make_student(db, student_id="900000001", name="Test Student 01", password=None):
    """Add a student; with a password they are registered, otherwise not."""
    student = models.Student(uin=student_id, name=name, hashed_password="")
    if password:
        student.hashed_password = hash_password(password)
        student.is_registered = True
    db.add(student)
    db.commit()
    db.refresh(student)
    return student


def issue(db, student):
    code = enrollment.issue_code(student)
    db.commit()
    return code


def make_admin(db, username="admin-test", password="correct-horse-battery"):
    if not db.query(models.Admin).filter_by(username=username).first():
        db.add(models.Admin(username=username, hashed_password=hash_password(password)))
        db.commit()
    return username, password


def admin_headers(client, db):
    username, password = make_admin(db)
    token = client.post("/api/admin/login", json={"username": username, "password": password}).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def student_headers(client, student_id="900000001", password="hunter2-hunter2"):
    token = client.post("/api/student/login", json={"student_id": student_id, "password": password}).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}
