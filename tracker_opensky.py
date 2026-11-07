#!/usr/bin/env python3
"""
OpenSky Network Live Tracker — SOLID Architecture Edition
=========================================================
Production-grade async collector for live aircraft state vectors from the
OpenSky Network REST API, with periodic snapshots and JSONL archiving.

Why this replaces the ADS-B Exchange version
---------------------------------------------
The old globe.adsbexchange.com/VirtualRadar endpoint is gone: that host now
runs a tar1090 backend behind reCAPTCHA and returns 403 to scripted clients,
and the official v2 API (gateway.adsbexchange.com) is paid and per-ICAO only.
OpenSky exposes a documented /states/all endpoint that returns a full
bounding-box snapshot in one call — matching this project's snapshot model.

Key structural differences from the old design
-----------------------------------------------
  • AircraftDetailService is REMOVED. OpenSky returns everything in the
    snapshot; there is no per-aircraft detail endpoint to enrich from.
  • Authentication is now first-class. Accounts created after mid-March 2025
    require OAuth2 client-credentials. Anonymous access exists but is capped
    at 400 credits/day and 10-second resolution — usable only for smoke tests.
  • State vectors are POSITIONAL ARRAYS (fixed field order), not objects, so
    parsing is index-based against the official field map.

IMPORTANT — infrastructure note
--------------------------------
OpenSky may block AWS / large cloud provider IP ranges due to abuse. Run this
collector from local/self-hosted hardware, not from rented cloud GPU hosts.

Requirements
    pip install aiohttp aiofiles

Usage
    # Authenticated (recommended): set credentials via env or flags
    export OPENSKY_CLIENT_ID=... OPENSKY_CLIENT_SECRET=...
    python tracker_opensky.py --lamin 45.83 --lomin 5.99 --lamax 47.82 --lomax 10.52

    # Anonymous (smoke test only — heavy rate limits)
    python tracker_opensky.py --lamin 33.5 --lomin -118.9 --lamax 34.3 --lomax -117.9 --anonymous
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import random
import signal
import sys
import time
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import aiohttp
import aiofiles

# ==============================================================================
# Logging
# ==============================================================================
logger = logging.getLogger("opensky_tracker")


def _setup_logging(output_dir: Path, verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    output_dir.mkdir(parents=True, exist_ok=True)
    console_fmt = logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(message)s", datefmt="%H:%M:%S"
    )
    file_fmt = logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(name)s | %(funcName)s:%(lineno)d | %(message)s"
    )
    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(console_fmt)
    fh = logging.FileHandler(output_dir / "tracker.log", encoding="utf-8", mode="a")
    fh.setFormatter(file_fmt)
    logging.basicConfig(level=level, handlers=[console, fh], force=True)


# ==============================================================================
# Value parsing helpers
# ==============================================================================

def _get(arr: list[Any], idx: int) -> Any:
    """Safe positional access — OpenSky arrays may be shorter than expected."""
    return arr[idx] if idx < len(arr) else None


def _f(val: Any) -> float | None:
    try:
        return float(val) if val is not None else None
    except (ValueError, TypeError):
        return None


def _i(val: Any) -> int | None:
    try:
        return int(val) if val is not None else None
    except (ValueError, TypeError):
        return None


# ==============================================================================
# Domain Model
# ==============================================================================

@dataclass(frozen=True, slots=True)
class StateVector:
    """
    A single aircraft state vector from OpenSky.

    Field order is fixed by the OpenSky /states/all contract. Index comments
    match the official python binding (0..17). New trailing fields from the
    server are ignored, per OpenSky's forward-compatibility note.
    """
    icao24: str                        # 0
    callsign: str                      # 1
    origin_country: str                # 2
    time_position: int | None          # 3
    last_contact: int | None           # 4
    longitude: float | None            # 5
    latitude: float | None             # 6
    baro_altitude: float | None        # 7 (meters)
    on_ground: bool                    # 8
    velocity: float | None             # 9 (m/s)
    true_track: float | None           # 10 (deg)
    vertical_rate: float | None        # 11 (m/s)
    geo_altitude: float | None         # 13 (meters)
    squawk: str                        # 14
    spi: bool                          # 15
    position_source: int | None        # 16
    category: int | None               # 17
    snapshot_time: int = 0             # from the enclosing states response
    fetched_at: str = ""

    # index 12 (sensors) is intentionally dropped: only populated for own-sensor
    # queries, irrelevant to bounding-box archiving.

    @classmethod
    def from_array(cls, arr: list[Any], snapshot_time: int) -> StateVector | None:
        icao24 = _get(arr, 0)
        if not isinstance(icao24, str) or len(icao24) < 4:
            return None
        return cls(
            icao24=icao24.strip().lower(),
            callsign=str(_get(arr, 1) or "").strip(),
            origin_country=str(_get(arr, 2) or "").strip(),
            time_position=_i(_get(arr, 3)),
            last_contact=_i(_get(arr, 4)),
            longitude=_f(_get(arr, 5)),
            latitude=_f(_get(arr, 6)),
            baro_altitude=_f(_get(arr, 7)),
            on_ground=bool(_get(arr, 8)),
            velocity=_f(_get(arr, 9)),
            true_track=_f(_get(arr, 10)),
            vertical_rate=_f(_get(arr, 11)),
            geo_altitude=_f(_get(arr, 13)),
            squawk=str(_get(arr, 14) or "").strip(),
            spi=bool(_get(arr, 15)),
            position_source=_i(_get(arr, 16)),
            category=_i(_get(arr, 17)),
            snapshot_time=snapshot_time,
            fetched_at=datetime.now(timezone.utc).isoformat(),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "icao24": self.icao24,
            "callsign": self.callsign,
            "origin_country": self.origin_country,
            "time_position": self.time_position,
            "last_contact": self.last_contact,
            "longitude": self.longitude,
            "latitude": self.latitude,
            "baro_altitude": self.baro_altitude,
            "geo_altitude": self.geo_altitude,
            "on_ground": self.on_ground,
            "velocity": self.velocity,
            "true_track": self.true_track,
            "vertical_rate": self.vertical_rate,
            "squawk": self.squawk,
            "spi": self.spi,
            "position_source": self.position_source,
            "category": self.category,
            "snapshot_time": self.snapshot_time,
            "fetched_at": self.fetched_at,
        }


# ==============================================================================
# Configuration
# ==============================================================================

@dataclass(frozen=True, slots=True)
class BoundingBox:
    lamin: float
    lomin: float
    lamax: float
    lomax: float

    def __post_init__(self) -> None:
        if not -90 <= self.lamin <= 90 or not -90 <= self.lamax <= 90:
            raise ValueError("latitudes must be between -90 and 90")
        if not -180 <= self.lomin <= 180 or not -180 <= self.lomax <= 180:
            raise ValueError("longitudes must be between -180 and 180")
        if self.lamin >= self.lamax:
            raise ValueError("lamin must be < lamax")
        if self.lomin >= self.lomax:
            raise ValueError("lomin must be < lomax")

    def as_params(self) -> dict[str, float]:
        return {
            "lamin": self.lamin,
            "lomin": self.lomin,
            "lamax": self.lamax,
            "lomax": self.lomax,
        }


@dataclass(frozen=True, slots=True)
class AuthConfig:
    """
    OAuth2 client-credentials. If both are empty, the client runs anonymously
    (smoke-test only; 400 credits/day, 10s resolution).
    """
    client_id: str = ""
    client_secret: str = ""

    TOKEN_URL: str = (
        "https://auth.opensky-network.org/auth/realms/opensky-network/"
        "protocol/openid-connect/token"
    )

    @property
    def is_authenticated(self) -> bool:
        return bool(self.client_id and self.client_secret)


@dataclass(frozen=True, slots=True)
class TimingConfig:
    snapshot_delay: float = 30.0   # seconds between snapshots
    max_snapshots: int = 0         # 0 = infinite
    max_retries: int = 3

    def __post_init__(self) -> None:
        # Authenticated resolution is ~5s; anonymous is 10s. A floor of 5s
        # avoids wasting credits on duplicate state vectors.
        if self.snapshot_delay < 5:
            raise ValueError("snapshot_delay must be >= 5 (OpenSky resolution floor)")
        if self.max_retries < 1:
            raise ValueError("max_retries must be >= 1")


@dataclass(frozen=True, slots=True)
class InfrastructureConfig:
    output_dir: Path = field(default_factory=lambda: Path("opensky_output"))
    user_agent: str = "opensky-tracker/1.0 (+research; self-hosted)"

    @property
    def snapshot_file(self) -> Path:
        return self.output_dir / "states.jsonl"

    @property
    def discovered_file(self) -> Path:
        return self.output_dir / "discovered_icao24.json"

    @property
    def latest_file(self) -> Path:
        """Most recent snapshot, atomically replaced — the live-view source."""
        return self.output_dir / "latest.json"


@dataclass(frozen=True, slots=True)
class Config:
    bbox: BoundingBox
    auth: AuthConfig
    timing: TimingConfig
    infra: InfrastructureConfig
    # Phase 2 (backend): optional DB mirror, empty = disabled. The archive
    # (JSONL/latest.json) remains ground truth; this is an additional,
    # failure-tolerant write path — see DatabaseSink.
    db_url: str = ""


# ==============================================================================
# Utilities
# ==============================================================================

class BackoffPolicy:
    """Exponential backoff with jitter."""

    def __init__(self, max_wait: float = 60.0, jitter: float = 2.0) -> None:
        self.max_wait = max_wait
        self.jitter = jitter

    def calculate(self, attempt: int) -> float:
        base = min(2 ** attempt, self.max_wait)
        return base + random.uniform(0, self.jitter)


class RetriableError(Exception):
    """Signals a retriable HTTP failure after exhausting attempts."""


# ==============================================================================
# File I/O — batched JSONL
# ==============================================================================

class JsonlWriter:
    """Batched JSONL append writer with periodic flush. Lock-protected."""

    def __init__(self, path: Path, batch_size: int = 200, flush_interval: float = 5.0) -> None:
        self.path = path
        self.batch_size = batch_size
        self.flush_interval = flush_interval
        self._buffer: list[str] = []
        self._lock = asyncio.Lock()
        self._last_flush = time.monotonic()
        self._total_written = 0

    async def write(self, record: dict[str, Any]) -> None:
        line = json.dumps(record, ensure_ascii=False) + "\n"
        async with self._lock:
            self._buffer.append(line)
            if (len(self._buffer) >= self.batch_size
                    or (time.monotonic() - self._last_flush) >= self.flush_interval):
                await self._flush_unlocked()

    async def _flush_unlocked(self) -> None:
        if not self._buffer:
            return
        data = "".join(self._buffer)
        async with aiofiles.open(self.path, "a", encoding="utf-8") as f:
            await f.write(data)
            await f.flush()
        self._total_written += len(self._buffer)
        self._buffer.clear()
        self._last_flush = time.monotonic()

    async def close(self) -> None:
        async with self._lock:
            await self._flush_unlocked()

    async def __aenter__(self) -> JsonlWriter:
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.close()


class AtomicJsonStorage:
    """Atomic JSON read/write via temp-file rename."""

    @staticmethod
    async def read(path: Path) -> Any:
        if not path.exists():
            return None
        try:
            async with aiofiles.open(path, "r", encoding="utf-8") as f:
                return json.loads(await f.read())
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            logger.warning("Failed to read %s: %s", path, exc)
            return None

    @staticmethod
    async def write(path: Path, data: Any) -> None:
        tmp = path.with_suffix(path.suffix + ".tmp")
        async with aiofiles.open(tmp, "w", encoding="utf-8") as f:
            await f.write(json.dumps(data, ensure_ascii=False, indent=2))
        tmp.replace(path)


# ==============================================================================
# Token Manager — OAuth2 client-credentials with auto-refresh
# ==============================================================================

class TokenManager:
    """
    Fetches and caches an OAuth2 access token, refreshing before expiry.
    Concurrency-safe: a single refresh runs even under simultaneous callers.
    Returns None in anonymous mode (no credentials).
    """

    REFRESH_MARGIN = 30  # refresh this many seconds before actual expiry

    def __init__(self, auth: AuthConfig, session: aiohttp.ClientSession) -> None:
        self.auth = auth
        self.session = session
        self._token: str | None = None
        self._expires_at: float = 0.0
        self._lock = asyncio.Lock()

    async def get_token(self) -> str | None:
        if not self.auth.is_authenticated:
            return None
        async with self._lock:
            if self._token and time.monotonic() < self._expires_at:
                return self._token
            return await self._refresh_unlocked()

    async def _refresh_unlocked(self) -> str:
        data = {
            "grant_type": "client_credentials",
            "client_id": self.auth.client_id,
            "client_secret": self.auth.client_secret,
        }
        async with self.session.post(self.auth.TOKEN_URL, data=data) as resp:
            resp.raise_for_status()
            payload = await resp.json()
        self._token = payload["access_token"]
        expires_in = int(payload.get("expires_in", 1800))
        self._expires_at = time.monotonic() + expires_in - self.REFRESH_MARGIN
        logger.info("OAuth2 token acquired (expires in %ds)", expires_in)
        return self._token


# ==============================================================================
# HTTP Client
# ==============================================================================

class HttpClient:
    """
    Managed aiohttp session with OAuth2 bearer injection and centralized
    retry/backoff. Retry logic lives here only — callers must not re-wrap.
    """

    def __init__(self, infra: InfrastructureConfig, auth: AuthConfig, max_retries: int) -> None:
        self.infra = infra
        self.auth = auth
        self.max_retries = max_retries
        self.backoff = BackoffPolicy()
        self._session: aiohttp.ClientSession | None = None
        self._tokens: TokenManager | None = None

    async def __aenter__(self) -> HttpClient:
        connector = aiohttp.TCPConnector(limit=10, limit_per_host=5)
        timeout = aiohttp.ClientTimeout(total=45, connect=10)
        self._session = aiohttp.ClientSession(
            connector=connector,
            timeout=timeout,
            headers={
                "User-Agent": self.infra.user_agent,
                "Accept": "application/json",
            },
        )
        self._tokens = TokenManager(self.auth, self._session)
        logger.info(
            "HTTP session created | mode=%s",
            "authenticated" if self.auth.is_authenticated else "anonymous",
        )
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.close()

    async def _auth_headers(self) -> dict[str, str]:
        assert self._tokens is not None
        token = await self._tokens.get_token()
        return {"Authorization": f"Bearer {token}"} if token else {}

    async def get(self, url: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        if self._session is None:
            raise RuntimeError("Session not initialized")

        last_exc: Exception | None = None
        for attempt in range(1, self.max_retries + 1):
            try:
                headers = await self._auth_headers()
                async with self._session.get(url, params=params, headers=headers) as resp:
                    if resp.status == 429:
                        retry_after = resp.headers.get("X-Rate-Limit-Retry-After-Seconds")
                        wait = float(retry_after) if retry_after else self.backoff.calculate(attempt)
                        logger.warning("429 rate limit / credits exhausted, waiting %.1fs", wait)
                        await asyncio.sleep(wait)
                        continue
                    if resp.status in (401, 403):
                        # Token may have gone stale mid-flight; force one refresh.
                        logger.warning("Auth error %d — refreshing token", resp.status)
                        if self._tokens:
                            self._tokens._token = None  # invalidate; next get_token refreshes
                        await asyncio.sleep(self.backoff.calculate(attempt))
                        continue
                    if 500 <= resp.status < 600:
                        logger.debug("Server error %d (attempt %d)", resp.status, attempt)
                        await asyncio.sleep(self.backoff.calculate(attempt))
                        continue
                    resp.raise_for_status()
                    return await resp.json()

            except aiohttp.ClientResponseError as exc:
                last_exc = exc
                if exc.status < 500:
                    raise
                await asyncio.sleep(self.backoff.calculate(attempt))
            except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                last_exc = exc
                logger.debug("Request error (attempt %d): %s", attempt, exc)
                await asyncio.sleep(self.backoff.calculate(attempt))

        raise RetriableError(f"Failed after {self.max_retries} attempts: {url}") from last_exc

    async def close(self) -> None:
        if self._session:
            await self._session.close()
            self._session = None


# ==============================================================================
# State Snapshot Service
# ==============================================================================

class StateSnapshotService:
    """Fetches a bounding-box snapshot of state vectors from OpenSky."""

    STATES_URL = "https://opensky-network.org/api/states/all"

    def __init__(self, client: HttpClient, bbox: BoundingBox) -> None:
        self.client = client
        self.bbox = bbox

    async def fetch(self) -> list[StateVector]:
        data = await self.client.get(self.STATES_URL, self.bbox.as_params())
        if not isinstance(data, dict):
            logger.warning("Unexpected response type: %s", type(data).__name__)
            return []
        snapshot_time = _i(data.get("time")) or int(time.time())
        raw_states = data.get("states") or []
        results = [
            sv for arr in raw_states
            if isinstance(arr, list) and (sv := StateVector.from_array(arr, snapshot_time)) is not None
        ]
        logger.info("Snapshot @ %d: %d state vectors", snapshot_time, len(results))
        return results


# ==============================================================================
# Optional DB sink — flag-gated (--db-url), failure-tolerant by contract
# ==============================================================================

class DatabaseSink:
    """
    Mirrors each fresh snapshot into TimescaleDB via the SAME idempotent
    ingestion path as ingest.py (backfill) — one implementation, no drift.

    Resilience contract (non-negotiable): any DB error is logged and
    swallowed; JSONL/latest.json archiving is never affected and the
    collector never crashes because the DB is down. Missed snapshots are
    covered by `ingest.py backfill` (idempotent) when the DB returns.

    ingest.py is imported lazily so the collector keeps zero DB dependencies
    unless --db-url is actually used.
    """

    def __init__(self, db_url: str, latest_path: Path) -> None:
        from ingest import Ingester, parse_record  # lazy: only with --db-url
        self._parse = parse_record
        self._ingester = Ingester(db_url, latest_path)
        self._connected = False

    async def write(self, states: list[StateVector]) -> None:
        try:
            if not self._connected:
                await self._ingester.connect()
                self._connected = True
                logger.info("DB sink connected")
            rows = [row for sv in states
                    if (row := self._parse(sv.to_dict())) is not None]
            await self._ingester.ingest_rows(rows, notify=True)
            logger.info("DB sink: %d vectors mirrored", len(rows))
        except Exception as exc:  # noqa: BLE001 — DB failure must never stop archiving
            logger.error("DB write failed (archive unaffected, backfill will "
                         "cover the gap): %s", exc)
            with suppress(Exception):
                await self._ingester.close()
            self._connected = False

    async def close(self) -> None:
        with suppress(Exception):
            await self._ingester.close()


# ==============================================================================
# Orchestrator
# ==============================================================================

class OpenSkyOrchestrator:
    """Coordinates snapshot collection and archiving."""

    def __init__(self, config: Config) -> None:
        self.config = config
        self.stop_event = asyncio.Event()
        self._last_written_time: int | None = None
        self.storage = AtomicJsonStorage()
        self._db_sink: DatabaseSink | None = (
            DatabaseSink(config.db_url, config.infra.latest_file)
            if config.db_url else None
        )

    async def run(self) -> int:
        infra = self.config.infra
        infra.output_dir.mkdir(parents=True, exist_ok=True)
        self._install_signal_handlers()

        snapshot_count = 0
        async with HttpClient(infra, self.config.auth, self.config.timing.max_retries) as client:
            service = StateSnapshotService(client, self.config.bbox)
            async with JsonlWriter(infra.snapshot_file) as writer:
                while not self.stop_event.is_set():
                    snapshot_count += 1
                    logger.info("=== Snapshot #%d ===", snapshot_count)
                    try:
                        states = await service.fetch()
                        # Structural dedup guard: OpenSky's snapshot_time advances
                        # only when new ADS-B data arrives, so two polls can return
                        # the identical snapshot. Skip writing an unchanged snapshot
                        # regardless of poll timing — this guarantees no duplicate
                        # snapshot ever enters the archive.
                        current_time = states[0].snapshot_time if states else None
                        if states and current_time == self._last_written_time:
                            logger.info(
                                "Snapshot @ %s unchanged — skipping write (%d vectors)",
                                current_time, len(states),
                            )
                        elif states:
                            for sv in states:
                                await writer.write(sv.to_dict())
                            await self._save_discovered({sv.icao24 for sv in states})
                            await self._save_latest(states, current_time)
                            self._last_written_time = current_time
                            logger.info("Wrote %d vectors @ %s", len(states), current_time)
                            if self._db_sink is not None:
                                # AFTER the archive writes: ground truth first,
                                # DB mirror second. Never raises (see DatabaseSink).
                                await self._db_sink.write(states)

                        if (self.config.timing.max_snapshots > 0
                                and snapshot_count >= self.config.timing.max_snapshots):
                            logger.info("Max snapshots reached: %d", snapshot_count)
                            break

                        logger.info(
                            "Waiting %.1fs before next snapshot",
                            self.config.timing.snapshot_delay,
                        )
                        await self._wait_or_stop(self.config.timing.snapshot_delay)

                    except Exception as exc:  # noqa: BLE001 — resilient main loop
                        logger.error("Snapshot cycle error: %s", exc, exc_info=True)
                        await self._wait_or_stop(30)

        if self._db_sink is not None:
            await self._db_sink.close()
        logger.info("Finished — %d snapshots collected", snapshot_count)
        return 130 if self.stop_event.is_set() else 0

    async def _save_discovered(self, icao_set: set[str]) -> None:
        existing = await self.storage.read(self.config.infra.discovered_file)
        if isinstance(existing, list):
            icao_set |= set(existing)
        await self.storage.write(self.config.infra.discovered_file, sorted(icao_set))

    async def _save_latest(self, states: list[StateVector], snapshot_time: int | None) -> None:
        """Write the current snapshot as latest.json for the live UI to read."""
        bbox = self.config.bbox
        payload = {
            "snapshot_time": snapshot_time,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "bbox": {"lamin": bbox.lamin, "lomin": bbox.lomin,
                     "lamax": bbox.lamax, "lomax": bbox.lomax},
            "count": len(states),
            "states": [sv.to_dict() for sv in states],
        }
        await self.storage.write(self.config.infra.latest_file, payload)

    async def _wait_or_stop(self, seconds: float) -> None:
        with suppress(asyncio.TimeoutError):
            await asyncio.wait_for(self.stop_event.wait(), timeout=seconds)

    def _install_signal_handlers(self) -> None:
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            with suppress(NotImplementedError):
                loop.add_signal_handler(sig, self._request_stop)

    def _request_stop(self) -> None:
        if not self.stop_event.is_set():
            logger.warning("Stop signal received — finishing current cycle")
            self.stop_event.set()


# ==============================================================================
# CLI
# ==============================================================================

def parse_args(argv: list[str] | None = None) -> Config:
    p = argparse.ArgumentParser(
        description="OpenSky Network live tracker — SOLID architecture edition.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
        epilog="""
LEGAL: OpenSky data is for research / non-commercial use. Cite the OpenSky
paper in publications. Run from self-hosted hardware (cloud IPs may be blocked).

EXAMPLES:
    # Switzerland bounding box, authenticated
    export OPENSKY_CLIENT_ID=... OPENSKY_CLIENT_SECRET=...
    python tracker_opensky.py --lamin 45.83 --lomin 5.99 --lamax 47.82 --lomax 10.52

    # Anonymous smoke test (heavy limits)
    python tracker_opensky.py --lamin 33.5 --lomin -118.9 --lamax 34.3 --lomax -117.9 \\
        --anonymous --max-snapshots 2
        """,
    )
    p.add_argument("--lamin", type=float, required=True, help="Min latitude")
    p.add_argument("--lomin", type=float, required=True, help="Min longitude")
    p.add_argument("--lamax", type=float, required=True, help="Max latitude")
    p.add_argument("--lomax", type=float, required=True, help="Max longitude")
    p.add_argument("--snapshot-delay", type=float, default=30.0, help="Seconds between snapshots (>=5)")
    p.add_argument("--max-snapshots", type=int, default=0, help="0 = infinite")
    p.add_argument("--retries", type=int, default=3, help="Max retries per request")
    p.add_argument("--client-id", type=str, default=None, help="OpenSky OAuth2 client_id")
    p.add_argument("--client-secret", type=str, default=None, help="OpenSky OAuth2 client_secret")
    p.add_argument("--anonymous", action="store_true", help="Force anonymous mode (ignore credentials)")
    p.add_argument("--db-url", type=str, default=None,
                   help="Optional: also mirror snapshots into TimescaleDB "
                        "(falls back to $OPENSKY_DB_URL; empty = disabled). "
                        "DB failures never interrupt archiving.")
    p.add_argument("--output", type=Path, default=Path("opensky_output"), help="Output directory")
    p.add_argument("-v", "--verbose", action="store_true", help="Debug-level logging")

    a = p.parse_args(argv)
    a.output.mkdir(parents=True, exist_ok=True)
    _setup_logging(a.output, a.verbose)

    if a.anonymous:
        auth = AuthConfig()
    else:
        auth = AuthConfig(
            client_id=a.client_id or os.getenv("OPENSKY_CLIENT_ID", ""),
            client_secret=a.client_secret or os.getenv("OPENSKY_CLIENT_SECRET", ""),
        )
        if not auth.is_authenticated:
            logger.warning(
                "No credentials found — running anonymously (400 credits/day, 10s "
                "resolution). Set OPENSKY_CLIENT_ID/SECRET for full access."
            )

    try:
        return Config(
            bbox=BoundingBox(lamin=a.lamin, lomin=a.lomin, lamax=a.lamax, lomax=a.lomax),
            auth=auth,
            timing=TimingConfig(
                snapshot_delay=a.snapshot_delay,
                max_snapshots=a.max_snapshots,
                max_retries=a.retries,
            ),
            infra=InfrastructureConfig(output_dir=a.output),
            db_url=a.db_url if a.db_url is not None else os.getenv("OPENSKY_DB_URL", ""),
        )
    except ValueError as exc:
        p.error(str(exc))
        raise  # unreachable


# ==============================================================================
# Entry Point
# ==============================================================================

def main(argv: list[str] | None = None) -> int:
    cfg = parse_args(argv)
    orchestrator = OpenSkyOrchestrator(cfg)
    try:
        return asyncio.run(orchestrator.run())
    except KeyboardInterrupt:
        logger.warning("Interrupted by user")
        return 130
    except Exception as exc:  # noqa: BLE001
        logger.error("Fatal error: %s", exc, exc_info=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())