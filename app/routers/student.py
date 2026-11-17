"""
Student API endpoints: registration, login, password reset and check-in.
"""
from typing import Optional

from sqlalchemy.exc import IntegrityError
from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from sqlalchemy.orm import Session

from .. import auth, enrollment, models, ratelimit, schemas, stats, timeutil, utils
from ..database import get_db

router = APIRouter(prefix="/api/student", tags=["student"])

BAD_ENROLLMENT = (
    "That student ID and enrollment code do not match, or the code was already used. "
    "Ask your instructor for a new code."
)


def get_current_student(authorization: Optional[str] = Header(None)) -> int:
    """Dependency to verify student authentication."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid authorization header"
        )
    return auth.verify_student_token(authorization[len("Bearer "):])


def _find_student(db: Session, student_id: str) -> Optional[models.Student]:
    # student_id was already stripped by the request schema; the same value keys the rate limiter.
    return db.query(models.Student).filter(models.Student.uin == student_id).first()


def _enrollment_failure(request: Request, student: Optional[models.Student], student_id: str):
    """Record a bad ID/code pair and raise the (deliberately uniform) error."""
    # Record first, then apply the per-IP cap: the failure must count even when the cap answers 429.
    ratelimit.fail(request, "enroll", student_id, known=student is not None)
    ratelimit.guard_unknown(request, "enroll", counted=student is None)
    raise HTTPException(status_code=400, detail=BAD_ENROLLMENT)


@router.post("/register")
def student_register(req: schemas.StudentRegisterRequest, request: Request, db: Session = Depends(get_db)):
    """Set a password using the student ID and the one-time enrollment code."""
    ratelimit.guard(request, "enroll", req.student_id)
    student = _find_student(db, req.student_id)

    if not enrollment.code_matches(student, req.code):
        _enrollment_failure(request, student, req.student_id)

    if student.is_registered:
        raise HTTPException(
            status_code=400,
            detail="This account is already registered. To set a new password, use the "
                   "reset page with the new code your instructor gave you."
        )

    student.hashed_password = auth.hash_password(req.password)
    student.is_registered = True
    enrollment.consume(student)
    db.commit()
    ratelimit.succeed(request, "enroll", req.student_id)

    return {
        "message": "Registration successful! You can now log in.",
        "student_id": student.uin,
        "name": student.name,
    }


@router.post("/reset-password")
def reset_password(req: schemas.StudentResetPasswordRequest, request: Request, db: Session = Depends(get_db)):
    """Set a new password. Needs a fresh enrollment code issued by an instructor."""
    ratelimit.guard(request, "enroll", req.student_id)
    student = _find_student(db, req.student_id)

    if not enrollment.code_matches(student, req.code):
        _enrollment_failure(request, student, req.student_id)

    if not student.is_registered:
        raise HTTPException(
            status_code=400,
            detail="This account is not registered yet. Use the registration page instead."
        )

    student.hashed_password = auth.hash_password(req.new_password)
    enrollment.consume(student)
    db.commit()
    ratelimit.succeed(request, "enroll", req.student_id)

    return {
        "message": "Password reset successful! You can now log in with your new password.",
        "student_id": student.uin,
        "name": student.name,
    }


@router.post("/login")
def student_login(req: schemas.StudentLoginRequest, request: Request, db: Session = Depends(get_db)):
    """Student login endpoint."""
    ratelimit.guard(request, "login", req.student_id)
    student = _find_student(db, req.student_id)

    if student is not None and student.is_registered:
        ok = auth.verify_password(req.password, student.hashed_password)
    else:
        auth.pwd_context.dummy_verify()  # keep timing similar for unknown IDs
        ok = False

    if not ok:
        # Record first, then apply the per-IP cap: the failure must count even when the cap answers 429.
        ratelimit.fail(request, "login", req.student_id, known=student is not None)
        ratelimit.guard_unknown(request, "login", counted=student is None)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid student ID or password. If you have not registered yet, "
                   "use the registration page."
        )

    ratelimit.succeed(request, "login", req.student_id)
    access_token = auth.create_access_token(
        data={"sub": str(student.id), "type": "student"}
    )

    return {
        "access_token": access_token,
        "token_type": "bearer",
        "user_type": "student",
        "user_info": {
            "id": student.id,
            "student_id": student.uin,
            "name": student.name,
        }
    }


def _sessions_payload(db: Session, student_id: int, future: bool):
    today = utils.local_today()
    query = db.query(models.Session)
    query = query.filter(models.Session.date >= today) if future else query.filter(models.Session.date == today)
    sessions = query.order_by(models.Session.date).all()
    attended_ids = {
        a.session_id for a in db.query(models.Attendance).filter(
            models.Attendance.student_id == student_id
        ).all()
    }
    return [
        {
            "id": s.id,
            "date": s.date,
            "is_test_session": s.is_test_session,
            "already_marked": s.id in attended_ids,
            "is_today": s.date == today,
        } for s in sessions
    ]


@router.get("/sessions/today")
def get_today_sessions(
    student_id: int = Depends(get_current_student),
    db: Session = Depends(get_db)
):
    """Today's sessions (in the course timezone)."""
    return {"sessions": _sessions_payload(db, student_id, future=False)}


@router.get("/sessions/available")
def get_available_sessions(
    student_id: int = Depends(get_current_student),
    db: Session = Depends(get_db)
):
    """Today's and future sessions."""
    return {"sessions": _sessions_payload(db, student_id, future=True)}


@router.post("/attendance/mark")
def mark_attendance(
    req: schemas.AttendanceMarkRequest,
    request: Request,
    student_id: int = Depends(get_current_student),
    db: Session = Depends(get_db)
):
    """Check in to a session with the code shown in class."""
    ratelimit.guard(request, "checkin", str(student_id))

    session = db.query(models.Session).filter(models.Session.id == req.session_id).first()
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    if session.date != utils.local_today():
        raise HTTPException(
            status_code=400,
            detail="You can only check in to today's sessions"
        )

    admin_settings = db.query(models.AdminSettings).first()
    restrictions_off = admin_settings.disable_time_restrictions if admin_settings else False

    if not session.is_test_session and not restrictions_off:
        if not utils.is_within_checkin_window():
            raise HTTPException(
                status_code=403,
                detail=f"Check-in is closed. It is open: {utils.describe_windows()}"
            )

    existing = db.query(models.Attendance).filter(
        models.Attendance.student_id == student_id,
        models.Attendance.session_id == req.session_id
    ).first()
    if existing:
        raise HTTPException(status_code=400, detail="Attendance already marked for this session")

    now = timeutil.utcnow()
    code_record = db.query(models.SessionToken).filter(
        models.SessionToken.session_id == req.session_id,
        models.SessionToken.token == req.token,
        models.SessionToken.is_active == True,  # noqa: E712
        models.SessionToken.expires_at >= now
    ).first()
    if not code_record:
        ratelimit.fail(request, "checkin", str(student_id))
        raise HTTPException(status_code=403, detail="Invalid or expired check-in code")

    attendance = models.Attendance(student_id=student_id, session_id=req.session_id)
    db.add(attendance)
    try:
        db.commit()
    except IntegrityError:  # two simultaneous check-ins: the unique constraint caught the second
        db.rollback()
        raise HTTPException(status_code=400, detail="Attendance already marked for this session")
    ratelimit.succeed(request, "checkin", str(student_id))
    db.refresh(attendance)

    return {
        "message": "Attendance marked successfully",
        "attendance": schemas.AttendanceResponse.model_validate(attendance)
    }


@router.get("/attendance/my-records")
def get_my_attendance(
    student_id: int = Depends(get_current_student),
    db: Session = Depends(get_db)
):
    """The student's own attendance records and grade."""
    student = db.query(models.Student).filter(models.Student.id == student_id).first()
    if student is None:
        raise HTTPException(status_code=401, detail="Account no longer exists")

    row = next(r for r in stats.all_student_rows(db) if r["student_id"] == student.uin)
    attendances = db.query(models.Attendance).filter(
        models.Attendance.student_id == student_id
    ).all()

    return {
        "student": {"student_id": student.uin, "name": student.name},
        "statistics": {
            "total_sessions": row["total_sessions"],
            "total_regular_sessions": row["total_regular_sessions"],
            "attended_sessions": row["attended_all"],
            "attended_regular_sessions": row["attended_regular"],
            "attended_test_sessions": row["attended_test"],
            "attendance_percentage": row["attendance_percentage"],
            "grade_points": row["grade_points"],
        },
        "records": [
            {
                "session_id": a.session_id,
                "session_date": a.session.date,
                "marked_at": a.marked_at,
                "is_test_session": a.session.is_test_session,
            } for a in attendances
        ]
    }
