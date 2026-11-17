# Course Attendance Tracker

A simple app to replace paper-based attendance tracking for courses. Self-deploy and enjoy!

It is a self-hosted class attendance tracker. You run it for your own course: set a few
values in `.env`, load your roster, hand each student a one-time enrollment code,
and show a short check-in code in class. Students check in from their phones; you
get live percentages, grade points from your own scale, and an Excel export.

One deployment serves one class. Built with FastAPI, SQLAlchemy (SQLite by default,
any SQLAlchemy URL works), server-rendered templates and plain JavaScript.

## How it works

- **Enrollment codes.** Each student gets a one-time code. Student ID plus code lets them
  register (set a password). A forgotten password is reset the same way, with a fresh code
  that you issue. Codes are stored only as hashes.
- **Check-in codes.** During class you generate a 6-digit code on the admin dashboard. It
  expires after a few minutes (configurable). Students enter it on their phone.
- **Check-in windows.** You can restrict check-in to class times, e.g. `MON,WED,FRI 08:00-09:00`.
- **Grades.** Attendance percentage maps to points with a scale you define.

## Requirements

- **Python 3.11, 3.12 or 3.13.** Python 3.14 and newer cannot install the pinned packages yet. Check with
  `python3 --version`; if your default is newer, create the environment with a supported one, e.g. `python3.12 -m venv .venv`.
- **git** and **pip** (pip comes with Python).
- macOS, Linux or Windows. No other services: the default database is a local SQLite file.
- Optional: **Docker**, if you would rather run it in a container (see [DEPLOYMENT_GUIDE.md](DEPLOYMENT_GUIDE.md)).

Python packages are listed in `requirements.txt` (pinned versions) and installed in the quick start:

| Package | Used for |
| --- | --- |
| `fastapi`, `uvicorn[standard]` | Web framework and server |
| `sqlalchemy` | Database access |
| `pydantic`, `pydantic-settings` | Request validation and reading `.env` |
| `python-jose[cryptography]` | Login tokens (JWT) |
| `passlib[bcrypt]`, `bcrypt` | Password hashing |
| `python-multipart` | Form uploads |
| `pandas`, `openpyxl` | Roster import and the Excel export |
| `Jinja2` | Page templates |
| `tzdata` | Time zone data on systems without it (Windows) |

`requirements-dev.txt` adds `pytest` and `httpx` for the tests.

## Quick start

```bash
git clone https://github.com/xoumyax/CourseAttendanceTracker.git && cd CourseAttendanceTracker
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
sed -i.bak "s|^SECRET_KEY=.*|SECRET_KEY=$(python -c 'import secrets; print(secrets.token_urlsafe(48))')|" .env && rm .env.bak
python -m app.cli init-db
python -m app.cli admin set instructor            # prompts for a password twice
python -m app.cli roster import tests/fixtures/roster_simple.csv   # use your own roster file here
python -m app.cli codes generate --all --out codes.csv
python -m uvicorn app.main:app
```

Open <http://localhost:8000/admin/login> and sign in as the admin you created. Students
register at <http://localhost:8000/student/register> using a line from `codes.csv`.
Edit `.env` to set your course name, timezone and check-in windows (see below), then restart.

The example roster above is fake. To just look around first, run
`python -m app.cli demo` and follow what it prints: it builds a throwaway database (`demo.db`) with
its own settings file (`.env.demo`), fake students and random logins, and never touches your `.env` or database.

On Windows, activate the environment with `.venv\Scripts\activate`.

## Configuration

Settings come from environment variables or a `.env` file. `.env.example` has every one.

| Variable | Default | Meaning |
| --- | --- | --- |
| `SECRET_KEY` | none, required | Signs login tokens and hashes enrollment codes. The app will not start if it is empty, `change-me`, or shorter than 32 characters. Changing it logs everyone out and invalidates unused enrollment codes. |
| `DATABASE_URL` | `sqlite:///./attendance.db` | Any SQLAlchemy URL. Other databases need their own driver installed. |
| `COURSE_NAME` | `My Course` | Display name in page titles and headings. |
| `TIMEZONE` | `UTC` | IANA name such as `America/Chicago`. Windows, "today" and displayed times use it. Stored times are UTC. |
| `CHECKIN_WINDOWS` | empty (always open) | Days and times check-in is open: `MON,WED,FRI 08:00-09:00`. Separate several windows with `;`. Days can be a range, `MON-FRI`. End time is exclusive. |
| `CHECKIN_CODE_TTL_SECONDS` | `300` | How long a check-in code works. |
| `TEST_CODE_TTL_HOURS` | `24` | Same, for test sessions. |
| `GRADE_SCALE` | `85:10,75:8,50:5,0:0` | `<percent>:<points>` pairs. A student gets the points of the highest threshold they reach. |
| `CORS_ORIGINS` | empty | Comma-separated origins allowed to call the API from other sites. Empty means same-origin only, which the bundled pages need. |
| `ACCESS_TOKEN_EXPIRE_HOURS` | `8` | Login session length. |
| `RATE_LIMIT_FREE_FAILURES` | `2` | Wrong attempts per (account, IP) before delays start. |
| `RATE_LIMIT_BACKOFF_MAX_SECONDS` | `300` | Delays double from 1 s up to this. |
| `RATE_LIMIT_UNKNOWN_PER_IP` | `60` | Failed attempts at non-existent accounts per IP per window. |
| `RATE_LIMIT_WINDOW_SECONDS` | `60` | Window for the unknown-account cap. |
| `TRUSTED_PROXIES` | empty | Proxy IPs/CIDRs whose `X-Forwarded-For` is believed. See Rate limiting. |
| `HOST`, `PORT` | `0.0.0.0`, `8000` | Used by `run.py` and the container. |

The pages read the public values (course name, windows, code lifetime, grade scale) from
`GET /api/config`, so nothing is hard-coded in the templates.

### Rate limiting

Wrong passwords, wrong enrollment codes and wrong check-in codes are slowed down, not locked out:

- Failures are counted per **(account, client IP)**. After `RATE_LIMIT_FREE_FAILURES` (2) wrong
  attempts the next try from that IP must wait 1 s, then 2 s, 4 s and so on, up to
  `RATE_LIMIT_BACKOFF_MAX_SECONDS` (300). The server answers HTTP 429 with `Retry-After`.
  A success clears the delay.
- Someone else's failures from a **different IP** never block you, even with your correct password.
- A separate, looser cap (`RATE_LIMIT_UNKNOWN_PER_IP` per `RATE_LIMIT_WINDOW_SECONDS`) counts failed
  attempts at IDs or usernames that do not exist. Once an IP is over it, every *failed* login from that IP
  gets 429, whether or not the ID exists, so the responses do not reveal which IDs are real. A correct password
  still succeeds.
- Check-in failures are counted per student only (check-in needs a login, so only that student can
  trigger them).
- Trade-off: two people who share an IP (a campus network) can slow each other's retries on the
  same account. Setting up `TRUSTED_PROXIES` correctly matters behind a proxy.

Counters live in server memory: they reset on restart and are not shared between workers, so run one worker.

**Behind a platform proxy with `TRUSTED_PROXIES` unset, every visitor shares one IP.** Then a classmate's wrong
guesses at your account delay your login too, by up to 5 minutes for as long as they keep failing. Set
`TRUSTED_PROXIES` (it accepts IPs and CIDR ranges) to the platform's internal proxy ranges. For Render and Railway
that is probably the private ranges `10.0.0.0/8,172.16.0.0/12,192.168.0.0/16`, but **this was not verified on
those platforms**. Confirm it: the server logs `failed login attempt from client IP ...` on each failed attempt;
fail a login yourself from your phone and check that the logged address is yours, not a private one.

**`TRUSTED_PROXIES`.** By default the client IP is the socket peer address. Behind a reverse proxy
every request then appears to come from the proxy, so the per-IP part stops separating people. Set
`TRUSTED_PROXIES` to the proxy's address or CIDR (comma-separated) and the server will read the
right-most `X-Forwarded-For` entry that is not a trusted proxy. Never trust a range that clients can reach directly,
because they could forge the header. Windows are listed as `start-end` within one day; a window cannot cross midnight.

Test sessions ignore the check-in windows and use the long code lifetime, and are not graded.
The dashboard also has a "Disable Time Restrictions" switch for trying things out.

## Command line

`python -m app.cli <command>`. Run it from the repository folder, with `.env` there.

| Command | What it does |
| --- | --- |
| `init-db` | Creates the tables. Safe to repeat. |
| `admin set <username>` | Creates an admin or changes its password. Prompts twice, or add `--password-stdin` to read one line from stdin. |
| `admin list` / `admin delete <username>` | List or remove admins. |
| `roster import <file.csv> [--format canvas\|simple]` | Adds new students and updates names of existing ones. Nobody is duplicated. Default format is `simple`. |
| `codes generate --all --out codes.csv` | One enrollment code for every student who has not registered, written to a CSV. Students who already hold an unused code are skipped (a new code would void it), and the count is printed. |
| `codes generate --student <id> [<id> ...] --out codes.csv` | Codes for specific students. For a registered student this is a password-reset code. |
| `codes generate --all --reissue ...` | Include students who already registered and replace unused codes, voiding the old ones. |
| `sessions create --date YYYY-MM-DD [--test]` | Adds a session. `--until YYYY-MM-DD` also adds one on every later date up to that day that falls on a `CHECKIN_WINDOWS` day. |
| `sessions list` | Lists sessions. |
| `demo [--db demo.db]` | Builds a throwaway database with fake students, sessions and attendance, and prints random demo logins. |
| `purge --confirm` | Drops every table (any database), reports how many tables and rows went, and deletes the SQLite file. See Privacy. |

`codes generate` refuses to overwrite an existing file (codes cannot be read back from the
database); pass `--force` to replace it.

## Roster files

**simple**: two columns.

```csv
student_id,name
900000001,Test Student 01
900000002,Test Student 02
```

**canvas**: a Canvas gradebook export. The `Student` and `SIS User ID` columns are used; the
"Points Possible" row and Canvas's "Student, Test" row are skipped, as are rows with no ID.

```csv
Student,ID,SIS User ID,SIS Login ID,Section
    Points Possible,,,,
"Student, Test",9999,,,Test Section
"Student, Alpha",1001,900000001,test01,Section A
```

Import with `python -m app.cli roster import roster.csv --format canvas`. The database column
is still called `uin` for historical reasons; everything user-facing says "student ID".

## Student flow

1. The instructor imports the roster and issues enrollment codes (CLI, or the Enrollment Codes
   card on the dashboard), then hands each student their code privately.
2. The student opens `/student/register`, enters student ID, code and a password
   (8 to 72 characters), and logs in at `/`.
3. In class, the instructor opens the dashboard, picks today's session and presses
   "Generate Check-in Code".
4. The student opens `/attendance`, picks the session, types the code. Duplicates are refused.
5. Forgot password: the instructor issues a new code (CLI, or "Reissue code" next to the student in
   the dashboard sidebar). The student uses `/student/reset-password`. Name and ID alone never reset anything.

There is a student guide at `/student/guide` that shows your own windows and grade scale.

## Privacy

- **Rosters and code files are student records.** Never commit them. `.gitignore` excludes
  `*.csv` (except the fake files under `tests/fixtures/`), `rosters/` and `*.db`. Keep your
  roster outside the repository if you can, and delete `codes.csv` after handing the codes out.
- You are responsible for following your institution's data rules (for example FERPA in the
  United States) and for who can reach your server and backups.
- The database holds student IDs, names, password hashes, enrollment code hashes and attendance times.
  No plain-text passwords or codes are stored.
- **End of term:** export what you need (the dashboard's Excel report), then run
  `python -m app.cli purge --confirm`. It drops all tables and deletes the SQLite file. There is no undo.
  Also delete `codes.csv`, your roster files and any database backups.

## Known limitations

- Resetting a password or changing an admin password does not revoke login tokens already issued (they last up to 8 hours).
- Two simultaneous registrations with the same code can both succeed; the last password wins.
- Enrollment codes do not expire.
- Check-in windows cannot cross midnight and `24:00` is not accepted as an end time.
- The interactive API docs (`/docs`, `/openapi.json`) are public.
- Dependencies (`python-jose`, `starlette`, `python-multipart`) are pinned old versions and have not been audited or bumped.
- The Dockerfile runs the app as root.
- `run.py` is a leftover dev launcher (auto-reload); use `uvicorn` as shown above.
- A login token is not checked against the student still existing, so after a purge and re-import with the same `SECRET_KEY` an old token could map to a different student.
- There are no database migrations; an older database will not gain new columns.

## Tests

```bash
pip install -r requirements-dev.txt
python -m pytest
```

The suite uses FastAPI's TestClient and a temporary database. GitHub Actions runs it on every push and pull request.

## Deployment

See [DEPLOYMENT_GUIDE.md](DEPLOYMENT_GUIDE.md) for Docker, Render and Railway.

## License

MIT, see [LICENSE](LICENSE).
