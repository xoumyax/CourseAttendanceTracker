"""
Rate limiting for failed login, enrollment and check-in attempts (in memory).

Design, chosen so that nobody can lock a classmate out:

* Failures are tracked per (account, client IP). After a couple of free typos each further
  failure doubles the wait before that same account is tried again from that same IP
  (1 s, 2 s, 4 s ... capped at RATE_LIMIT_BACKOFF_MAX_SECONDS). A correct password from a
  different IP is never affected by someone else's failures, and a success clears the delay.
* A much looser per-IP cap applies only to failed attempts on accounts that do not exist
  (RATE_LIMIT_UNKNOWN_PER_IP per window). Requests for real accounts ignore it.

Counters live in this process: they reset on restart and are not shared between workers.
"""
import ipaddress
import logging
import threading
import time
from collections import deque
from typing import Callable, Deque, Dict, Tuple

from fastapi import HTTPException, Request

from .config import settings


class RateLimiter:
    def __init__(self, clock: Callable[[], float] = time.monotonic):
        self._clock = clock
        self._backoff: Dict[str, Tuple[int, float]] = {}  # key -> (failures, last failure time)
        self._unknown: Dict[str, Deque[float]] = {}
        self._lock = threading.Lock()

    # -- per (account, ip) backoff ------------------------------------------
    @staticmethod
    def delay_for(failures: int) -> float:
        over = failures - settings.RATE_LIMIT_FREE_FAILURES
        if over <= 0:
            return 0.0
        return float(min(2 ** (over - 1), settings.RATE_LIMIT_BACKOFF_MAX_SECONDS))

    def wait_needed(self, key: str) -> float:
        """Seconds the caller must still wait before trying `key` again (0 = go ahead)."""
        with self._lock:
            entry = self._backoff.get(key)
            if entry is None:
                return 0.0
            failures, last = entry
            now = self._clock()
            if now - last > 2 * settings.RATE_LIMIT_BACKOFF_MAX_SECONDS:  # forgotten after a long quiet spell
                del self._backoff[key]
                return 0.0
            return max(0.0, last + self.delay_for(failures) - now)

    def record_failure(self, key: str) -> None:
        with self._lock:
            if len(self._backoff) > 10000:
                cutoff = self._clock() - 2 * settings.RATE_LIMIT_BACKOFF_MAX_SECONDS
                self._backoff = {k: v for k, v in self._backoff.items() if v[1] > cutoff}
            failures = self._backoff.get(key, (0, 0.0))[0] + 1
            self._backoff[key] = (failures, self._clock())

    def clear(self, key: str) -> None:
        with self._lock:
            self._backoff.pop(key, None)

    # -- per-IP cap on unknown accounts ---------------------------------------
    def _recent_unknown(self, key: str) -> Deque[float]:
        events = self._unknown.setdefault(key, deque())
        cutoff = self._clock() - settings.RATE_LIMIT_WINDOW_SECONDS
        while events and events[0] <= cutoff:
            events.popleft()
        if not events:
            del self._unknown[key]
            return deque()
        return events

    def unknown_count(self, key: str) -> int:
        with self._lock:
            return len(self._recent_unknown(key))

    def record_unknown(self, key: str) -> None:
        with self._lock:
            self._recent_unknown(key)
            self._unknown.setdefault(key, deque()).append(self._clock())

    def reset(self) -> None:
        with self._lock:
            self._backoff.clear()
            self._unknown.clear()


limiter = RateLimiter()
log = logging.getLogger("attendance.ratelimit")


def client_ip(request: Request) -> str:
    """
    The client address. Uses the socket peer unless that peer is in TRUSTED_PROXIES; then the
    right-most X-Forwarded-For entry that is not itself a trusted proxy (the client cannot forge
    that one, only entries further left).
    """
    peer = request.client.host if request.client else "unknown"
    trusted = settings.trusted_proxies

    def is_trusted(addr: str) -> bool:
        try:
            ip = ipaddress.ip_address(addr)
        except ValueError:
            return False
        return any(ip in net for net in trusted)

    if not trusted or not is_trusted(peer):
        return peer
    hops = [h.strip() for h in request.headers.get("x-forwarded-for", "").split(",") if h.strip()]
    for hop in reversed(hops):
        if not is_trusted(hop):
            return hop
    return hops[0] if hops else peer


def _retry(wait: float) -> HTTPException:
    seconds = max(1, int(wait + 0.999))
    return HTTPException(
        status_code=429,
        detail=f"Too many failed attempts. Wait {seconds} second{'s' if seconds != 1 else ''} and try again.",
        headers={"Retry-After": str(seconds)},
    )


# Check-in is authenticated, so only the student themselves can fail it: count per student,
# not per IP (otherwise wrong codes spread over many addresses would never be slowed).
PER_ACCOUNT_ONLY = {"checkin"}


def _key(request: Request, scope: str, account: str) -> str:
    if scope in PER_ACCOUNT_ONLY:
        return f"{scope}|{account}"
    return f"{scope}|{account}|{client_ip(request)}"


def guard(request: Request, scope: str, account: str) -> None:
    """Raise 429 if this account was tried (and failed) too recently from this IP."""
    wait = limiter.wait_needed(_key(request, scope, account))
    if wait > 0:
        raise _retry(wait)


def fail(request: Request, scope: str, account: str, known: bool = True) -> None:
    """Record a failed attempt. Unknown accounts also count against the looser per-IP cap."""
    limiter.record_failure(_key(request, scope, account))
    # The client IP as the app sees it. Check this once behind a proxy: it should be the
    # visitor's address, not the proxy's (see TRUSTED_PROXIES in the README).
    log.warning("failed %s attempt from client IP %s", scope, client_ip(request))
    if not known:
        limiter.record_unknown(f"{scope}|unknown|{client_ip(request)}")


def guard_unknown(request: Request, scope: str, counted: bool = False) -> None:
    """
    Call on EVERY failed attempt, known account or not: once this IP has failed too often at
    non-existent accounts it gets 429 for all failures, so responses do not reveal which IDs
    exist. A correct password never reaches this check.
    """
    ip_key = f"{scope}|unknown|{client_ip(request)}"
    # Called after fail() has recorded this attempt. `counted` says whether that attempt was itself
    # an unknown-account failure (and so is already in the count); subtract it so the cap means
    # "N earlier unknown-account failures from this IP" for every caller.
    earlier = limiter.unknown_count(ip_key) - (1 if counted else 0)
    if earlier >= settings.RATE_LIMIT_UNKNOWN_PER_IP:
        raise _retry(settings.RATE_LIMIT_WINDOW_SECONDS)


def succeed(request: Request, scope: str, account: str) -> None:
    limiter.clear(_key(request, scope, account))
