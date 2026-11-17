# Course Attendance Tracker — general-purpose release: design

## Intent

A self-hosted class attendance tracker that any instructor or TA can clone and run for their own class. Whoever clones it sets their own secrets, loads their own roster CSV, creates their own admin accounts and generates codes for their students. Nothing in the repo refers to a specific person, course or university, and no student data is ever committed.

One deployment serves one class. Stack stays as it is: FastAPI, SQLAlchemy, SQLite by default (any SQLAlchemy URL works), server-rendered templates with vanilla JS. Python 3.11+.

## Goals

1. **Everything course-specific is configuration**, read from `.env` and documented in `.env.example` and the README:
   - `SECRET_KEY` (required; startup fails if missing or a placeholder)
   - `DATABASE_URL`
   - `COURSE_NAME` (display only)
   - `TIMEZONE` (IANA name, e.g. `America/Chicago`; every window and expiry check uses it)
   - `CHECKIN_WINDOWS`: the days and times check-in is open, e.g. `MON,WED,FRI 08:00-09:00` (several windows separated by `;`; empty means always open)
   - `CHECKIN_CODE_TTL_SECONDS` (default 300) and `TEST_CODE_TTL_HOURS`
   - `GRADE_SCALE`: attendance-percentage thresholds to points, e.g. `85:10,75:8,50:5,0:0`
   - `CORS_ORIGINS` (default: none, same-origin only)
   
   Templates and JS get these values from one public endpoint (e.g. `GET /api/config`) instead of hard-coding "8 AM – 9 AM", "10 points" or a course name.

2. **One CLI**, `python -m app.cli <command>`, replacing the loose scripts:
   - `init-db`
   - `admin set <username>` (password prompted twice, or `--password-stdin`), `admin list`, `admin delete <username>`
   - `roster import <file.csv> [--format canvas|simple]`. `canvas` is a Canvas gradebook export (current parser, including its "Points Possible" row). `simple` is a CSV with `student_id,name` columns. Re-importing updates names and adds new students, without duplicating anyone.
   - `codes generate [--all | --student <id> ...] --out codes.csv`: one-time **enrollment codes**, written once to a CSV (student_id, name, code) for the instructor to hand out, and stored only as hashes. `--all` skips students who already registered unless `--reissue` is given.
   - `sessions create --date YYYY-MM-DD [--test]` and `sessions list`
   - `demo`: a throwaway database with a fake roster, sessions and attendance, printing demo logins. All fake data is clearly fake.
   
   `set_admin.py`, `seed_students.py`, `seed_sessions.py`, `seed_test_students.py`, `import_students_csv.py` and `init_db.py` are folded into the CLI and removed. `start.sh`, the Dockerfile, Procfile, render.yaml and railway.json call the CLI.

3. **Student accounts are protected by enrollment codes.**
   - Registration needs the student ID **and** a valid, unused enrollment code. The code is consumed when it is used.
   - Password reset needs a fresh enrollment code issued by an admin, through the CLI or a "reissue code" button in the dashboard. The current reset, which accepts ID + name, is removed, because any classmate could use it to take over an account.
   - The admin dashboard can generate and download enrollment codes for one student or for everyone not yet registered.

4. **Check-in codes are hard to guess and to abuse.**
   - Codes come from `secrets`, not `random`.
   - Login and check-in attempts are rate-limited per student and per IP. A simple in-memory limiter is enough; document that it resets on restart.
   - Expiry and window checks use timezone-aware datetimes in `TIMEZONE`. Stored times are UTC.
   - Duplicate check-ins are refused, as now.

5. **Tests and CI.**
   - A pytest suite that uses FastAPI's TestClient and a temporary database, covering:
     - the admin CLI;
     - roster import, both formats, using fake fixture CSVs under `tests/fixtures/`;
     - registration with valid, used and wrong enrollment codes;
     - login;
     - check-in: valid, expired, wrong code, duplicate, outside the window, and rate limiting;
     - the grade scale;
     - the Excel export.
   - A GitHub Actions workflow runs it on push and on pull requests.

6. **Docs for a stranger.**
   - **README:** what it does; a quick start in about 10 commands (clone, venv, install, `.env` with generated `SECRET_KEY`, `init-db`, `admin set`, `roster import`, `codes generate`, run); a configuration table; both CSV formats with fake examples; the student flow; and a privacy section. The privacy section says that rosters and code CSVs are student records, must never be committed, and are git-ignored; that the operator is responsible for their institution's data rules (e.g. FERPA in the US); and how to delete data at the end of term (`python -m app.cli purge --confirm` drops the database).
   - **`DEPLOYMENT_GUIDE.md`:** generic (Docker, Render, Railway), with no institution-specific sections.
   - **`LICENSE`:** MIT, copyright "Course Attendance Tracker contributors".

## Non-goals

- Several courses in one deployment, SSO/CAS, LMS sync, or email delivery of codes.
- A frontend framework rewrite.

## Anti-goals

- Any real name, student ID, course code, institution, password or absolute local path in the repo, including tests, fixtures, docs and commit messages.
- Any default credential that works out of the box. The demo command makes its own throwaway database and says so.

## Done means

- `pytest` passes locally and in CI.
- A fresh clone runs by following only the README.
- A grep of the tracked files for the original course code, institution name and local filesystem paths finds nothing, and no `*.csv` is tracked except the fake fixtures.
