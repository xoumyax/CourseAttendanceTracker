"""
Admin API endpoints: sessions, check-in codes, enrollment codes, reports.
"""
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response, status
from sqlalchemy.orm import Session

from .. import auth, enrollment, models, ratelimit, schemas, stats, timeutil, utils
from ..config import settings
from ..database import get_db

router = APIRouter(prefix="/api/admin", tags=["admin"])


def get_current_admin(
    authorization: Optional[str] = Header(None),
    db: Session = Depends(get_db),
) -> str:
    """Dependency to verify admin authentication (and that the account still exists)."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid authorization header"
        )
    username = auth.verify_admin_token(authorization[len("Bearer "):])
    if db.query(models.Admin).filter(models.Admin.username == username).first() is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Admin account no longer exists"
        )
    return username


@router.post("/login", response_model=dict)
def admin_login(req: schemas.AdminLoginRequest, request: Request, db: Session = Depends(get_db)):
    """Admin login endpoint."""
    ratelimit.guard(request, "admin-login", req.username)
    if not auth.verify_admin_credentials(db, req.username, req.password):
        exists = db.query(models.Admin).filter(models.Admin.username == req.username).first() is not None
        # Record first, then apply the per-IP cap: the failure must count even when the cap answers 429.
        ratelimit.fail(request, "admin-login", req.username, known=exists)
        ratelimit.guard_unknown(request, "admin-login", counted=not exists)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid admin credentials"
        )

    ratelimit.succeed(request, "admin-login", req.username)
    access_token = auth.create_access_token(
        data={"sub": req.username, "type": "admin"}
    )

    return {
        "access_token": access_token,
        "token_type": "bearer",
        "user_type": "admin",
        "user_info": {"username": req.username}
    }


@router.get("/dashboard", response_model=schemas.DashboardStats)
def get_dashboard_stats(
    admin: str = Depends(get_current_admin),
    db: Session = Depends(get_db)
):
    """Dashboard statistics."""
    today_session = db.query(models.Session).filter(
        models.Session.date == utils.local_today()
    ).first()

    recent = db.query(models.Attendance).order_by(
        models.Attendance.marked_at.desc()
    ).limit(10).all()

    return schemas.DashboardStats(
        total_students=db.query(models.Student).count(),
        total_registered_students=db.query(models.Student).filter(
            models.Student.is_registered == True  # noqa: E712
        ).count(),
        total_sessions=db.query(models.Session).count(),
        total_attendances=db.query(models.Attendance).count(),
        today_session=today_session,
        recent_attendances=[
            schemas.AttendanceRecord(
                student_id=att.student.uin,
                student_name=att.student.name,
                session_date=att.session.date,
                marked_at=att.marked_at
            ) for att in recent
        ]
    )


@router.get("/students/grades", response_model=List[schemas.StudentStats])
def get_all_student_grades(
    admin: str = Depends(get_current_admin),
    db: Session = Depends(get_db)
):
    """Grades and statistics for all students (test sessions are not graded)."""
    return [
        schemas.StudentStats(
            student_id=r["student_id"],
            name=r["name"],
            is_registered=r["is_registered"],
            total_sessions=r["total_regular_sessions"],
            attended_sessions=r["attended_regular"],
            attendance_percentage=r["attendance_percentage"],
            grade_points=r["grade_points"],
        ) for r in stats.all_student_rows(db)
    ]


@router.post("/sessions", response_model=schemas.SessionResponse)
def create_session(
    req: schemas.SessionCreate,
    admin: str = Depends(get_current_admin),
    db: Session = Depends(get_db)
):
    """Create one session on a date."""
    if not req.is_test_session:
        exists = db.query(models.Session).filter(
            models.Session.date == req.date,
            models.Session.is_test_session == False  # noqa: E712
        ).first()
        if exists:
            raise HTTPException(status_code=400, detail="A session already exists on that date")
    session = models.Session(date=req.date, is_test_session=req.is_test_session)
    db.add(session)
    db.commit()
    db.refresh(session)
    return session


@router.post("/sessions/create-test")
def create_test_sessions(
    admin: str = Depends(get_current_admin),
    db: Session = Depends(get_db)
):
    """Create test sessions for today until there are 2."""
    today = utils.local_today()
    existing = db.query(models.Session).filter(
        models.Session.date == today,
        models.Session.is_test_session == True  # noqa: E712
    ).count()

    if existing >= 2:
        raise HTTPException(status_code=400, detail="2 test sessions already exist for today")

    created = []
    for _ in range(2 - existing):
        session = models.Session(date=today, is_test_session=True)
        db.add(session)
        db.flush()
        created.append({"id": session.id, "date": session.date, "is_test_session": True})
    db.commit()

    return {"message": f"Created {len(created)} test sessions for today", "sessions": created}


@router.get("/sessions", response_model=List[schemas.SessionResponse])
def get_all_sessions(
    admin: str = Depends(get_current_admin),
    db: Session = Depends(get_db)
):
    """All sessions, newest first."""
    return db.query(models.Session).order_by(models.Session.date.desc(), models.Session.id.desc()).all()


@router.get("/sessions/today")
def get_today_sessions(
    admin: str = Depends(get_current_admin),
    db: Session = Depends(get_db)
):
    """Today's sessions (in the course timezone)."""
    sessions = db.query(models.Session).filter(
        models.Session.date == utils.local_today()
    ).all()
    return {"sessions": [schemas.SessionResponse.model_validate(s) for s in sessions]}


@router.post("/tokens/generate")
def generate_token(
    req: schemas.TokenGenerate,
    admin: str = Depends(get_current_admin),
    db: Session = Depends(get_db)
):
    """Generate a check-in code for a session."""
    session = db.query(models.Session).filter(models.Session.id == req.session_id).first()
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    token = utils.generate_checkin_code()
    expires_at = utils.calculate_code_expiry(session.is_test_session)

    db.add(models.SessionToken(
        session_id=req.session_id, token=token, expires_at=expires_at, is_active=True
    ))
    db.commit()

    return {
        "session_id": req.session_id,
        "token": token,
        "expires_at": expires_at,
        "is_test_session": session.is_test_session,
        "expiry_info": "Valid for " + (
            f"{settings.TEST_CODE_TTL_HOURS} hours"
            if session.is_test_session
            else f"{round(settings.CHECKIN_CODE_TTL_SECONDS / 60, 1):g} minutes"
        )
    }


@router.get("/tokens/active/{session_id}")
def get_active_tokens(
    session_id: int,
    admin: str = Depends(get_current_admin),
    db: Session = Depends(get_db)
):
    """Unexpired check-in codes for a session."""
    tokens = db.query(models.SessionToken).filter(
        models.SessionToken.session_id == session_id,
        models.SessionToken.is_active == True,  # noqa: E712
        models.SessionToken.expires_at >= timeutil.utcnow()
    ).all()

    return {
        "session_id": session_id,
        "tokens": [
            {"token": t.token, "expires_at": t.expires_at, "created_at": t.created_at}
            for t in tokens
        ]
    }


def _token_row(t: models.SessionToken, now: datetime) -> dict:
    return {
        "id": t.id,
        "token": t.token,
        "created_at": t.created_at,
        "expires_at": t.expires_at,
        "is_active": t.is_active,
        "is_expired": t.expires_at < now,
    }


@router.get("/tokens/history")
def get_token_history(
    admin: str = Depends(get_current_admin),
    db: Session = Depends(get_db)
):
    """Every check-in code ever generated."""
    now = timeutil.utcnow()
    tokens = db.query(models.SessionToken).join(models.Session).order_by(
        models.SessionToken.created_at.desc()
    ).all()

    return {
        "tokens": [
            {**_token_row(t, now), "session_id": t.session_id,
             "session_date": t.session.date, "is_test_session": t.session.is_test_session}
            for t in tokens
        ],
        "total": len(tokens)
    }


@router.get("/tokens/history/{session_id}")
def get_session_token_history(
    session_id: int,
    admin: str = Depends(get_current_admin),
    db: Session = Depends(get_db)
):
    """Check-in codes generated for one session."""
    session = db.query(models.Session).filter(models.Session.id == session_id).first()
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    now = timeutil.utcnow()
    tokens = db.query(models.SessionToken).filter(
        models.SessionToken.session_id == session_id
    ).order_by(models.SessionToken.created_at.desc()).all()

    return {
        "session_id": session_id,
        "session_date": session.date,
        "is_test_session": session.is_test_session,
        "tokens": [_token_row(t, now) for t in tokens],
        "total": len(tokens)
    }


@router.get("/attendance/session/{session_id}")
def get_session_attendance(
    session_id: int,
    admin: str = Depends(get_current_admin),
    db: Session = Depends(get_db)
):
    """Who checked in to a session."""
    session = db.query(models.Session).filter(models.Session.id == session_id).first()
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    attendances = db.query(models.Attendance).filter(
        models.Attendance.session_id == session_id
    ).all()

    return {
        "session": schemas.SessionResponse.model_validate(session),
        "attendances": [
            {"student_id": a.student.uin, "name": a.student.name, "marked_at": a.marked_at}
            for a in attendances
        ],
        "count": len(attendances)
    }


@router.get("/export/excel")
def export_attendance_excel(
    admin: str = Depends(get_current_admin),
    db: Session = Depends(get_db)
):
    """Download the attendance report as an .xlsx file."""
    content = utils.export_to_excel(stats.all_student_rows(db))
    stamp = timeutil.utcnow().astimezone(settings.tz).strftime("%Y%m%d_%H%M%S")
    return Response(
        content=content,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="attendance_report_{stamp}.xlsx"'},
    )


@router.post("/codes")
def generate_enrollment_codes(
    req: schemas.CodesRequest,
    admin: str = Depends(get_current_admin),
    db: Session = Depends(get_db)
):
    """
    Issue one-time enrollment codes (registration or password reset).

    Students who already hold an unused code are skipped unless `replace_outstanding` is set,
    because a new code voids the old one. With `dry_run` nothing changes and the counts are
    returned. Codes are returned once and stored only as hashes, so the caller must save them.
    """
    if not req.student_ids and not req.all_unregistered:
        raise HTTPException(status_code=400, detail="Give student_ids or set all_unregistered")
    plan = enrollment.plan_codes(
        db, student_ids=req.student_ids, everyone=req.all_unregistered,
        replace_outstanding=req.replace_outstanding,
    )
    summary = {
        "would_issue": len(plan.students),
        "would_void": plan.would_void,
        "skipped_outstanding": len(plan.skipped_outstanding),
        "unknown_student_ids": plan.unknown,
    }
    if req.dry_run:
        return {"dry_run": True, **summary}
    rows = enrollment.apply_plan(plan)
    db.commit()
    return {
        "dry_run": False,
        "codes": [{"student_id": sid, "name": name, "code": code} for sid, name, code in rows],
        **summary,
    }


@router.get("/settings")
def get_settings(db: Session = Depends(get_db)):
    """Admin settings (public so the student page can tell whether restrictions are off)."""
    row = db.query(models.AdminSettings).first()
    if not row:  # read-only: nothing is stored until an admin changes the switch
        return schemas.SettingsResponse(disable_time_restrictions=False)
    return schemas.SettingsResponse.model_validate(row)


@router.put("/settings")
def update_settings(
    req: schemas.SettingsUpdate,
    admin: str = Depends(get_current_admin),
    db: Session = Depends(get_db)
):
    """Update admin settings."""
    row = db.query(models.AdminSettings).first()
    if not row:
        row = models.AdminSettings()
        db.add(row)

    row.disable_time_restrictions = req.disable_time_restrictions
    row.updated_at = timeutil.utcnow()
    db.commit()
    db.refresh(row)

    return {
        "message": "Settings updated successfully",
        "settings": schemas.SettingsResponse.model_validate(row)
    }
