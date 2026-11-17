from starlette.requests import Request
from fastapi.testclient import TestClient

from app import ratelimit
from app.main import app
from app.ratelimit import RateLimiter, client_ip

from .helpers import make_admin, make_student

PW = "hunter2-hunter2"


def login(client, sid="900000001", pw="wrong-wrong-wrong", ip=None):
    headers = {"X-Test-IP": ip} if ip else {}
    return client.post("/api/student/login", json={"student_id": sid, "password": pw}, headers=headers)


class _FromIP:
    """ASGI wrapper that sets the socket peer address."""
    def __init__(self, asgi_app, ip):
        self.app, self.ip = asgi_app, ip

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            scope = {**scope, "client": (self.ip, 50000)}
        await self.app(scope, receive, send)


def client_from(ip):
    """A TestClient whose requests appear to come from `ip` (socket peer address)."""
    return TestClient(_FromIP(app, ip))


def test_delay_grows_and_caps(cfg):
    cfg(RATE_LIMIT_FREE_FAILURES=2, RATE_LIMIT_BACKOFF_MAX_SECONDS=300)
    assert [RateLimiter.delay_for(n) for n in (1, 2, 3, 4, 5, 6)] == [0, 0, 1, 2, 4, 8]
    assert RateLimiter.delay_for(40) == 300


def test_login_backs_off_per_account_and_ip(db, rl_clock):
    make_student(db, password=PW)
    c = client_from("10.0.0.5")
    for _ in range(3):
        assert login(c).status_code == 401
    assert login(c).status_code == 429
    rl_clock(1.1)
    assert login(c).status_code == 401  # allowed again, one more failure: 2 s delay
    assert login(c).status_code == 429
    rl_clock(2.1)
    assert login(c, pw=PW).status_code == 200  # success clears it
    assert login(c).status_code == 401 and login(c).status_code == 401  # free typos again


def test_other_ips_failures_never_block_the_real_student(db):
    """The lockout-DoS scenario: an attacker hammers a victim's ID from another address."""
    make_student(db, password=PW)
    attacker = client_from("10.0.0.99")
    for _ in range(50):
        login(attacker)
    victim = client_from("10.0.0.7")
    assert login(victim, pw=PW).status_code == 200


def test_padded_ids_share_one_bucket(db):
    make_student(db, password=PW)
    c = client_from("10.0.0.5")
    for sid in ("900000001", " 900000001", "900000001 ", "\t900000001"):
        login(c, sid=sid)
    assert login(c, sid="  900000001  ").status_code == 429
    assert login(c, sid="900000001").status_code == 429
    # and padded IDs still log in correctly
    other = client_from("10.0.0.6")
    assert login(other, sid=" 900000001 ", pw=PW).status_code == 200


def test_unknown_account_spraying_is_capped_per_ip_only_for_unknown_accounts(db, cfg):
    make_student(db, password=PW)
    cfg(RATE_LIMIT_UNKNOWN_PER_IP=5)
    spray = client_from("10.0.0.50")
    codes = [login(spray, sid=f"9000009{n:02d}").status_code for n in range(8)]
    assert codes[:5] == [401] * 5 and set(codes[5:]) == {429}
    # the same IP can still use a real account with the right password
    assert login(spray, pw=PW).status_code == 200
    # and a different IP can still reach unknown IDs
    assert login(client_from("10.0.0.51"), sid="900000700").status_code == 401


def test_enrollment_guessing_backs_off(client, db, rl_clock):
    make_student(db)
    body = {"student_id": "900000001", "code": "AAAA-AAAA", "password": PW}
    assert [client.post("/api/student/register", json=body).status_code for _ in range(3)] == [400, 400, 400]
    assert client.post("/api/student/register", json=body).status_code == 429
    padded = {**body, "student_id": " 900000001 "}
    assert client.post("/api/student/register", json=padded).status_code == 429


def test_admin_login_backs_off_but_other_ip_is_fine(db, rl_clock):
    username, password = make_admin(db)
    c = client_from("10.0.0.5")
    for _ in range(3):
        c.post("/api/admin/login", json={"username": username, "password": "wrong-wrong"})
    assert c.post("/api/admin/login", json={"username": username, "password": password}).status_code == 429
    assert client_from("10.0.0.6").post("/api/admin/login", json={"username": username, "password": password}).status_code == 200


def _request(peer, xff=None):
    headers = [(b"x-forwarded-for", xff.encode())] if xff else []
    return Request({"type": "http", "client": (peer, 1), "headers": headers, "method": "GET", "path": "/"})


def test_client_ip_ignores_forwarded_header_by_default():
    assert client_ip(_request("203.0.113.9", "1.2.3.4")) == "203.0.113.9"


def test_client_ip_trusted_proxy_uses_rightmost_untrusted_hop(cfg):
    cfg(TRUSTED_PROXIES="10.0.0.0/8, 192.168.1.1")
    # an untrusted peer cannot set the header
    assert client_ip(_request("203.0.113.9", "1.2.3.4")) == "203.0.113.9"
    # the client forges the leftmost entry; the proxy appended the real address
    assert client_ip(_request("10.1.1.1", "6.6.6.6, 198.51.100.4")) == "198.51.100.4"
    assert client_ip(_request("10.1.1.1", "6.6.6.6, 198.51.100.4, 10.2.2.2")) == "198.51.100.4"
    assert client_ip(_request("10.1.1.1")) == "10.1.1.1"


def test_spoofed_forwarded_header_does_not_sidestep_backoff(db, cfg):
    make_student(db, password=PW)
    cfg(TRUSTED_PROXIES="10.0.0.1")
    proxy = client_from("10.0.0.1")
    for n in range(3):  # attacker cycles forged leftmost entries; the proxy appends the true address
        proxy.post("/api/student/login", json={"student_id": "900000001", "password": "bad-bad-bad"},
                   headers={"X-Forwarded-For": f"1.1.1.{n}, 198.51.100.4"})
    r = proxy.post("/api/student/login", json={"student_id": "900000001", "password": PW},
                   headers={"X-Forwarded-For": "1.1.1.200, 198.51.100.4"})
    assert r.status_code == 429


def test_trusted_proxies_must_parse(tmp_path):
    from .test_config import _run_import
    assert "TRUSTED_PROXIES" in _run_import(tmp_path, TRUSTED_PROXIES="not-an-ip").stderr


def test_checkin_failures_count_per_student_not_per_ip(db, rl_clock):
    """Wrong codes spread over many addresses are still slowed down (the student is authenticated)."""
    from datetime import date
    from app import models
    from .helpers import student_headers
    make_student(db, password=PW)
    session = models.Session(date=ratelimit_today(), is_test_session=True)
    db.add(session)
    db.commit()
    token = login(client_from("10.1.0.1"), pw=PW).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    codes = []
    for n in range(6):
        c = client_from(f"2001:db8::{n + 1}")
        codes.append(c.post("/api/student/attendance/mark", headers=headers,
                            json={"session_id": session.id, "token": "000000"}).status_code)
    assert codes[:3] == [403, 403, 403] and 429 in codes[3:]


def ratelimit_today():
    from app import utils
    return utils.local_today()


def test_over_the_unknown_cap_every_failed_login_is_429_but_correct_passwords_work(db, cfg):
    make_student(db, password=PW)
    cfg(RATE_LIMIT_UNKNOWN_PER_IP=3)
    c = client_from("10.0.0.60")
    for n in range(3):
        assert login(c, sid=f"90000080{n}").status_code == 401
    # known and unknown IDs now look identical
    assert login(c, sid="900000001").status_code == 429
    assert login(c, sid="900000899").status_code == 429
    assert login(c, sid="900000001", pw=PW).status_code == 200


def test_failed_attempts_log_the_client_ip(db, caplog):
    make_student(db, password=PW)
    with caplog.at_level("WARNING", logger="attendance.ratelimit"):
        login(client_from("203.0.113.77"))
    assert "client IP 203.0.113.77" in caplog.text


def test_trusted_proxies_accepts_cidr_ranges(cfg):
    cfg(TRUSTED_PROXIES="10.0.0.0/8,172.16.0.0/12,192.168.0.0/16")
    assert client_ip(_request("172.20.5.5", "198.51.100.8")) == "198.51.100.8"
    assert client_ip(_request("8.8.8.8", "198.51.100.8")) == "8.8.8.8"


def test_spraying_past_the_unknown_cap_does_not_unlock_unlimited_guessing(db, cfg):
    """R3: failures on a real account must still be recorded once the per-IP cap answers 429."""
    make_student(db, password=PW)
    username, admin_pw = make_admin(db)
    make_student(db, student_id="900000002", name="Test Student 02")
    cfg(RATE_LIMIT_UNKNOWN_PER_IP=3)
    c = client_from("10.0.0.70")
    for n in range(3):
        assert login(c, sid=f"90000090{n}").status_code == 401
        c.post("/api/admin/login", json={"username": f"nobody{n}", "password": "x-x-x-x-x"})
        c.post("/api/student/register", json={"student_id": f"90000095{n}", "code": "AAAA-AAAA", "password": PW})
    for _ in range(20):
        assert login(c, sid="900000001").status_code == 429
        assert c.post("/api/admin/login", json={"username": username, "password": "wrong-wrong"}).status_code == 429
        assert c.post("/api/student/register", json={"student_id": "900000002", "code": "BBBB-BBBB", "password": PW}).status_code == 429
    # the guesses were counted: the right secret is now held back by the per-account backoff too
    assert login(c, sid="900000001", pw=PW).status_code == 429
    assert c.post("/api/admin/login", json={"username": username, "password": admin_pw}).status_code == 429
