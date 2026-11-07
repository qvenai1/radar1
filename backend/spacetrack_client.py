"""
Space-Track.org authenticated client — S2a FOUNDATION ONLY.

Establishes the authenticated, rate-limited connection scraplan will later use
to ingest decay/reentry (TIP) data (S2c). This module is the connection layer:
auth + session reuse + strict rate limiting + resilient backoff + Alpha-5
handling + a proof-of-connection. It does NOT ingest or persist anything.

Verified against the LIVE Space-Track documentation (read 2026-07-18):

  AUTH (cookie session):
    POST {base}/ajaxauth/login   form fields: identity=<user>, password=<pass>
      -> HTTP 200 + a session cookie on success; non-200 on failure.
    The cookie is REUSED for every basicspacedata query (one httpx.AsyncClient
    with a persistent cookie jar) — Space-Track penalizes excessive logins, so
    we log in ONCE and reuse. Logout: GET {base}/ajaxauth/logout.

  RATE LIMITS (quoted from the docs, honored WITH MARGIN):
    "Limit API queries to less than 30 requests per 1 minute(s) and
     300 requests per 1 hour(s)."
    Per-class guidance (for later phases): gp 1/hour, satcat 1/day, decay 1/day,
    tip via the decay/tip classes. Defaults here are 20/min + 200/hour — a safe
    margin below the hard 30/300 ceiling; override via env.

  QUERY FORMAT:
    GET {base}/basicspacedata/query/class/<class>/<pred>/<val>/.../format/<fmt>
    e.g. .../class/gp/NORAD_CAT_ID/25544/format/json

  REQUEST CLASSES (identified now; ingested in later phases, NOT here):
    gp, gp_history (elements) · satcat · decay · tip (reentry/TIP).

  ALPHA-5 (catalog > 99999, since 2026-07-11):
    gp/gp_history TLE/3LE output encodes 100000..339999 as Alpha-5 (leading
    letter, I and O skipped). JSON/CSV/XML use the native integer NORAD_CAT_ID.
    This client QUERIES by integer id and proves connectivity in JSON (no
    Alpha-5 on the wire); alpha5_to_int/int_to_alpha5 below decode TLE-format
    output for later phases. NOTE: the existing CelesTrak parser
    (tracker_satellites.parse_3le) DROPS Alpha-5 objects today — a documented
    data-layer gap, fixed there when high-catalog objects are ingested.

Credentials come ONLY from the environment (never code, never git, never logged):
    SPACETRACK_USER, SPACETRACK_PASS               (required — fail-fast if unset)
    SPACETRACK_BASE_URL      (default https://www.space-track.org)
    SPACETRACK_MAX_PER_MIN   (default 20)   SPACETRACK_MAX_PER_HOUR (default 200)
    SPACETRACK_TIMEOUT_S     (default 30)

Proof-of-connection (no persistence, no secrets logged):
    SPACETRACK_USER=... SPACETRACK_PASS=... python backend/spacetrack_client.py
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

logger = logging.getLogger("spacetrack")

LOGIN_PATH = "/ajaxauth/login"
LOGOUT_PATH = "/ajaxauth/logout"
QUERY_BASE = "/basicspacedata/query"
DEFAULT_BASE_URL = "https://www.space-track.org"

# Alpha-5 alphabet: index == catalog high-digit value. Digits 0-9 map to
# themselves; letters start at 10 (A) and SKIP I and O (avoid 1/0 confusion),
# so A=10 ... H=17, J=18 ... N=22, P=23 ... Z=33 -> covers 100000..339999.
_ALPHA5 = "0123456789ABCDEFGHJKLMNPQRSTUVWXYZ"


def alpha5_to_int(value: str) -> int:
    """Decode a NORAD catalog id that may be Alpha-5 encoded (TLE/3LE output).
    Classic 5-digit ids pass straight through int()."""
    s = str(value).strip()
    if len(s) == 5 and s[0].isalpha():
        try:
            high = _ALPHA5.index(s[0].upper())
        except ValueError:
            raise ValueError(f"invalid Alpha-5 leading char: {s[0]!r}") from None
        return high * 10000 + int(s[1:])
    return int(s)


def int_to_alpha5(norad: int) -> str:
    """Encode an integer catalog id to the 5-char TLE field (Alpha-5 for
    100000..339999). Classic ids are zero-padded to 5 chars."""
    if norad < 0:
        raise ValueError("norad id must be non-negative")
    if norad < 100000:
        return str(norad).rjust(5, "0")
    if norad > 339999:
        raise ValueError("Alpha-5 caps at 339999")
    high, low = divmod(norad, 10000)
    return _ALPHA5[high] + str(low).rjust(4, "0")


class SpaceTrackError(RuntimeError):
    """Base for Space-Track client failures."""


class AuthError(SpaceTrackError):
    """Credentials missing/rejected — never carries the password."""


class ThrottleError(SpaceTrackError):
    """Upstream throttling that survived all backoff retries."""


@dataclass(frozen=True, slots=True)
class SpaceTrackConfig:
    identity: str = ""
    password: str = field(default="", repr=False)   # never printed in a repr
    base_url: str = DEFAULT_BASE_URL
    max_per_min: int = 20        # < 30 hard limit, with margin
    max_per_hour: int = 200      # < 300 hard limit, with margin
    timeout_s: float = 30.0
    backoff_min_s: float = 1.0
    backoff_max_s: float = 60.0
    max_retries: int = 5

    @classmethod
    def from_env(cls) -> "SpaceTrackConfig":
        def _int(name: str, default: int) -> int:
            try:
                return int(os.environ.get(name, "") or default)
            except ValueError:
                return default
        return cls(
            identity=os.environ.get("SPACETRACK_USER", ""),
            password=os.environ.get("SPACETRACK_PASS", ""),
            base_url=os.environ.get("SPACETRACK_BASE_URL", DEFAULT_BASE_URL).rstrip("/"),
            max_per_min=_int("SPACETRACK_MAX_PER_MIN", 20),
            max_per_hour=_int("SPACETRACK_MAX_PER_HOUR", 200),
            timeout_s=float(os.environ.get("SPACETRACK_TIMEOUT_S", "") or 30.0),
        )

    @property
    def has_credentials(self) -> bool:
        return bool(self.identity and self.password)


class RateLimiter:
    """Sliding-window limiter honoring BOTH a per-minute and a per-hour cap.
    time_fn/sleep_fn are injectable so tests drive it without real waiting."""

    def __init__(
        self, max_per_min: int, max_per_hour: int, *,
        time_fn: Callable[[], float] = time.monotonic,
        sleep_fn: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._per_min = max_per_min
        self._per_hour = max_per_hour
        self._minute: deque[float] = deque()
        self._hour: deque[float] = deque()
        self._time = time_fn
        self._sleep = sleep_fn
        self.waits = 0  # observable in tests

    @staticmethod
    def _purge(dq: deque[float], now: float, window: float) -> None:
        while dq and now - dq[0] >= window:
            dq.popleft()

    async def acquire(self) -> None:
        while True:
            now = self._time()
            self._purge(self._minute, now, 60.0)
            self._purge(self._hour, now, 3600.0)
            waits = []
            if self._per_min and len(self._minute) >= self._per_min:
                waits.append(self._minute[0] + 60.0 - now)
            if self._per_hour and len(self._hour) >= self._per_hour:
                waits.append(self._hour[0] + 3600.0 - now)
            wait = max((w for w in waits if w > 0), default=0.0)
            if wait <= 0:
                break
            self.waits += 1
            await self._sleep(wait)
        stamp = self._time()
        self._minute.append(stamp)
        self._hour.append(stamp)


class SpaceTrackClient:
    """Authenticated, rate-limited, resilient Space-Track connection.

    Logs in ONCE (cookie reused); every query passes the rate limiter; 429/503
    and network errors back off exponentially and retry (mirrors the aisstream
    backoff discipline); a 401 mid-session triggers exactly one re-login.

    `http` is injectable (an object with async post(url, data) / get(url) and a
    close()); the default is a persistent httpx.AsyncClient whose cookie jar IS
    the reused session.
    """

    def __init__(
        self, config: SpaceTrackConfig, *,
        http: Any | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        time_fn: Callable[[], float] = time.monotonic,
    ) -> None:
        self._cfg = config
        self._http = http
        self._sleep = sleep
        self._limiter = RateLimiter(
            config.max_per_min, config.max_per_hour, time_fn=time_fn, sleep_fn=sleep
        )
        self._logged_in = False
        # counters (health / proof output — never include secrets)
        self.logins = 0
        self.queries = 0
        self.throttles = 0
        self.retries = 0

    # -- transport -------------------------------------------------------

    async def _client(self) -> Any:
        if self._http is None:
            import httpx  # lazy so tests need no network stack
            self._http = httpx.AsyncClient(
                timeout=self._cfg.timeout_s, follow_redirects=True
            )
        return self._http

    async def close(self) -> None:
        """Log out (best-effort) and release the transport."""
        if self._logged_in and self._http is not None:
            with _suppress():
                await self._http.get(self._cfg.base_url + LOGOUT_PATH)
        self._logged_in = False
        if self._http is not None and hasattr(self._http, "aclose"):
            with _suppress():
                await self._http.aclose()

    # -- auth ------------------------------------------------------------

    async def login(self) -> None:
        """Establish the session. Fail-fast (no network) if creds are unset;
        the password is NEVER placed in a message or log."""
        if not self._cfg.has_credentials:
            raise AuthError(
                "SPACETRACK_USER / SPACETRACK_PASS are not set — export them "
                "(see .env.example). Credentials are read from the environment "
                "only; never hardcode them."
            )
        http = await self._client()
        await self._limiter.acquire()
        resp = await http.post(
            self._cfg.base_url + LOGIN_PATH,
            data={"identity": self._cfg.identity, "password": self._cfg.password},
        )
        status = resp.status_code
        body = (getattr(resp, "text", "") or "")
        if status != 200 or ("failed" in body.lower() and len(body) < 500):
            # message carries the identity (not secret) and status only
            raise AuthError(
                f"Space-Track login failed for identity {self._cfg.identity!r} "
                f"(HTTP {status})"
            )
        self._logged_in = True
        self.logins += 1
        logger.info("Space-Track session established (login #%d)", self.logins)

    async def _ensure_login(self) -> None:
        if not self._logged_in:
            await self.login()

    # -- query -----------------------------------------------------------

    def _build_url(self, class_name: str, fmt: str, predicates: dict[str, Any]) -> str:
        parts = [self._cfg.base_url + QUERY_BASE, "class", class_name]
        for key, val in predicates.items():
            parts += [key, str(val)]
        parts += ["format", fmt]
        return "/".join(parts)

    async def query(self, class_name: str, *, fmt: str = "json", **predicates: Any) -> Any:
        """Run one basicspacedata query. Returns parsed JSON (fmt='json') or raw
        text otherwise. Rate-limited, session-reused, backoff-resilient."""
        await self._ensure_login()
        url = self._build_url(class_name, fmt, predicates)
        resp = await self._request_with_backoff(url, allow_relogin=True)
        self.queries += 1
        if fmt == "json":
            return resp.json()
        return resp.text

    async def _request_with_backoff(self, url: str, *, allow_relogin: bool) -> Any:
        backoff = self._cfg.backoff_min_s
        last_exc: Exception | None = None
        for _attempt in range(self._cfg.max_retries):
            await self._limiter.acquire()
            try:
                http = await self._client()
                resp = await http.get(url)
            except Exception as exc:  # noqa: BLE001 — network error: back off, retry
                last_exc = exc
                self.retries += 1
                logger.warning("Space-Track network error (%s) — retry in %.0fs", exc, backoff)
                await self._sleep(backoff)
                backoff = min(backoff * 2, self._cfg.backoff_max_s)
                continue

            status = resp.status_code
            if status == 200:
                return resp
            if status == 401 and allow_relogin:
                # session expired mid-run — re-login exactly once, then retry
                logger.info("Space-Track 401 — re-establishing session once")
                self._logged_in = False
                await self.login()
                allow_relogin = False
                continue
            if status in (429, 500, 502, 503):
                self.throttles += 1
                wait = _retry_after(resp, backoff)
                logger.warning("Space-Track throttled (HTTP %d) — backoff %.0fs", status, wait)
                await self._sleep(wait)
                backoff = min(backoff * 2, self._cfg.backoff_max_s)
                continue
            raise SpaceTrackError(f"Space-Track query failed (HTTP {status})")

        if last_exc is not None:
            raise SpaceTrackError(f"Space-Track unreachable after retries: {last_exc}")
        raise ThrottleError("Space-Track kept throttling after all retries")

    def health(self) -> dict[str, Any]:
        return {
            "logged_in": self._logged_in,
            "logins": self.logins,
            "queries": self.queries,
            "throttles": self.throttles,
            "retries": self.retries,
            "rate_limit": {"per_min": self._cfg.max_per_min, "per_hour": self._cfg.max_per_hour},
        }


def _retry_after(resp: Any, fallback: float) -> float:
    hdrs = getattr(resp, "headers", {}) or {}
    raw = hdrs.get("Retry-After") or hdrs.get("retry-after")
    if raw:
        try:
            return float(raw)
        except (TypeError, ValueError):
            pass
    return fallback


class _suppress:
    def __enter__(self) -> None:
        return None

    def __exit__(self, *exc: Any) -> bool:
        return True


# --- proof-of-connection (no persistence; secrets never logged) -------------

async def _proof(norad_ids: list[int]) -> int:
    cfg = SpaceTrackConfig.from_env()
    if not cfg.has_credentials:
        logger.error("SPACETRACK_USER / SPACETRACK_PASS not set — cannot run the "
                     "live proof. Export them (see .env.example) and re-run.")
        return 2
    client = SpaceTrackClient(cfg)
    started = time.monotonic()
    try:
        ids = ",".join(str(n) for n in norad_ids)
        rows = await client.query("gp", NORAD_CAT_ID=ids, fmt="json")
        elapsed = time.monotonic() - started
        n = len(rows) if isinstance(rows, list) else 0
        logger.info("PROOF OK: gp round-trip for %d NORAD ids -> %d rows in %.2fs",
                    len(norad_ids), n, elapsed)
        for r in (rows or [])[:3]:
            # print a couple of safe identity fields only — no bulk, no persist
            logger.info("  %s  NORAD=%s  EPOCH=%s",
                        r.get("OBJECT_NAME"), r.get("NORAD_CAT_ID"), r.get("EPOCH"))
        logger.info("health: %s", client.health())
        return 0
    finally:
        await client.close()


def main() -> int:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
                        datefmt="%H:%M:%S")
    from contextlib import suppress
    with suppress(Exception):
        from dotenv import load_dotenv
        load_dotenv()
    # a tiny, safe query — a handful of already-tracked NORAD ids (ISS + two more)
    return asyncio.run(_proof([25544, 36086, 48274]))


if __name__ == "__main__":
    import sys
    sys.exit(main())
