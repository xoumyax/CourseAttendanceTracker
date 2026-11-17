"""
Utility functions: check-in codes, expiry, grading, check-in windows, Excel export.
"""
import io
import secrets
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional

import pandas as pd

from . import timeutil
from .config import settings


def generate_checkin_code() -> str:
    """A random 6-digit check-in code from the OS CSPRNG."""
    return f"{secrets.randbelow(1_000_000):06d}"


def calculate_code_expiry(is_test_session: bool = False) -> datetime:
    """Expiry time (aware UTC) for a check-in code created now."""
    if is_test_session:
        return timeutil.utcnow() + timedelta(hours=settings.TEST_CODE_TTL_HOURS)
    return timeutil.utcnow() + timedelta(seconds=settings.CHECKIN_CODE_TTL_SECONDS)


def calculate_grade(attendance_percentage: float) -> int:
    """Grade points for an attendance percentage (0-100), using GRADE_SCALE."""
    for threshold, points in settings.grade_scale:
        if attendance_percentage >= threshold:
            return points
    return 0


def local_today() -> date:
    """Today's date in the course TIMEZONE."""
    return timeutil.utcnow().astimezone(settings.tz).date()


def is_within_checkin_window(now: Optional[datetime] = None) -> bool:
    """True if check-in is open now according to CHECKIN_WINDOWS (empty = always)."""
    return timeutil.window_is_open(settings.windows, settings.tz, now or timeutil.utcnow())


def describe_windows() -> str:
    windows = settings.windows
    if not windows:
        return "any time"
    return "; ".join(w.text() for w in windows) + f" ({settings.TIMEZONE})"


def public_config() -> Dict:
    """The values templates and JS may see. Served at GET /api/config."""
    scale = settings.grade_scale
    return {
        "course_name": settings.COURSE_NAME,
        "timezone": settings.TIMEZONE,
        "always_open": not settings.windows,
        "checkin_windows": [
            {
                "days": [timeutil.DAY_NAMES[d] for d in w.days],
                "start": w.start.strftime("%H:%M"),
                "end": w.end.strftime("%H:%M"),
                "text": w.text(),
            }
            for w in settings.windows
        ],
        "checkin_windows_text": describe_windows(),
        "open_now": is_within_checkin_window(),
        "server_time": timeutil.utcnow().isoformat(),
        "code_ttl_seconds": settings.CHECKIN_CODE_TTL_SECONDS,
        "code_ttl_minutes": round(settings.CHECKIN_CODE_TTL_SECONDS / 60, 1),
        "test_code_ttl_hours": settings.TEST_CODE_TTL_HOURS,
        "grade_scale": [{"min_percent": p, "points": pts} for p, pts in scale],
        "max_points": max(pts for _, pts in scale),
    }


def spreadsheet_safe(value):
    """Stop spreadsheet apps from running a student name such as '=1+1' as a formula."""
    if isinstance(value, str) and value[:1] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + value
    return value


def export_to_excel(rows: List[Dict]) -> bytes:
    """
    Build the attendance workbook in memory and return its bytes.

    Each row needs the keys used in COLUMNS below.
    """
    columns = [
        ("student_id", "Student ID"),
        ("name", "Name"),
        ("total_sessions", "Sessions Held (All)"),
        ("total_regular_sessions", "Regular Sessions Held"),
        ("attended_all", "Attended (All)"),
        ("attended_regular", "Attended (Regular)"),
        ("attended_test", "Attended (Test)"),
        ("attendance_percentage", "Attendance % (Regular, to date)"),
        ("grade_points", "Grade Points"),
    ]
    df = pd.DataFrame(
        [{label: spreadsheet_safe(row[key]) for key, label in columns} for row in rows],
        columns=[label for _, label in columns],
    )
    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        df.to_excel(writer, sheet_name="Attendance Report", index=False)
        sheet = writer.sheets["Attendance Report"]
        for column in sheet.columns:
            width = max(len(str(cell.value)) if cell.value is not None else 0 for cell in column)
            sheet.column_dimensions[column[0].column_letter].width = width + 2
    return buffer.getvalue()
