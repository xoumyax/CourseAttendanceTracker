import os
import subprocess
import sys
from pathlib import Path
from datetime import datetime, time, timezone

import pytest

from app import timeutil
from app.utils import calculate_grade


def test_parse_windows_multiple_and_ranges():
    ws = timeutil.parse_windows("MON,WED,FRI 08:00-09:00; TUE-THU 14:00-15:15")
    assert ws[0].days == (0, 2, 4) and ws[0].start == time(8) and ws[0].end == time(9)
    assert ws[1].days == (1, 2, 3) and ws[1].end == time(15, 15)
    assert ws[0].text() == "MON,WED,FRI 08:00-09:00"


def test_parse_windows_empty_is_always_open():
    tz = timeutil.get_timezone("UTC")
    assert timeutil.parse_windows("") == []
    assert timeutil.window_is_open([], tz, datetime(2026, 3, 7, 3, 0, tzinfo=timezone.utc))


@pytest.mark.parametrize("bad", ["MON", "FUN 08:00-09:00", "MON 09:00-08:00", "MON 8am-9am", "MON 08:00"])
def test_parse_windows_rejects_garbage(bad):
    with pytest.raises(ValueError):
        timeutil.parse_windows(bad)


def test_window_uses_course_timezone():
    ws = timeutil.parse_windows("WED 08:00-09:00")
    tz = timeutil.get_timezone("America/Chicago")
    # 14:30 UTC on 2026-03-04 is 08:30 in Chicago (CST, UTC-6): open
    assert timeutil.window_is_open(ws, tz, datetime(2026, 3, 4, 14, 30, tzinfo=timezone.utc))
    # 08:30 UTC is 02:30 in Chicago: closed
    assert not timeutil.window_is_open(ws, tz, datetime(2026, 3, 4, 8, 30, tzinfo=timezone.utc))
    # end is exclusive
    assert not timeutil.window_is_open(ws, tz, datetime(2026, 3, 4, 15, 0, tzinfo=timezone.utc))


def test_grade_scale_parsing_and_lookup(cfg):
    assert timeutil.parse_grade_scale("0:0,50:5,85:10,75:8") == [(85, 10), (75, 8), (50, 5), (0, 0)]
    assert calculate_grade(100) == 10
    assert calculate_grade(85) == 10
    assert calculate_grade(84.99) == 8
    assert calculate_grade(75) == 8
    assert calculate_grade(50) == 5
    assert calculate_grade(49.9) == 0
    cfg(GRADE_SCALE="90:4,60:3,0:1")
    assert calculate_grade(95) == 4 and calculate_grade(10) == 1


@pytest.mark.parametrize("bad", ["", "85", "a:b", "101:5", "50:5,50:6"])
def test_grade_scale_rejects_garbage(bad):
    with pytest.raises(ValueError):
        timeutil.parse_grade_scale(bad)


REPO = str(Path(__file__).resolve().parent.parent)


def _run_import(tmp_path, **env_overrides):
    """Import app.config in a clean process (no .env in cwd) and return the result."""
    env = {k: v for k, v in os.environ.items() if k != "SECRET_KEY"}
    env.update(SECRET_KEY="a-valid-secret-for-this-test-0123456789", PYTHONPATH=REPO)
    env.update(env_overrides)
    return subprocess.run([sys.executable, "-c", "import app.config"], env=env,
                          capture_output=True, text=True, cwd=tmp_path)


@pytest.mark.parametrize("secret", ["", "change-me", "change-me-please"])
def test_startup_fails_without_real_secret(secret, tmp_path):
    r = _run_import(tmp_path, SECRET_KEY=secret)
    assert r.returncode != 0
    assert "SECRET_KEY" in r.stderr


def test_startup_fails_when_secret_missing_entirely(tmp_path):
    env = {k: v for k, v in os.environ.items() if k != "SECRET_KEY"}
    env["PYTHONPATH"] = REPO
    r = subprocess.run([sys.executable, "-c", "import app.config"], env=env,
                       capture_output=True, text=True, cwd=tmp_path)
    assert r.returncode != 0 and "SECRET_KEY" in r.stderr


def test_startup_fails_on_bad_timezone_windows_or_scale(tmp_path):
    assert "TIMEZONE" in _run_import(tmp_path, TIMEZONE="Mars/Base").stderr
    assert "CHECKIN_WINDOWS" in _run_import(tmp_path, CHECKIN_WINDOWS="someday").stderr
    assert "GRADE_SCALE" in _run_import(tmp_path, GRADE_SCALE="lots").stderr


def test_startup_ok_with_valid_config(tmp_path):
    assert _run_import(tmp_path).returncode == 0
