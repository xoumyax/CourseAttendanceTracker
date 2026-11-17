"""
One-time enrollment codes.

An admin issues a code for a student; the student uses it once to register or to
reset a password. Only a keyed hash is stored, so a code cannot be read back from
the database. Changing SECRET_KEY invalidates codes that are still unused.
"""
import hashlib
import hmac
import secrets
from dataclasses import dataclass, field
from typing import Iterable, List, Optional, Tuple

from sqlalchemy.orm import Session as DbSession

from . import models, timeutil
from .config import settings

# 32 characters without I and O; 8 of them are 40 bits of randomness.
ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
CODE_LENGTH = 8


def new_code() -> str:
    """A fresh code such as 'K7Q2-9MXD'."""
    raw = "".join(secrets.choice(ALPHABET) for _ in range(CODE_LENGTH))
    return f"{raw[:4]}-{raw[4:]}"


def normalize(code: str) -> str:
    return "".join(ch for ch in (code or "").upper() if ch.isalnum())


def hash_code(student_id: str, code: str) -> str:
    key = settings.SECRET_KEY.encode("utf-8")
    message = f"{student_id}:{normalize(code)}".encode("utf-8")
    return hmac.new(key, message, hashlib.sha256).hexdigest()


def issue_code(student: models.Student) -> str:
    """Create a new code for the student (replacing any earlier one). Caller commits."""
    code = new_code()
    student.enrollment_code_hash = hash_code(student.uin, code)
    student.enrollment_code_issued_at = timeutil.utcnow()
    return code


def code_matches(student: Optional[models.Student], code: str) -> bool:
    if student is None or not student.enrollment_code_hash:
        return False
    return hmac.compare_digest(student.enrollment_code_hash, hash_code(student.uin, code))


def consume(student: models.Student) -> None:
    student.enrollment_code_hash = None
    student.enrollment_code_issued_at = None


@dataclass
class IssuePlan:
    """Who would get a code, who is skipped, and how many outstanding codes get voided."""
    students: List[models.Student] = field(default_factory=list)
    skipped_outstanding: List[str] = field(default_factory=list)  # student IDs left alone
    unknown: List[str] = field(default_factory=list)

    @property
    def would_void(self) -> int:
        return sum(1 for s in self.students if s.enrollment_code_hash)


def plan_codes(
    db: DbSession,
    student_ids: Optional[Iterable[str]] = None,
    everyone: bool = False,
    include_registered: bool = False,
    replace_outstanding: bool = False,
) -> IssuePlan:
    """
    Decide who gets a code, without changing anything.

    `everyone` covers students not yet registered (also registered ones with
    `include_registered`). Explicitly listed students are always eligible. A student who already
    holds an unused code is skipped unless `replace_outstanding`, because issuing a new code
    voids the one they were already given.
    """
    plan = IssuePlan()
    candidates: List[models.Student] = []
    if everyone:
        query = db.query(models.Student).order_by(models.Student.name)
        if not include_registered:
            query = query.filter(models.Student.is_registered == False)  # noqa: E712
        candidates = query.all()
    for sid in student_ids or []:
        sid = str(sid).strip()
        found = db.query(models.Student).filter(models.Student.uin == sid).first()
        if found is None:
            plan.unknown.append(sid)
        elif found not in candidates:
            candidates.append(found)
    for student in candidates:
        if student.enrollment_code_hash and not replace_outstanding:
            plan.skipped_outstanding.append(student.uin)
        else:
            plan.students.append(student)
    return plan


def apply_plan(plan: IssuePlan) -> List[Tuple[str, str, str]]:
    """Generate codes for the planned students. Does NOT commit: the caller commits once the
    codes are safely written somewhere, so a failure never voids codes nobody received."""
    return [(s.uin, s.name, issue_code(s)) for s in plan.students]
