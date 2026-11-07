"""
asyncpg pool management + the shared snapshot queries.

The pool is created lazily and re-created on demand: the API process must
start (and /api/v1/health must answer honestly) even when the database is
down. Endpoints translate pool failures into 503s or archive fallback —
they never crash the server.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import asyncpg

#: Errors that mean "the database is unavailable" — the shared except-clause of
#: every handler. OSError/ConnectionError cover the socket layer under asyncpg.
DB_ERRORS = (asyncpg.PostgresError, asyncpg.InterfaceError, OSError, ConnectionError)

logger = logging.getLogger("opensky_api.db")

STATE_COLUMNS = (
    "icao24, callsign, origin_country, time_position, last_contact, "
    "longitude, latitude, baro_altitude, geo_altitude, on_ground, velocity, "
    "true_track, vertical_rate, squawk, spi, position_source, category, "
    "snapshot_time, fetched_at, "
    # geo classification (migration 012 / P3) — populated for classified rows
    # (adsbexchange today; NULL for pre-backfill opensky/airplanes_live).
    "country, near_country, position_quality, distance_km, geo_source"
)


class Database:
    def __init__(self, dsn: str) -> None:
        self.dsn = dsn
        self._pool: asyncpg.Pool | None = None
        self._lock = asyncio.Lock()

    async def pool(self) -> asyncpg.Pool:
        """Return the pool, creating it if needed. Raises on unreachable DB."""
        if self._pool is not None:
            return self._pool
        async with self._lock:
            if self._pool is None:
                self._pool = await asyncpg.create_pool(
                    self.dsn, min_size=1, max_size=5, command_timeout=30
                )
                logger.info("DB pool created")
            return self._pool

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None

    # -- shared queries --------------------------------------------------

    async def latest_snapshot_row(self, complete_only: bool = False,
                                  source: str = "opensky") -> asyncpg.Record | None:
        pool = await self.pool()
        where = "WHERE source = $1" + (" AND complete" if complete_only else "")
        return await pool.fetchrow(
            "SELECT snapshot_time, ts, lamin, lomin, lamax, lomax, count, complete "
            f"FROM snapshots {where} ORDER BY snapshot_time DESC LIMIT 1",
            source,
        )

    async def snapshot_row_at_or_before(self, at: int, source: str = "opensky") -> asyncpg.Record | None:
        """Newest COMPLETE snapshot at/before `at` for a source — replay never
        serves a half-ingested snapshot."""
        pool = await self.pool()
        return await pool.fetchrow(
            "SELECT snapshot_time, ts, lamin, lomin, lamax, lomax, count, complete "
            "FROM snapshots WHERE complete AND source = $2 AND snapshot_time <= $1 "
            "ORDER BY snapshot_time DESC LIMIT 1",
            at, source,
        )

    async def states_at(self, snapshot_time: int, source: str = "opensky",
                        aoi_only: bool = False) -> list[asyncpg.Record]:
        pool = await self.pool()
        # aoi_only keeps the adsbexchange overlay bounded to the Middle-East
        # neighbourhood (INSIDE a country or NEAR a border) using the P3 geo
        # columns — so a globally-configured adsb feed still shows only relevant
        # traffic. A no-op for feeds/rows not yet classified.
        extra = " AND (country IS NOT NULL OR near_country IS NOT NULL)" if aoi_only else ""
        return await pool.fetch(
            f"SELECT {STATE_COLUMNS} FROM state_vectors "
            "WHERE ts = to_timestamp($1::bigint) AND source = $2" + extra + " ORDER BY icao24",
            snapshot_time, source,
        )

    async def military_states_at(self, snapshot_time: int) -> list[asyncpg.Record]:
        """states_at for the airplanes_live feed INCLUDING its airframe extras
        (actype/acdesc/reg — populated by ingest.py for this source only).
        The DB-fallback path of /military."""
        pool = await self.pool()
        return await pool.fetch(
            f"SELECT {STATE_COLUMNS}, actype, acdesc, reg FROM state_vectors "
            "WHERE ts = to_timestamp($1::bigint) AND source = 'airplanes_live' "
            "ORDER BY icao24",
            snapshot_time,
        )

    async def latest_source_snapshot(self, source: str) -> int | None:
        """Newest snapshot_time present for a source in state_vectors (the live
        overlay reads this feed's most recent cycle)."""
        pool = await self.pool()
        return await pool.fetchval(
            "SELECT max(snapshot_time) FROM state_vectors WHERE source = $1", source
        )


def state_record_to_dict(r: asyncpg.Record) -> dict[str, Any]:
    d = dict(r)
    fa = d.get("fetched_at")
    d["fetched_at"] = fa.isoformat() if isinstance(fa, datetime) else None
    return d


def bbox_or_none(row: Any) -> dict[str, float] | None:
    if row is None or row["lamin"] is None:
        return None
    return {
        "lamin": row["lamin"], "lomin": row["lomin"],
        "lamax": row["lamax"], "lomax": row["lomax"],
    }


def read_latest_json(path: Path) -> dict[str, Any] | None:
    """Archive fallback for /live when the DB is unavailable or empty."""
    try:
        with path.open(encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError, json.JSONDecodeError):
        return None


def utcnow() -> datetime:
    return datetime.now(timezone.utc)
