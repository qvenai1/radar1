#!/usr/bin/env python3
"""
Reentry/decay collector — Space-Track TIP (Tracking & Impact Prediction) → DB,
geo-linked to scraplan's seven-country AOI. Satellite layer S2c.

Architecture (consistent with the rest of scraplan):
    S2a client (auth + session reuse + rate limit) → query `tip`
    → satellite_output/reentry.jsonl   (append — durable ground truth, FIRST)
    → parse + geo-classify (shared backend/geo_area.py)
    → satellite_reentry (idempotent DB mirror, ON CONFLICT (id))
    → signal_events (collector_run)

TIP is the geo-bearing reentry class (it carries predicted LAT/LON); the `decay`
class has DECAY_EPOCH + identity but no impact point, so it can't be geo-linked.
Object NAME/COUNTRY/TYPE are NOT stored here — they come from the S1 join to
satellite_info by norad_id.

RATE LIMIT: `tip`/`decay` are ~1/day classes — this collector runs AT MOST ONCE
DAILY (systemd timer). The S2a client owns auth, session reuse, the rate limiter,
and backoff; this module never re-implements them.

SCOPE: TIP for DECAY_EPOCH within the last `--lookback-days` (default 60) through
the future — i.e. recently-reentered + all upcoming predictions. Reentry is a
small, sporadic set, so this whole window is a tiny daily pull (not bulk).

The predicted impact point is a SINGLE nominal point with LARGE uncertainty;
window_min (± minutes) and next_report are preserved so the imprecision is
visible. LON is normalized to [-180,180] (Space-Track reports 0..360 E).

Usage
    SPACETRACK_USER=... SPACETRACK_PASS=... python tracker_reentry.py
    python tracker_reentry.py --lookback-days 90
    python tracker_reentry.py backfill      # rebuild satellite_reentry from JSONL
"""

from __future__ import annotations

import argparse
import asyncio
import gzip
import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import asyncpg

from archive import RawLog, emit_signal
from backend.geo_area import GEO_SOURCE, get_area

logger = logging.getLogger("reentry")

SOURCE = "spacetrack_tip"

UPSERT = """
INSERT INTO satellite_reentry
    (id, norad_id, msg_epoch, insert_epoch, decay_epoch, window_min, lat, lon,
     direction, incl, rev, next_report, high_interest,
     country, near_country, distance_km, geo_source, source, fetched_at)
VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18, now())
ON CONFLICT (id) DO UPDATE SET
    norad_id=EXCLUDED.norad_id, msg_epoch=EXCLUDED.msg_epoch,
    insert_epoch=EXCLUDED.insert_epoch, decay_epoch=EXCLUDED.decay_epoch,
    window_min=EXCLUDED.window_min, lat=EXCLUDED.lat, lon=EXCLUDED.lon,
    direction=EXCLUDED.direction, incl=EXCLUDED.incl, rev=EXCLUDED.rev,
    next_report=EXCLUDED.next_report, high_interest=EXCLUDED.high_interest,
    country=EXCLUDED.country, near_country=EXCLUDED.near_country,
    distance_km=EXCLUDED.distance_km, geo_source=EXCLUDED.geo_source,
    source=EXCLUDED.source, fetched_at=now()
-- idempotent: a same-id re-fetch that changed nothing is a 0-row no-op, so
-- fetched_at is bumped only on a real correction.
WHERE (
    satellite_reentry.norad_id     IS DISTINCT FROM EXCLUDED.norad_id     OR
    satellite_reentry.msg_epoch    IS DISTINCT FROM EXCLUDED.msg_epoch    OR
    satellite_reentry.insert_epoch IS DISTINCT FROM EXCLUDED.insert_epoch OR
    satellite_reentry.decay_epoch  IS DISTINCT FROM EXCLUDED.decay_epoch  OR
    satellite_reentry.window_min   IS DISTINCT FROM EXCLUDED.window_min   OR
    satellite_reentry.lat          IS DISTINCT FROM EXCLUDED.lat          OR
    satellite_reentry.lon          IS DISTINCT FROM EXCLUDED.lon          OR
    satellite_reentry.direction    IS DISTINCT FROM EXCLUDED.direction    OR
    satellite_reentry.incl         IS DISTINCT FROM EXCLUDED.incl         OR
    satellite_reentry.rev          IS DISTINCT FROM EXCLUDED.rev          OR
    satellite_reentry.next_report  IS DISTINCT FROM EXCLUDED.next_report  OR
    satellite_reentry.high_interest IS DISTINCT FROM EXCLUDED.high_interest OR
    satellite_reentry.country      IS DISTINCT FROM EXCLUDED.country      OR
    satellite_reentry.near_country IS DISTINCT FROM EXCLUDED.near_country OR
    satellite_reentry.distance_km  IS DISTINCT FROM EXCLUDED.distance_km
)
"""


# -- pure parsing / geo (no I/O — unit-testable) -----------------------------

def _num(v: Any) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _int(v: Any) -> int | None:
    try:
        return int(str(v).strip())
    except (TypeError, ValueError):
        return None


def _dt(v: Any) -> datetime | None:
    """Space-Track 'YYYY-MM-DD HH:MM:SS' (UTC, hour may be single-digit) → aware
    datetime, or None. Never raises."""
    if not isinstance(v, str) or not v.strip():
        return None
    s = v.strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(s, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def _norm_lon(lon: float | None) -> float | None:
    """Space-Track TIP LON is 0..360 E; normalize to [-180,180] (same point)."""
    if lon is None:
        return None
    lon = ((lon + 180.0) % 360.0) - 180.0
    return lon


def parse_tip_record(rec: dict[str, Any]) -> dict[str, Any] | None:
    """One raw TIP JSON record → normalized reentry dict, or None if unkeyable
    (missing message ID or NORAD id). Malformed sub-fields degrade to None; the
    row is kept for its identity/time (imprecision is not a reason to drop)."""
    if not isinstance(rec, dict):
        return None
    msg_id = _int(rec.get("ID"))
    norad = _int(rec.get("NORAD_CAT_ID"))
    if msg_id is None or norad is None:
        return None
    hi = rec.get("HIGH_INTEREST")
    return {
        "id": msg_id,
        "norad_id": norad,
        "msg_epoch": _dt(rec.get("MSG_EPOCH")),
        "insert_epoch": _dt(rec.get("INSERT_EPOCH")),
        "decay_epoch": _dt(rec.get("DECAY_EPOCH")),
        "window_min": _int(rec.get("WINDOW")),
        "lat": _num(rec.get("LAT")),
        "lon": _norm_lon(_num(rec.get("LON"))),
        "direction": (rec.get("DIRECTION") or None),
        "incl": _num(rec.get("INCL")),
        "rev": _int(rec.get("REV")),
        "next_report": _int(rec.get("NEXT_REPORT")),
        "high_interest": (hi == "Y") if isinstance(hi, str) and hi else None,
    }


def classify_reentry(row: dict[str, Any], area: Any) -> dict[str, Any]:
    """Add geo columns for the NOMINAL predicted impact point using the shared
    geo_area classifier. Leaves them NULL when there is no lat/lon to classify."""
    lat, lon = row.get("lat"), row.get("lon")
    if lat is None or lon is None:
        return {**row, "country": None, "near_country": None,
                "distance_km": None, "geo_source": None}
    gc = area.classify_columns(lat, lon)
    return {**row, "country": gc.country, "near_country": gc.near_country,
            "distance_km": gc.distance_km, "geo_source": GEO_SOURCE}


def reentry_row(r: dict[str, Any]) -> tuple:
    """Classified reentry dict → UPSERT parameter tuple (matches column order)."""
    return (
        r["id"], r["norad_id"], r.get("msg_epoch"), r.get("insert_epoch"),
        r.get("decay_epoch"), r.get("window_min"), r.get("lat"), r.get("lon"),
        r.get("direction"), r.get("incl"), r.get("rev"), r.get("next_report"),
        r.get("high_interest"), r.get("country"), r.get("near_country"),
        r.get("distance_km"), r.get("geo_source"), SOURCE,
    )


# -- DB ----------------------------------------------------------------------

async def _upsert_rows(conn: asyncpg.Connection, rows: list[dict[str, Any]]) -> int:
    """Idempotent per-row upsert; returns how many rows were inserted/updated."""
    changed = 0
    for r in rows:
        status = await conn.execute(UPSERT, *reentry_row(r))
        changed += int(status.rsplit(" ", 1)[-1])
    return changed


# -- collector ---------------------------------------------------------------

async def run(dsn: str, lookback_days: int, out_dir: Path) -> int:
    from backend.spacetrack_client import SpaceTrackClient, SpaceTrackConfig

    cfg = SpaceTrackConfig.from_env()
    if not cfg.has_credentials:
        logger.error("SPACETRACK_USER / SPACETRACK_PASS not set — see .env.example")
        return 2

    out_dir.mkdir(parents=True, exist_ok=True)
    raw_log = RawLog(out_dir / "reentry.jsonl")     # ground truth (raw TIP records)
    op_signals = RawLog(out_dir / "signals.jsonl")  # operational signal log
    fetched_at = datetime.now(timezone.utc).isoformat()

    area = get_area()                               # shared AOI classifier (fail-fast)
    client = SpaceTrackClient(cfg)
    conn = await asyncpg.connect(dsn)
    inside = near = outside = 0
    try:
        raw = await client.query("tip", DECAY_EPOCH=f">now-{lookback_days}", fmt="json")
        records = raw if isinstance(raw, list) else []
        # ARCHIVE FIRST — every raw TIP record (+ fetch stamp) to the JSONL log.
        raw_log.append_many([{**rec, "_fetched_at": fetched_at} for rec in records])

        classified: list[dict[str, Any]] = []
        for rec in records:
            parsed = parse_tip_record(rec)
            if parsed is None:
                continue
            row = classify_reentry(parsed, area)
            classified.append(row)
            if row["country"]:
                inside += 1
            elif row["near_country"]:
                near += 1
            else:
                outside += 1

        changed = await _upsert_rows(conn, classified)
        await emit_signal(op_signals, conn, "spacetrack", "collector_run", {
            "class": "tip", "lookback_days": lookback_days,
            "records": len(records), "ingested": len(classified),
            "changed": changed, "inside_aoi": inside, "near_aoi": near, "outside_aoi": outside,
        })
        logger.info("=" * 56)
        logger.info("REENTRY (TIP) PULL: %d records, %d rows, %d changed",
                    len(records), len(classified), changed)
        logger.info("  geo: INSIDE=%d NEAR=%d OUTSIDE=%d (nominal predicted point)",
                    inside, near, outside)
        logger.info("  health: %s", client.health())
        logger.info("=" * 56)
        return 0
    finally:
        await client.close()
        await conn.close()


def _open(path: Path):
    return gzip.open(path, "rt", encoding="utf-8") if path.suffix == ".gz" \
        else path.open(encoding="utf-8")


async def backfill(dsn: str, archive: Path, lookback_days: int) -> int:
    """Rebuild satellite_reentry from the raw reentry.jsonl log(s), idempotently.
    Re-parses + re-classifies (geo) every archived TIP record."""
    files = sorted(archive.glob("reentry*.jsonl*")) if archive.is_dir() else [archive]
    files = [f for f in files if f.exists()]
    if not files:
        logger.error("no reentry archive found at %s", archive)
        return 2
    area = get_area()
    conn = await asyncpg.connect(dsn)
    try:
        before = await conn.fetchval("SELECT count(*) FROM satellite_reentry")
        rows: list[dict[str, Any]] = []
        read = 0
        for path in files:
            with _open(path) as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    read += 1
                    try:
                        rec = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    parsed = parse_tip_record(rec)
                    if parsed is not None:
                        rows.append(classify_reentry(parsed, area))
        changed = await _upsert_rows(conn, rows)
        after = await conn.fetchval("SELECT count(*) FROM satellite_reentry")
        logger.info("reentry backfill: %d lines, %d valid, rows %d→%d (+%d), %d changed",
                    read, len(rows), before, after, after - before, changed)
        return 0
    finally:
        await conn.close()


def main() -> int:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
                        datefmt="%H:%M:%S")
    from contextlib import suppress
    with suppress(Exception):
        from dotenv import load_dotenv
        load_dotenv()

    p = argparse.ArgumentParser(description="Space-Track TIP reentry collector (geo-linked)")
    p.add_argument("--lookback-days", type=int,
                   default=int(os.environ.get("REENTRY_LOOKBACK_DAYS", "60")),
                   help="pull TIP with DECAY_EPOCH within the last N days + future")
    p.add_argument("--output", type=Path, default=Path("satellite_output"))
    sub = p.add_subparsers(dest="cmd")
    pb = sub.add_parser("backfill", help="rebuild satellite_reentry from reentry.jsonl")
    pb.add_argument("--archive", type=Path, default=Path("satellite_output"))
    a = p.parse_args()

    dsn = os.getenv("OPENSKY_DB_URL") or os.getenv("DATABASE_URL", "")
    if not dsn:
        print("ERROR: set OPENSKY_DB_URL or DATABASE_URL", file=sys.stderr)
        return 2
    if a.cmd == "backfill":
        return asyncio.run(backfill(dsn, a.archive, a.lookback_days))
    return asyncio.run(run(dsn, a.lookback_days, a.output))


if __name__ == "__main__":
    sys.exit(main())
