"""
Command line interface:  python -m app.cli <command>

  init-db                                     create the tables
  admin set|list|delete                       manage admin accounts
  roster import FILE [--format canvas|simple] load students from a CSV
  codes generate ...                          one-time enrollment codes -> CSV
  sessions create|list                        class sessions
  demo                                        throwaway database with fake data
  purge --confirm                             delete the database

App modules are imported inside the commands, so `--help` works before SECRET_KEY is set.
"""
import argparse
import csv
import getpass
import os
import secrets
import sys
from datetime import date, datetime, timedelta
from pathlib import Path


def fail(message: str, code: int = 1):
    print(f"Error: {message}", file=sys.stderr)
    sys.exit(code)


def _load_models():
    """Import the models so Base.metadata knows every table (needed by create_all and drop_all)."""
    from . import models  # noqa: F401
    from .database import Base
    return Base


def _open_db():
    from .database import SessionLocal, engine
    _load_models().metadata.create_all(bind=engine)
    return SessionLocal()


def _read_password(from_stdin: bool) -> str:
    if from_stdin:
        password = sys.stdin.readline().rstrip("\r\n")
    else:
        password = getpass.getpass("Password: ")
        if password != getpass.getpass("Confirm password: "):
            fail("Passwords do not match.")
    if len(password) < 8:
        fail("Password must be at least 8 characters.")
    if len(password.encode("utf-8")) > 72:
        fail("Password must be at most 72 bytes (bcrypt limit).")
    return password


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def cmd_init_db(args):
    from .config import settings  # noqa: F401  (validates configuration)
    from sqlalchemy import inspect
    from .database import engine
    Base = _load_models()
    Base.metadata.create_all(bind=engine)
    present = set(inspect(engine).get_table_names())
    missing = sorted(set(Base.metadata.tables) - present)
    if missing:
        fail(f"tables were not created: {', '.join(missing)}")
    print(f"Database ready ({engine.url.render_as_string(hide_password=True)}): "
          f"{len(Base.metadata.tables)} tables.")


def cmd_admin(args):
    from .auth import hash_password
    from .models import Admin
    db = _open_db()
    try:
        if args.admin_cmd == "list":
            for admin in db.query(Admin).order_by(Admin.username):
                print(admin.username)
        elif args.admin_cmd == "delete":
            admin = db.query(Admin).filter(Admin.username == args.username).first()
            if admin is None:
                fail(f"No admin named '{args.username}'.")
            db.delete(admin)
            db.commit()
            print(f"Deleted admin '{args.username}'.")
        else:  # set
            username = args.username.strip()
            if not username:
                fail("Username must not be empty.")
            password = _read_password(args.password_stdin)
            admin = db.query(Admin).filter(Admin.username == username).first()
            if admin is None:
                db.add(Admin(username=username, hashed_password=hash_password(password)))
                print(f"Created admin '{username}'.")
            else:
                admin.hashed_password = hash_password(password)
                print(f"Updated password for admin '{username}'.")
            db.commit()
    finally:
        db.close()


def cmd_roster(args):
    from . import roster
    db = _open_db()
    try:
        try:
            result = roster.import_roster(db, args.file, args.format)
        except (OSError, ValueError) as exc:
            fail(str(exc))
        print(f"Roster imported: {result.added} added, {result.updated} renamed, "
              f"{result.unchanged} unchanged, {result.skipped} skipped (missing ID or name).")
    finally:
        db.close()


def _write_codes_csv(path: str, rows):
    """Write the CSV (owner-only permissions) to `path`."""
    from .utils import spreadsheet_safe
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["student_id", "name", "code"])
        writer.writerows([[sid, spreadsheet_safe(name), code] for sid, name, code in rows])


def cmd_codes(args):
    from . import enrollment
    if bool(args.all) == bool(args.student):
        fail("Give exactly one of --all or --student <id> [<id> ...].")
    if os.path.exists(args.out) and not args.force:
        fail(f"{args.out} already exists. Codes can't be read back later, so pick another "
             "file name or pass --force to overwrite it.")
    db = _open_db()
    tmp_path = None
    try:
        plan = enrollment.plan_codes(
            db, student_ids=args.student, everyone=args.all,
            include_registered=args.reissue, replace_outstanding=args.reissue,
        )
        for sid in plan.unknown:
            print(f"Warning: no student with ID {sid}; skipped.", file=sys.stderr)
        if plan.skipped_outstanding:
            print(f"Skipped {len(plan.skipped_outstanding)} student(s) who already hold an unused code "
                  "(a new code would void it). Use --reissue to replace those codes.", file=sys.stderr)
        if not plan.students:
            print("No codes issued.")
            return
        # Order matters: write the file first, commit the new hashes only once it exists, then
        # rename into place. A failure never voids codes that nobody received.
        rows = enrollment.apply_plan(plan)
        tmp_path = f"{args.out}.tmp-{os.getpid()}"
        _write_codes_csv(tmp_path, rows)
        db.commit()
        os.replace(tmp_path, args.out)
        tmp_path = None
    except OSError as exc:
        db.rollback()
        fail(f"could not write {args.out}: {exc}. No codes were changed.")
    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.unlink(tmp_path)
        db.close()
    print(f"Wrote {len(rows)} codes to {args.out}. They are stored only as hashes and cannot "
          "be shown again. This file is a student record: do not commit or share it.")


def cmd_sessions(args):
    from . import models, utils
    db = _open_db()
    try:
        if args.sessions_cmd == "list":
            for s in db.query(models.Session).order_by(models.Session.date, models.Session.id):
                print(f"{s.id}\t{s.date.isoformat()}\t{'test' if s.is_test_session else 'regular'}")
            return
        try:
            start = date.fromisoformat(args.date)
            end = date.fromisoformat(args.until) if args.until else start
        except ValueError:
            fail("Dates must look like YYYY-MM-DD.")
        if end < start:
            fail("--until must not be before --date.")
        windows = utils.settings.windows
        created = skipped = 0
        day = start
        while day <= end:
            wanted = day == start or not windows or any(day.weekday() in w.days for w in windows)
            if wanted:
                exists = db.query(models.Session).filter(
                    models.Session.date == day,
                    models.Session.is_test_session == args.test,
                ).first()
                if exists and not args.test:
                    skipped += 1
                else:
                    db.add(models.Session(date=day, is_test_session=args.test))
                    created += 1
            day += timedelta(days=1)
        db.commit()
        print(f"Created {created} {'test ' if args.test else ''}session(s), "
              f"skipped {skipped} that already existed.")
    finally:
        db.close()


def drop_all_tables(engine):
    """Drop every app table. Returns ([(table, rows), ...], leftover_table_names)."""
    from sqlalchemy import func, inspect, select
    Base = _load_models()
    present = set(inspect(engine).get_table_names())
    removed = []
    with engine.connect() as conn:
        for table in Base.metadata.sorted_tables:
            if table.name in present:
                removed.append((table.name, conn.execute(select(func.count()).select_from(table)).scalar()))
    Base.metadata.drop_all(bind=engine)
    leftover = sorted(set(inspect(engine).get_table_names()) & set(Base.metadata.tables))
    return removed, leftover


def cmd_purge(args):
    from .database import engine
    url = engine.url
    target = url.render_as_string(hide_password=True)
    if not args.confirm:
        fail(f"This deletes ALL data (students, codes, attendance, admins) in {target}. "
             "Run again with --confirm to proceed.", code=2)
    removed, leftover = drop_all_tables(engine)
    if leftover:
        fail(f"tables still present after purge: {', '.join(leftover)}")
    engine.dispose()
    if url.get_backend_name() == "sqlite" and url.database and url.database != ":memory:":
        for suffix in ("", "-journal", "-wal", "-shm"):
            Path(url.database + suffix).unlink(missing_ok=True)
    rows = sum(n for _, n in removed)
    print(f"Purged {target}: dropped {len(removed)} tables holding {rows} rows.")


def cmd_demo(args):
    db_path = Path(args.db).resolve()
    if db_path.exists():
        if not args.force:
            fail(f"{args.db} already exists. Delete it or pass --force to replace it.")
        db_path.unlink()
    # The demo has its own settings file, so it never reads or changes your real .env or database.
    # Everything is set BEFORE any app module is imported.
    env_path = db_path.with_name(".env.demo")
    demo_env = {
        "SECRET_KEY": secrets.token_urlsafe(48),
        "DATABASE_URL": f"sqlite:///{db_path}",
        "COURSE_NAME": "Demo Course",
        "TIMEZONE": "UTC",
        "CHECKIN_WINDOWS": "",
    }
    fd = os.open(env_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write("# Throwaway settings for the demo database. Safe to delete.\n")
        handle.writelines(f"{k}={v}\n" for k, v in demo_env.items())
    os.environ.update(demo_env)
    os.environ["ENV_FILE"] = str(env_path)

    from . import enrollment, models, utils
    from .auth import hash_password
    from .database import Base, SessionLocal, engine
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        admin_pw = secrets.token_urlsafe(9)
        db.add(models.Admin(username="demo-admin", hashed_password=hash_password(admin_pw)))

        students = [
            models.Student(uin=str(900000000 + i), name=f"Test Student {i:02d}", hashed_password="")
            for i in range(1, 13)
        ]
        db.add_all(students)
        logins = []
        for student in students[:2]:
            pw = secrets.token_urlsafe(9)
            student.hashed_password = hash_password(pw)
            student.is_registered = True
            logins.append((student.uin, student.name, pw))

        today = utils.local_today()
        past = [today - timedelta(days=7 * n) for n in range(8, 0, -1)]
        sessions = [models.Session(date=d, is_test_session=False) for d in past]
        test_session = models.Session(date=today, is_test_session=True)
        db.add_all(sessions + [test_session])
        db.flush()

        # Fake history: student 1 attends nearly always, student 2 about half, student 3 rarely.
        for student, step in zip(students[:3], (1, 2, 4)):
            for session in sessions[::step]:
                db.add(models.Attendance(student_id=student.id, session_id=session.id))

        test_session_id = test_session.id
        code = utils.generate_checkin_code()
        db.add(models.SessionToken(
            session_id=test_session.id, token=code, is_active=True,
            expires_at=utils.calculate_code_expiry(True),
        ))
        unregistered = [(s.uin, s.name, enrollment.issue_code(s)) for s in students[2:5]]
        db.commit()
    finally:
        db.close()

    print(f"""
Demo database created: {db_path}
THIS IS A THROWAWAY DATABASE WITH FAKE DATA. Delete the file when done.
The passwords and codes below are random and exist only in this database.

Start the server against it (the demo's own settings are in {env_path}):
    ENV_FILE={env_path} python -m uvicorn app.main:app

Admin login (/admin/login):
    username: demo-admin    password: {admin_pw}

Registered demo students (/):""")
    for sid, name, pw in logins:
        print(f"    student ID {sid} ({name})    password: {pw}")
    print("\nUnregistered demo students (register at /student/register with the code):")
    for sid, name, enrol in unregistered:
        print(f"    student ID {sid} ({name})    enrollment code: {enrol}")
    print(f"\nToday's test session (session id {test_session_id}) accepts check-in code {code} "
          f"for {utils.settings.TEST_CODE_TTL_HOURS} hours.")


# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m app.cli", description="Course Attendance Tracker admin tool.")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init-db", help="create the database tables").set_defaults(func=cmd_init_db)

    admin = sub.add_parser("admin", help="manage admin accounts")
    admin_sub = admin.add_subparsers(dest="admin_cmd", required=True)
    a_set = admin_sub.add_parser("set", help="create an admin or change its password")
    a_set.add_argument("username")
    a_set.add_argument("--password-stdin", action="store_true",
                       help="read the password from the first line of stdin instead of prompting")
    admin_sub.add_parser("list", help="list admin usernames")
    a_del = admin_sub.add_parser("delete", help="delete an admin")
    a_del.add_argument("username")
    admin.set_defaults(func=cmd_admin)

    roster = sub.add_parser("roster", help="import a roster")
    roster_sub = roster.add_subparsers(dest="roster_cmd", required=True)
    r_imp = roster_sub.add_parser("import", help="import students from a CSV file")
    r_imp.add_argument("file")
    r_imp.add_argument("--format", choices=("canvas", "simple"), default="simple",
                       help="simple = student_id,name columns (default); canvas = Canvas gradebook export")
    roster.set_defaults(func=cmd_roster)

    codes = sub.add_parser("codes", help="enrollment codes")
    codes_sub = codes.add_subparsers(dest="codes_cmd", required=True)
    c_gen = codes_sub.add_parser("generate", help="issue one-time codes and write them to a CSV")
    c_gen.add_argument("--all", action="store_true", help="every student who has not registered yet")
    c_gen.add_argument("--student", nargs="+", metavar="ID", help="specific student IDs")
    c_gen.add_argument("--reissue", action="store_true",
                       help="replace codes that were already issued and not used (voiding them); "
                            "with --all, also cover students who already registered")
    c_gen.add_argument("--out", required=True, help="CSV file to write (student_id, name, code)")
    c_gen.add_argument("--force", action="store_true", help="overwrite --out if it exists")
    codes.set_defaults(func=cmd_codes)

    sessions = sub.add_parser("sessions", help="class sessions")
    sessions_sub = sessions.add_subparsers(dest="sessions_cmd", required=True)
    s_new = sessions_sub.add_parser("create", help="create a session")
    s_new.add_argument("--date", required=True, help="YYYY-MM-DD")
    s_new.add_argument("--until", help="YYYY-MM-DD: also create one on every later date up to this "
                                       "one that falls on a CHECKIN_WINDOWS day")
    s_new.add_argument("--test", action="store_true", help="test session (not graded, no time window)")
    sessions_sub.add_parser("list", help="list sessions")
    sessions.set_defaults(func=cmd_sessions)

    demo = sub.add_parser("demo", help="create a throwaway database with fake data")
    demo.add_argument("--db", default="demo.db", help="file to create (default: demo.db)")
    demo.add_argument("--force", action="store_true", help="replace the file if it exists")
    demo.set_defaults(func=cmd_demo)

    purge = sub.add_parser("purge", help="delete the whole database (end-of-term cleanup)")
    purge.add_argument("--confirm", action="store_true", help="required; there is no undo")
    purge.set_defaults(func=cmd_purge)
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
