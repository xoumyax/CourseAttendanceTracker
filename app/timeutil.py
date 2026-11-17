"""
Time helpers: UTC clock, timezone-aware storage, and parsers for the
CHECKIN_WINDOWS and GRADE_SCALE settings.

All stored datetimes are UTC. Code that needs "now" must call ``timeutil.utcnow()``
(through the module, not ``from ... import``) so tests can freeze the clock.
"""
from dataclasses import dataclass
from datetime import datetime, time, timezone
from typing import List, Tuple
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy.types import DateTime, TypeDecorator

DAY_NAMES = ["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"]  # index == datetime.weekday()


def utcnow() -> datetime:
    """Current time as a timezone-aware UTC datetime."""
    return datetime.now(timezone.utc)


def get_timezone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError, OSError):
        raise ValueError(f"TIMEZONE '{name}' is not a valid IANA timezone name (e.g. America/Chicago)")


class UTCDateTime(TypeDecorator):
    """Stores datetimes as naive UTC and returns them as aware UTC."""
    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            return value  # naive values are assumed to be UTC already
        return value.astimezone(timezone.utc).replace(tzinfo=None)

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        return value.replace(tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Check-in windows
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Window:
    days: Tuple[int, ...]  # weekday numbers, Monday = 0
    start: time
    end: time  # exclusive

    def text(self) -> str:
        days = ",".join(DAY_NAMES[d] for d in self.days)
        return f"{days} {self.start:%H:%M}-{self.end:%H:%M}"


def _parse_days(spec: str) -> Tuple[int, ...]:
    days = set()
    for part in spec.split(","):
        part = part.strip().upper()
        if not part:
            raise ValueError("empty day name")
        if "-" in part:
            a, b = (x.strip() for x in part.split("-", 1))
            if a not in DAY_NAMES or b not in DAY_NAMES:
                raise ValueError(f"unknown day in '{part}'")
            i, j = DAY_NAMES.index(a), DAY_NAMES.index(b)
            if i > j:
                raise ValueError(f"day range '{part}' must not wrap around the week")
            days.update(range(i, j + 1))
        else:
            if part not in DAY_NAMES:
                raise ValueError(f"unknown day '{part}' (use {', '.join(DAY_NAMES)})")
            days.add(DAY_NAMES.index(part))
    return tuple(sorted(days))


def _parse_clock(text: str) -> time:
    try:
        return datetime.strptime(text.strip(), "%H:%M").time()
    except ValueError:
        raise ValueError(f"'{text.strip()}' is not a time in HH:MM format")


def parse_windows(value: str) -> List[Window]:
    """
    Parse e.g. "MON,WED,FRI 08:00-09:00; TUE,THU 14:00-15:15".
    A range such as MON-FRI is also accepted. Empty means always open.
    Raises ValueError with a readable message on bad input.
    """
    windows: List[Window] = []
    for chunk in (value or "").split(";"):
        chunk = chunk.strip()
        if not chunk:
            continue
        try:
            days_part, _, times_part = chunk.rpartition(" ")
            if not days_part or "-" not in times_part:
                raise ValueError("expected '<days> HH:MM-HH:MM'")
            start_s, end_s = times_part.split("-", 1)
            start, end = _parse_clock(start_s), _parse_clock(end_s)
            if start >= end:
                raise ValueError("start time must be before end time")
            windows.append(Window(_parse_days(days_part), start, end))
        except ValueError as exc:
            raise ValueError(f"Invalid CHECKIN_WINDOWS entry '{chunk}': {exc}") from None
    return windows


def window_is_open(windows: List[Window], tz: ZoneInfo, now: datetime) -> bool:
    """True when `now` falls in any window (or there are no windows at all)."""
    if not windows:
        return True
    local = now.astimezone(tz)
    return any(
        local.weekday() in w.days and w.start <= local.time().replace(tzinfo=None) < w.end
        for w in windows
    )


# ---------------------------------------------------------------------------
# Grade scale
# ---------------------------------------------------------------------------

def parse_grade_scale(value: str) -> List[Tuple[float, int]]:
    """
    Parse "85:10,75:8,50:5,0:0" into [(85.0, 10), (75.0, 8), ...] sorted by
    threshold, highest first. Raises ValueError on bad input.
    """
    pairs: List[Tuple[float, int]] = []
    for part in (value or "").split(","):
        part = part.strip()
        if not part:
            continue
        try:
            pct_s, pts_s = part.split(":")
            pct, pts = float(pct_s), int(pts_s)
        except ValueError:
            raise ValueError(f"Invalid GRADE_SCALE entry '{part}': expected '<percent>:<points>'") from None
        if not 0 <= pct <= 100:
            raise ValueError(f"Invalid GRADE_SCALE entry '{part}': percent must be between 0 and 100")
        if pts < 0:
            raise ValueError(f"Invalid GRADE_SCALE entry '{part}': points must not be negative")
        pairs.append((pct, pts))
    if not pairs:
        raise ValueError("GRADE_SCALE must have at least one '<percent>:<points>' entry")
    if len({p for p, _ in pairs}) != len(pairs):
        raise ValueError("GRADE_SCALE has a duplicate percentage threshold")
    return sorted(pairs, key=lambda p: p[0], reverse=True)
