import io
import os
import re
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from app import cli, models
from app.auth import verify_password

from .helpers import FIXTURES, make_student

REPO = str(Path(__file__).resolve().parent.parent)


def run(*argv, stdin=None):
    if stdin is not None:
        sys.stdin = io.StringIO(stdin)
    cli.main(list(argv))


@pytest.fixture(autouse=True)
def _restore_stdin():
    original = sys.stdin
    yield
    sys.stdin = original


def subprocess_cli(tmp_path, *argv, env=None):
    full_env = {k: v for k, v in os.environ.items()}
    full_env.update(PYTHONPATH=REPO, DATABASE_URL=f"sqlite:///{tmp_path}/cli.db")
    full_env.update(env or {})
    return subprocess.run([sys.executable, "-m", "app.cli", *argv], env=full_env,
                          capture_output=True, text=True, cwd=tmp_path)


# --- admin ---------------------------------------------------------------

def test_admin_set_list_delete(db, capsys):
    run("admin", "set", "ta-one", "--password-stdin", stdin="a-long-password\n")
    admin = db.query(models.Admin).one()
    assert admin.username == "ta-one"
    assert verify_password("a-long-password", admin.hashed_password)

    run("admin", "set", "ta-one", "--password-stdin", stdin="another-password\n")  # update
    db.expire_all()
    assert verify_password("another-password", db.query(models.Admin).one().hashed_password)

    capsys.readouterr()
    run("admin", "list")
    assert capsys.readouterr().out.split() == ["ta-one"]

    run("admin", "delete", "ta-one")
    assert db.query(models.Admin).count() == 0
    with pytest.raises(SystemExit):
        run("admin", "delete", "ta-one")


def test_admin_set_rejects_short_password(db):
    with pytest.raises(SystemExit):
        run("admin", "set", "ta-one", "--password-stdin", stdin="short\n")
    assert db.query(models.Admin).count() == 0


# --- roster --------------------------------------------------------------

def test_roster_import_simple_and_reimport(db):
    run("roster", "import", str(FIXTURES / "roster_simple.csv"))
    assert {s.uin: s.name for s in db.query(models.Student)} == {
        "900000001": "Test Student 01", "900000002": "Test Student 02", "900000003": "Test Student 03"}

    run("roster", "import", str(FIXTURES / "roster_simple_update.csv"), "--format", "simple")
    db.expire_all()
    names = {s.uin: s.name for s in db.query(models.Student)}
    assert len(names) == 4  # one added, nobody duplicated
    assert names["900000002"] == "Test Student 02 Renamed"
    assert names["900000004"] == "Test Student 04"


def test_roster_import_canvas_skips_points_possible_and_test_student(db):
    run("roster", "import", str(FIXTURES / "roster_canvas.csv"), "--format", "canvas")
    names = {s.uin: s.name for s in db.query(models.Student)}
    assert names == {"900000001": "Student, Alpha", "900000002": "Student, Beta", "900000003": "Student, Gamma"}


def test_roster_import_keeps_registration(db):
    make_student(db, "900000001", "Old Name", password="hunter2-hunter2")
    run("roster", "import", str(FIXTURES / "roster_simple.csv"))
    db.expire_all()
    s = db.query(models.Student).filter_by(uin="900000001").one()
    assert s.name == "Test Student 01" and s.is_registered


def test_roster_import_wrong_format_is_a_clear_error(capsys):
    with pytest.raises(SystemExit):
        run("roster", "import", str(FIXTURES / "roster_simple.csv"), "--format", "canvas")
    assert "SIS User ID" in capsys.readouterr().err


def test_roster_import_missing_file():
    with pytest.raises(SystemExit):
        run("roster", "import", "/nonexistent/roster.csv")


# --- codes ---------------------------------------------------------------

def test_codes_generate_all_skips_registered(db, tmp_path):
    make_student(db, "900000001", "Test Student 01", password="hunter2-hunter2")
    make_student(db, "900000002", "Test Student 02")
    out = tmp_path / "codes.csv"
    run("codes", "generate", "--all", "--out", str(out))
    lines = out.read_text().splitlines()
    assert lines[0] == "student_id,name,code"
    assert len(lines) == 2 and lines[1].startswith("900000002,Test Student 02,")
    assert oct(out.stat().st_mode & 0o777) == "0o600"

    code = lines[1].split(",")[2]
    db.expire_all()
    stored = db.query(models.Student).filter_by(uin="900000002").one().enrollment_code_hash
    assert stored and code not in stored and code.replace("-", "") not in stored


def test_codes_generate_reissue_and_student(db, tmp_path):
    make_student(db, "900000001", password="hunter2-hunter2")
    make_student(db, "900000002", "Test Student 02")
    out = tmp_path / "all.csv"
    run("codes", "generate", "--all", "--reissue", "--out", str(out))
    assert len(out.read_text().splitlines()) == 3

    # both now hold an unused code: asking again without --reissue must not void them
    one = tmp_path / "one.csv"
    before = {s.uin: s.enrollment_code_hash for s in db.query(models.Student)}
    run("codes", "generate", "--student", "900000001", "--out", str(one))
    assert not one.exists()
    db.expire_all()
    assert before == {s.uin: s.enrollment_code_hash for s in db.query(models.Student)}

    run("codes", "generate", "--student", "900000001", "--reissue", "--out", str(one))
    assert one.read_text().splitlines()[1].startswith("900000001,")
    db.expire_all()
    assert db.query(models.Student).filter_by(uin="900000001").one().enrollment_code_hash != before["900000001"]


def test_codes_all_twice_does_not_void_the_first_csv(db, tmp_path, client):
    make_student(db, "900000001", "Test Student 01")
    first = tmp_path / "first.csv"
    run("codes", "generate", "--all", "--out", str(first))
    code = first.read_text().splitlines()[1].split(",")[2]
    second = tmp_path / "second.csv"
    run("codes", "generate", "--all", "--out", str(second))  # late enrollee run: nothing to do
    assert not second.exists()
    r = client.post("/api/student/register", json={"student_id": "900000001", "code": code, "password": "hunter2-hunter2"})
    assert r.status_code == 200


def test_codes_unwritable_output_changes_nothing(db, tmp_path):
    make_student(db, "900000001", "Test Student 01")
    with pytest.raises(SystemExit):
        run("codes", "generate", "--all", "--out", str(tmp_path / "missing-dir" / "x.csv"))
    db.expire_all()
    assert db.query(models.Student).one().enrollment_code_hash is None


def test_codes_csv_neutralises_formula_names(db, tmp_path):
    make_student(db, "900000001", "=1+1")
    out = tmp_path / "c.csv"
    run("codes", "generate", "--all", "--out", str(out))
    assert out.read_text().splitlines()[1].startswith("900000001,'=1+1,")


def test_codes_generate_refuses_to_overwrite_and_needs_a_target(db, tmp_path):
    make_student(db, "900000001")
    out = tmp_path / "codes.csv"
    out.write_text("keep me")
    with pytest.raises(SystemExit):
        run("codes", "generate", "--all", "--out", str(out))
    assert out.read_text() == "keep me"
    with pytest.raises(SystemExit):
        run("codes", "generate", "--out", str(tmp_path / "x.csv"))


# --- sessions ------------------------------------------------------------

def test_sessions_create_list_and_range(db, cfg, capsys):
    run("sessions", "create", "--date", "2026-03-02")
    run("sessions", "create", "--date", "2026-03-02")  # duplicate regular session is skipped
    run("sessions", "create", "--date", "2026-03-02", "--test")
    assert db.query(models.Session).count() == 2

    cfg(CHECKIN_WINDOWS="MON,WED 08:00-09:00")
    run("sessions", "create", "--date", "2026-03-09", "--until", "2026-03-18")
    dates = sorted(s.date.isoformat() for s in db.query(models.Session).filter_by(is_test_session=False))
    assert dates == ["2026-03-02", "2026-03-09", "2026-03-11", "2026-03-16", "2026-03-18"]

    capsys.readouterr()
    run("sessions", "list")
    assert "2026-03-02" in capsys.readouterr().out


def test_sessions_create_bad_date():
    with pytest.raises(SystemExit):
        run("sessions", "create", "--date", "03/02/2026")


# --- subprocess commands --------------------------------------------------

def test_cli_populates_metadata_when_run_alone():
    code = "from app import cli; from app.database import Base; assert not Base.metadata.tables; cli._load_models(); print(sorted(Base.metadata.tables))"
    env = {k: v for k, v in os.environ.items()}
    env["PYTHONPATH"] = REPO
    r = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert "students" in r.stdout and "admins" in r.stdout


def table_names(path):
    con = sqlite3.connect(path)
    try:
        return {r[0] for r in con.execute("select name from sqlite_master where type='table'")}
    finally:
        con.close()


def test_init_db_creates_the_tables_and_purge_drops_them(tmp_path):
    r = subprocess_cli(tmp_path, "init-db")
    assert r.returncode == 0 and "6 tables" in r.stdout
    db_file = tmp_path / "cli.db"
    assert {"students", "sessions", "attendances", "admins", "session_tokens", "admin_settings"} <= table_names(db_file)

    assert subprocess_cli(tmp_path, "roster", "import", str(FIXTURES / "roster_simple.csv")).returncode == 0
    refused = subprocess_cli(tmp_path, "purge")
    assert refused.returncode == 2 and db_file.exists() and "cli.db" in refused.stderr
    done = subprocess_cli(tmp_path, "purge", "--confirm")
    assert done.returncode == 0, done.stderr
    assert "dropped 6 tables holding 3 rows" in done.stdout
    assert not db_file.exists()


def test_drop_all_tables_empties_any_engine(tmp_path):
    """The drop itself, independent of the SQLite file deletion (so it also covers other databases)."""
    from sqlalchemy import create_engine, inspect
    from app.database import Base
    cli._load_models()
    engine = create_engine(f"sqlite:///{tmp_path}/other.db")
    Base.metadata.create_all(bind=engine)
    with engine.begin() as conn:
        conn.exec_driver_sql("insert into students (uin, name, hashed_password, is_registered) values ('1', 'Test Student 01', '', 0)")
    removed, leftover = cli.drop_all_tables(engine)
    assert dict(removed)["students"] == 1 and len(removed) == 6
    assert leftover == [] and inspect(engine).get_table_names() == []


def test_demo_builds_a_throwaway_database_that_works(tmp_path):
    real_env = tmp_path / ".env"
    real_env.write_text("SECRET_KEY=operator-real-secret-key-0123456789abcdef\nCOURSE_NAME=Real Course\n")
    r = subprocess_cli(tmp_path, "demo", "--db", "demo.db")
    assert r.returncode == 0, r.stderr
    assert "THROWAWAY" in r.stdout and "ENV_FILE=" in r.stdout
    assert real_env.read_text().startswith("SECRET_KEY=operator-real")  # untouched
    assert not (tmp_path / "cli.db").exists()  # never touched the configured database
    con = sqlite3.connect(tmp_path / "demo.db")
    assert con.execute("select count(*) from students").fetchone()[0] >= 10
    assert con.execute("select count(*) from attendances").fetchone()[0] > 0
    assert con.execute("select count(*) from sessions").fetchone()[0] > 1
    assert all(n.startswith("Test Student") for (n,) in con.execute("select name from students"))
    assert subprocess_cli(tmp_path, "demo", "--db", "demo.db").returncode != 0  # no clobbering

    # Boot exactly as printed (only ENV_FILE) and use a printed enrollment code and a printed login.
    sid, code = re.search(r"student ID (\d+) .*enrollment code: (\S+)", r.stdout).groups()
    lsid, lpw = re.search(r"student ID (\d+) .*password: (\S+)", r.stdout).groups()
    apw = re.search(r"username: demo-admin\s+password: (\S+)", r.stdout).group(1)
    script = (
        "from fastapi.testclient import TestClient\n"
        "from app.main import app\n"
        "c = TestClient(app)\n"
        f"print(c.post('/api/student/register', json={{'student_id': '{sid}', 'code': '{code}', 'password': 'demo-new-password'}}).status_code)\n"
        f"print(c.post('/api/student/login', json={{'student_id': '{lsid}', 'password': '{lpw}'}}).status_code)\n"
        f"print(c.post('/api/admin/login', json={{'username': 'demo-admin', 'password': '{apw}'}}).status_code)\n"
        "print(c.get('/api/config').json()['course_name'])\n"
    )
    env = {k: v for k, v in os.environ.items() if k not in ("SECRET_KEY", "DATABASE_URL", "COURSE_NAME", "TIMEZONE")}
    env.update(PYTHONPATH=REPO, ENV_FILE=str(tmp_path / ".env.demo"))
    out = subprocess.run([sys.executable, "-c", script], env=env, capture_output=True, text=True, cwd=tmp_path)
    assert out.stdout.split("\n")[:4] == ["200", "200", "200", "Demo Course"], out.stderr
    # passwords differ between runs (nothing is hard-coded)
    r2 = subprocess_cli(tmp_path, "demo", "--db", "demo.db", "--force")
    pw = lambda text: [l for l in text.splitlines() if "password:" in l]
    assert pw(r.stdout) != pw(r2.stdout)


def test_help_works_without_secret_key(tmp_path):
    env = {k: v for k, v in os.environ.items() if k != "SECRET_KEY"}
    env["PYTHONPATH"] = REPO
    r = subprocess.run([sys.executable, "-m", "app.cli", "--help"], env=env, capture_output=True,
                       text=True, cwd=tmp_path)
    assert r.returncode == 0 and "roster" in r.stdout
