"""Air domain — /live /history /snapshots /replay /stats /military /adsb."""

from __future__ import annotations

import asyncio
import time
from datetime import timedelta
from typing import Any
from fastapi import APIRouter, HTTPException, Query, Request
from ..config import settings
from ..db import (
    DB_ERRORS,
    STATE_COLUMNS,
    bbox_or_none,
    read_latest_json,
    state_record_to_dict,
)
from ..live import build_live_payload
from ..models import (
    AdsbResponse, HistoryResponse, LiveResponse,
    MilitaryResponse, ReplayResponse,
    SnapshotInfo, SnapshotsResponse, StatsBucket, StatsResponse,
)

from .shared import _db, db_503, parse_time, resolve_range

router = APIRouter()


MAX_RANGE_SECONDS = 7 * 86400          # hard cap for history/snapshots ranges


MAX_STATS_BUCKETS = 5000               # hard cap on buckets per stats request


STATS_BUCKETS = {"1m": 60, "5m": 300, "15m": 900, "1h": 3600, "1d": 86400}


# ------------------------------------------------------------------------------
# Endpoints
# ------------------------------------------------------------------------------

@router.get("/live", response_model=LiveResponse)
async def live(request: Request) -> Any:
    return await build_live_payload(_db(request))


@router.get("/history", response_model=HistoryResponse, response_model_by_alias=True)
async def history(
    request: Request,
    icao24: str = Query(min_length=4, max_length=10),
    from_: str | None = Query(None, alias="from"),
    to: str | None = None,
    source: str = Query("opensky", pattern="^(opensky|airplanes_live|adsbexchange)$"),
    limit: int = Query(1000, ge=1, le=10000),
    offset: int = Query(0, ge=0),
) -> Any:
    from_t, to_t = resolve_range(from_, to, default_span=3600, max_span=MAX_RANGE_SECONDS)
    db = _db(request)
    with db_503():
        pool = await db.pool()
        rows = await pool.fetch(
            f"SELECT {STATE_COLUMNS} FROM state_vectors "
            "WHERE icao24 = $1 AND source = $6 AND ts >= to_timestamp($2::bigint) "
            "AND ts < to_timestamp($3::bigint) "
            "ORDER BY ts ASC LIMIT $4 OFFSET $5",
            icao24.strip().lower(), from_t, to_t, limit + 1, offset, source,
        )
    has_more = len(rows) > limit
    rows = rows[:limit]
    return {
        "icao24": icao24.strip().lower(),
        "from": from_t, "to": to_t,
        "count": len(rows), "limit": limit, "offset": offset,
        "has_more": has_more,
        "points": [state_record_to_dict(r) for r in rows],
    }


@router.get("/snapshots", response_model=SnapshotsResponse, response_model_by_alias=True)
async def snapshots(
    request: Request,
    from_: str | None = Query(None, alias="from"),
    to: str | None = None,
    source: str = Query("opensky", pattern="^(opensky|airplanes_live|adsbexchange)$"),
    limit: int = Query(1000, ge=1, le=5000),
    offset: int = Query(0, ge=0),
) -> Any:
    from_t, to_t = resolve_range(from_, to, default_span=86400, max_span=MAX_RANGE_SECONDS)
    db = _db(request)
    with db_503():
        pool = await db.pool()
        rows = await pool.fetch(
            "SELECT snapshot_time, ts, count, complete, lamin, lomin, lamax, lomax "
            "FROM snapshots WHERE source = $5 AND snapshot_time >= $1 AND snapshot_time < $2 "
            "ORDER BY snapshot_time ASC LIMIT $3 OFFSET $4",
            from_t, to_t, limit + 1, offset, source,
        )
    has_more = len(rows) > limit
    rows = rows[:limit]
    return {
        "from": from_t, "to": to_t,
        "count": len(rows), "limit": limit, "offset": offset,
        "has_more": has_more,
        "snapshots": [
            SnapshotInfo(
                snapshot_time=r["snapshot_time"], ts=r["ts"].isoformat(),
                count=r["count"], complete=r["complete"], bbox=bbox_or_none(r),
            )
            for r in rows
        ],
    }


@router.get("/replay", response_model=ReplayResponse)
async def replay(
    request: Request,
    at: str = Query(...),
    source: str = Query("opensky", pattern="^(opensky|airplanes_live|adsbexchange)$"),
) -> Any:
    at_t = parse_time(at, "at")
    assert at_t is not None  # `at` is Query(...) — required, so parse_time cannot see None
    db = _db(request)
    with db_503():
        snap = await db.snapshot_row_at_or_before(at_t, source)
        if snap is None:
            raise HTTPException(404, "no snapshot at or before the requested time")
        states = await db.states_at(snap["snapshot_time"], source)
    return {
        "snapshot_time": snap["snapshot_time"],
        "ts": snap["ts"].isoformat(),
        "bbox": bbox_or_none(snap),
        "count": len(states),
        "source": "db",
        "requested_at": at_t,
        "states": [state_record_to_dict(r) for r in states],
    }


@router.get("/stats", response_model=StatsResponse, response_model_by_alias=True)
async def stats(
    request: Request,
    from_: str | None = Query(None, alias="from"),
    to: str | None = None,
    bucket: str = Query("1m", pattern="^(1m|5m|15m|1h|1d)$"),
    source: str = Query("opensky", pattern="^(opensky|airplanes_live|adsbexchange)$"),
) -> Any:
    bucket_s = STATS_BUCKETS[bucket]
    from_t, to_t = resolve_range(
        from_, to, default_span=3600, max_span=MAX_STATS_BUCKETS * bucket_s
    )
    db = _db(request)
    interval = timedelta(seconds=bucket_s)
    with db_503():
        pool = await db.pool()
        # Rollups come from the source-aware 1m continuous aggregate
        # (re-bucketed for coarser windows), filtered to one source so the
        # frozen /stats numbers stay OpenSky-only by default; per-snapshot
        # counts come from the small plain `snapshots` table.
        rows = await pool.fetch(
            """
            WITH agg AS (
                SELECT time_bucket($1::interval, bucket) AS b,
                       sum(state_count)        AS state_count,
                       sum(ground_count)       AS ground_count,
                       max(max_baro_altitude)  AS max_baro_altitude,
                       max(max_geo_altitude)   AS max_geo_altitude,
                       max(max_velocity)       AS max_velocity
                FROM state_vectors_1m
                WHERE source = $4
                  AND bucket >= to_timestamp($2::bigint)
                  AND bucket <  to_timestamp($3::bigint)
                GROUP BY b
            ), snaps AS (
                SELECT time_bucket($1::interval, ts) AS b,
                       count(*) AS snapshot_count
                FROM snapshots
                WHERE source = $4
                  AND ts >= to_timestamp($2::bigint)
                  AND ts <  to_timestamp($3::bigint)
                GROUP BY b
            )
            SELECT agg.*, coalesce(snaps.snapshot_count, 0) AS snapshot_count
            FROM agg LEFT JOIN snaps USING (b)
            ORDER BY b ASC
            """,
            interval, from_t, to_t, source,
        )

    buckets = []
    for r in rows:
        sc, gc, nsnap = int(r["state_count"]), int(r["ground_count"]), int(r["snapshot_count"])
        buckets.append(StatsBucket(
            bucket_start=r["b"].isoformat(),
            state_count=sc,
            ground_count=gc,
            airborne_count=sc - gc,
            max_baro_altitude=r["max_baro_altitude"],
            max_geo_altitude=r["max_geo_altitude"],
            max_velocity=r["max_velocity"],
            snapshot_count=nsnap,
            avg_aircraft_per_snapshot=round(sc / nsnap, 1) if nsnap else None,
        ))
    return {"from": from_t, "to": to_t, "bucket": bucket, "buckets": buckets}


MILITARY_STALE_S = settings.military_stale_s


@router.get("/military", response_model=MilitaryResponse)
async def military(request: Request) -> Any:
    """Live military overlay from the airplanes.live collector
    (tracker_airplanes.py). ARCHIVE FIRST like /live: military.json when
    fresh; otherwise the newest airplanes_live snapshot in state_vectors (the
    follow-ingester mirrors the same feed), so a dead collector process
    degrades to "DB, honestly aged" instead of a blank layer — this was the
    one overlay with no fallback (arch review finding 11). Metric units —
    normalized at collection. Never an error: DB trouble returns the file
    verdict. v1 ADDITIVE: `source` = archive|db."""
    path = settings.output_dir / "military.json"
    data = await asyncio.to_thread(read_latest_json, path)
    file_resp = MilitaryResponse(available=False)
    if data and isinstance(data.get("states"), list):
        st = await asyncio.to_thread(path.stat)
        age = time.time() - st.st_mtime
        if age <= MILITARY_STALE_S:
            return MilitaryResponse(
                available=True, source="archive",
                snapshot_time=data.get("snapshot_time"),
                age_seconds=round(age, 1),
                count=data.get("count", len(data["states"])),
                states=data["states"],
            )
        file_resp = MilitaryResponse(
            available=False, source="archive",
            snapshot_time=data.get("snapshot_time"), age_seconds=round(age, 1))
    try:
        db = _db(request)
        latest = await db.latest_source_snapshot("airplanes_live")
        if latest is not None:
            db_age = time.time() - latest
            if db_age <= MILITARY_STALE_S:
                rows = await db.military_states_at(latest)
                return MilitaryResponse(
                    available=True, source="db",
                    snapshot_time=latest, age_seconds=round(db_age, 1),
                    count=len(rows),
                    states=[state_record_to_dict(r) for r in rows],
                )
    except DB_ERRORS:
        pass
    return file_resp


ADSB_STALE_S = settings.adsb_stale_s  # adsb_scraper cycle cadence ~15 s; allow several misses


@router.get("/adsb", response_model=AdsbResponse)
async def adsb(request: Request) -> Any:
    """Live ADS-B Exchange overlay, DB-backed (the follow-ingester writes the
    external adsb_scraper archive into state_vectors). Serves the latest
    adsbexchange snapshot bounded to the AOI (INSIDE a country or NEAR a
    border), so every row carries the P3 geo classification. available=false
    when the newest snapshot is stale or the feed is off — never a 500."""
    db = _db(request)
    latest = await db.latest_source_snapshot("adsbexchange")
    if latest is None:
        return AdsbResponse(available=False)
    age = time.time() - latest
    if age > ADSB_STALE_S:
        return AdsbResponse(available=False, snapshot_time=latest, age_seconds=round(age, 1))
    states = await db.states_at(latest, "adsbexchange", aoi_only=True)
    return AdsbResponse(
        available=True,
        snapshot_time=latest,
        age_seconds=round(age, 1),
        count=len(states),
        states=[state_record_to_dict(r) for r in states],
    )
