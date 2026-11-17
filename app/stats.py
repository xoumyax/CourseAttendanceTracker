"""
Attendance statistics shared by the admin dashboard, the Excel export and students.
"""
from typing import Dict, List

from sqlalchemy import func
from sqlalchemy.orm import Session as DbSession

from . import models, utils


def percentage(attended: int, total: int) -> float:
    return (attended / total * 100) if total else 0.0


def all_student_rows(db: DbSession) -> List[Dict]:
    """One dict per student with session counts (up to today), percentage and grade points."""
    # Only sessions that have happened (date on or before today in TIMEZONE) count, so a
    # term's worth of future sessions does not drag everyone's percentage down.
    today = utils.local_today()
    held = models.Session.date <= today
    total_sessions = db.query(models.Session).filter(held).count()
    total_regular = db.query(models.Session).filter(held, models.Session.is_test_session == False).count()  # noqa: E712

    counts = {}
    rows = (
        db.query(models.Attendance.student_id, models.Session.is_test_session, func.count())
        .join(models.Session, models.Session.id == models.Attendance.session_id)
        .filter(held)
        .group_by(models.Attendance.student_id, models.Session.is_test_session)
        .all()
    )
    for student_pk, is_test, count in rows:
        entry = counts.setdefault(student_pk, {"regular": 0, "test": 0})
        entry["test" if is_test else "regular"] = count

    result = []
    for student in db.query(models.Student).order_by(models.Student.name).all():
        c = counts.get(student.id, {"regular": 0, "test": 0})
        pct = percentage(c["regular"], total_regular)
        result.append({
            "student_id": student.uin,
            "name": student.name,
            "is_registered": bool(student.is_registered),
            "total_sessions": total_sessions,
            "total_regular_sessions": total_regular,
            "attended_all": c["regular"] + c["test"],
            "attended_regular": c["regular"],
            "attended_test": c["test"],
            "attendance_percentage": round(pct, 2),
            "grade_points": utils.calculate_grade(pct),
        })
    return result
