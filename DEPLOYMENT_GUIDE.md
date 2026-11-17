# Deployment guide

Course Attendance Tracker is one FastAPI process plus a database. It serves one class. Pick whichever
host you like; nothing here is specific to an institution.

## Before you deploy

1. **Choose a `SECRET_KEY`** (required):
   `python -c "import secrets; print(secrets.token_urlsafe(48))"`. Keep it in your host's secret
   store. Never commit it.
2. **Decide where the data lives.** The default database is a SQLite file. It needs storage that
   survives restarts (a volume or persistent disk). Without one, every deploy wipes your students and attendance.
3. **Use HTTPS.** Render and Railway provide it. On your own server, put a reverse proxy (Caddy,
   nginx) in front. Passwords and codes travel over this connection.
4. **Run a single worker.** The rate limiter keeps its counters in memory.
5. **Behind a proxy**, read "Client IPs behind Render, Railway or another proxy" below.
6. Settings are listed in the README's configuration table: `COURSE_NAME`, `TIMEZONE`,
   `CHECKIN_WINDOWS`, `GRADE_SCALE` and so on.

Rosters and code CSVs are student records. Do not put them in the image or the repository. See the Privacy section of the README.

## Docker

```bash
docker build -t course-attendance-tracker .
docker run -d --name attendance -p 8000:8000 \
  -v attendance-data:/app/data \
  -e SECRET_KEY="$(python -c 'import secrets; print(secrets.token_urlsafe(48))')" \
  -e COURSE_NAME="My Course" -e TIMEZONE="America/Chicago" \
  -e CHECKIN_WINDOWS="MON,WED,FRI 08:00-09:00" \
  course-attendance-tracker
```

(Keep the generated `SECRET_KEY` somewhere safe, or use `--env-file` with a file you keep private.
If it changes, everyone is logged out and unused enrollment codes stop working.)

The container runs `start.sh`, which creates the tables and starts the server. The SQLite file is
in the `attendance-data` volume at `/app/data/attendance.db`.

Create the admin and load the roster inside the running container:

```bash
docker exec -it attendance python -m app.cli admin set instructor
docker cp roster.csv attendance:/tmp/roster.csv
docker exec attendance python -m app.cli roster import /tmp/roster.csv
docker exec attendance python -m app.cli codes generate --all --out /tmp/codes.csv
docker cp attendance:/tmp/codes.csv ./codes.csv
docker exec attendance rm /tmp/roster.csv /tmp/codes.csv
```

Alternatively set `ADMIN_USERNAME` and `ADMIN_PASSWORD` as environment variables; `start.sh` creates
or updates that admin at every start. Issue enrollment codes from the dashboard's Enrollment Codes card.

Behind a reverse proxy, leave `TRUSTED_PROXIES` empty unless you know the proxy's address
(for example `-e TRUSTED_PROXIES=172.17.0.1` when nginx on the host reaches the container through the default Docker bridge).
Do not trust a range that clients can reach directly: they could forge `X-Forwarded-For`.

## Render

1. Push your copy of the repository to a Git host Render can read.
2. Create a Blueprint from `render.yaml` (or a Web Service with build command
   `pip install -r requirements.txt` and start command `bash start.sh`).
3. Persistent disks need a paid instance type, which is why `render.yaml` uses `plan: starter`.
   The disk is mounted at `/var/data` and `DATABASE_URL` points into it. On a free instance the
   SQLite file is lost on every deploy: use an external database instead (below).
4. In the dashboard, fill the variables marked `sync: false`: `COURSE_NAME`, `TIMEZONE`,
   `CHECKIN_WINDOWS`, `ADMIN_USERNAME`, `ADMIN_PASSWORD`. `SECRET_KEY` is generated for you.
5. Load the roster with a shell on the service (Render's Shell tab on paid plans):
   upload the file, then `python -m app.cli roster import <file>`. Delete the file afterwards.
   Or add students one batch at a time from your laptop against a Postgres `DATABASE_URL`.

## Railway

1. Create a project from your repository. `railway.json` sets the start command (`bash start.sh`) and the health check (`/health`).
2. Add a **Volume** mounted at `/data` and set `DATABASE_URL=sqlite:////data/attendance.db`.
3. Set `SECRET_KEY`, `COURSE_NAME`, `TIMEZONE`, `CHECKIN_WINDOWS`, `ADMIN_USERNAME` and `ADMIN_PASSWORD` under Variables.
4. For the roster, use `railway run` or `railway ssh` to run `python -m app.cli roster import ...`
   against the deployed volume, or issue codes from the dashboard after importing.

## Client IPs behind Render, Railway or another proxy

The rate limiter separates people by client IP. On a PaaS, requests reach the app through the platform's
proxy, so by default every user shares the proxy's address. The app is still safe (a correct password is never refused
because of failures from another IP, and attempts at real accounts only back off per account and IP), but
users then share one backoff per account, so a classmate's wrong guesses can slow your retries.

**With `TRUSTED_PROXIES` unset, every visitor shares one IP.** A classmate's wrong guesses at your account can then
delay your login by up to 5 minutes for as long as they keep failing.

To fix that, set `TRUSTED_PROXIES` (IPs or CIDR ranges) to the platform's internal proxy ranges so the app can read
`X-Forwarded-For`. For Render and Railway try the private ranges `10.0.0.0/8,172.16.0.0/12,192.168.0.0/16`.
**This was not verified on those platforms**: I could not test how they populate that header or which ranges their
proxies use. Confirm it yourself: the app logs `failed login attempt from client IP ...` for each failed attempt, so fail a
login from your phone and check that the address in the log is yours and not a private one.
Trusting a range clients can reach directly, or one that is too wide, lets them forge their IP and sidestep the limiter.
The app takes the right-most `X-Forwarded-For` entry that is not a trusted proxy, which a client cannot forge.

## Using another database

Any SQLAlchemy URL works. Install the driver in your image or environment
(for example `pip install psycopg2-binary`) and set `DATABASE_URL=postgresql+psycopg2://user:pass@host/dbname`.
Tables are created automatically on first start. There are no migrations in this release.

## Running on your own server

```bash
git clone https://github.com/xoumyax/CourseAttendanceTracker.git && cd CourseAttendanceTracker
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # then edit it
python -m app.cli init-db
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Use systemd (or similar) to keep it running, and a reverse proxy for HTTPS. Example Caddy file:

```
attendance.example.org {
    reverse_proxy 127.0.0.1:8000
}
```

Set `TRUSTED_PROXIES=127.0.0.1` in `.env` so the client IP is taken from the proxy's `X-Forwarded-For` header, and start uvicorn with `--no-proxy-headers` (the app does the header handling itself).

## Back up and clean up

- Back up the database file (or run your database's own backup) before the term gets busy. Treat backups as student records too.
- At the end of the term, export the Excel report from the dashboard, then run
  `python -m app.cli purge --confirm` and delete backups, roster files and `codes.csv`.

## Troubleshooting

| Symptom | Likely cause |
| --- | --- |
| Server exits with "Configuration error" | A required setting is missing or malformed. The message names it (`SECRET_KEY`, `TIMEZONE`, `CHECKIN_WINDOWS`, `GRADE_SCALE`). |
| "Check-in is closed" at class time | `TIMEZONE` or `CHECKIN_WINDOWS` is wrong. `GET /api/config` shows what the server believes and whether it is open now. |
| Students get 429 | Repeated wrong attempts. The delay grows to at most `RATE_LIMIT_BACKOFF_MAX_SECONDS` and clears with a correct attempt. If many unrelated students are slowed, a proxy is hiding client IPs: see "Client IPs behind Render, Railway or another proxy" above. |
| Data gone after redeploy | The SQLite file was not on persistent storage. |
