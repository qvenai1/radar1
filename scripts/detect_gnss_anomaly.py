#!/usr/bin/env python3
"""Behavioural analysis G4a — GNSS-anomaly EPISODE detection (per-vessel).

Aggregates the per-fix implausible verdicts (position_quality/anomaly_flags,
migration 027) into per-vessel EPISODES and writes them to the generalized
maritime_events table as event_type='gnss_anomaly', classified by the Step-0
three-way rule (area-jamming / glitch / individual — see backend.gnss_anomaly_events).
No new detection logic beyond the pure module; this owns all I/O.

Pipeline (mirrors detect_rendezvous.py):
  * build the BROADCAST jamming zone in-process from every implausible position
    (distinct vessels per 0.1° cell x hourly window) — the classification input,
    an in-process artifact like coverage_mask (NOT a stored table/view: it is a
    derived aggregation over vessel_states used to classify, rebuilt each run;
    G4b's A1 fifth confound rebuilds it the same way);
  * per (mmsi, source) with any implausible fix: fetch its full fix stream, group
    contiguous implausible runs into episodes, classify each against the zone +
    whether the source has any plausible track;
  * also build the AFFECTED zone (last-good position before each episode) with feed
    outage windows SUBTRACTED — reported/tested here, consumed by G4b.

Idempotent recompute-and-replace: full run DELETE+insert (A2 discipline); --since
incremental UPSERTs episodes starting in the window; --dry-run writes nothing.

Usage:
    python scripts/detect_gnss_anomaly.py --dry-run
    python scripts/detect_gnss_anomaly.py
    python scripts/detect_gnss_anomaly.py --since 2026-07-19T00:00:00Z
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

import asyncpg
from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from backend.gap_detect import reconstruct_outage_windows  # noqa: E402
from backend.gnss_anomaly_events import (  # noqa: E402
    AREA_JAMMING, CANDIDATE, GLITCH, METHOD, AREA_MIN_VESSELS,
    Fix, JammingZone, affected_zone_cells, classify_episode, episodes,
)

from backend import track_source as TS  # noqa: E402

logger = logging.getLogger("detect_gnss_anomaly")

# Every implausible position -> the broadcast zone (mmsi, ts, cell) source rows.
IMPL_POS_SQL = """
SELECT vessel_key AS mmsi, ts, latitude, longitude
FROM vessel_tracks
WHERE source = '{source}'
  AND position_quality = 'implausible' AND latitude IS NOT NULL AND longitude IS NOT NULL
"""

# MMSIs that have >=1 implausible fix, with their plausible-fix count (0 => glitch
# source: no real track). Optionally bounded to a --since window on the implausible side.
MMSI_STATS_SQL = """
SELECT vessel_key AS mmsi,
       count(*) FILTER (WHERE position_quality = 'implausible')                AS imp,
       count(*) FILTER (WHERE position_quality IS DISTINCT FROM 'implausible') AS plausible
FROM vessel_tracks
WHERE source = '{source}' AND latitude IS NOT NULL AND longitude IS NOT NULL
GROUP BY vessel_key
HAVING count(*) FILTER (WHERE position_quality = 'implausible'
        AND ($1::timestamptz IS NULL OR ts >= $1)) > 0
"""

# Full fix stream for the implausible-carrying vessels (plausible fixes included so
# episodes break on recovery; ordered for grouping).
FIX_SQL = """
SELECT vessel_key AS mmsi, ts, latitude, longitude, position_quality, anomaly_flags,
       implied_speed_kn, inland_km
FROM vessel_tracks
WHERE source = '{source}' AND vessel_key = ANY($1::text[])
  AND latitude IS NOT NULL AND longitude IS NOT NULL
ORDER BY vessel_key, ts
"""

UPSERT = """
-- ⛔ THE SOURCE AND KEY MUST BE WRITTEN EXPLICITLY. Without them the value bound to the key
-- parameter lands in `mmsi` and `source` takes its DEFAULT 'aisstream' — so a MarineTraffic
-- run silently files its ship_ids as AIS MMSIs. That happened: 3,210 gnss_anomaly and 33
-- zone_dwell MT events were written as aisstream and the hull composite moved 319 -> 320,
-- i.e. MT data reached the composite the migration-043 guard exists to keep it out of. The
-- guard was never wrong; it filters on `source`, and the source was a lie.
INSERT INTO maritime_events (
    event_type, mmsi, ship_id, source, start_ts, end_ts, imo,
    start_lat, start_lon, end_lat, end_lon, duration_s,
    classification, method, detail, computed_at)
VALUES ('gnss_anomaly',
        CASE WHEN $13 = 'aisstream' THEN $1 END,
        CASE WHEN $13 = 'aisstream' THEN NULL ELSE $1 END,
        $13, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12::jsonb, now())
-- ⛔ CONFLICT TARGET UPDATED BY MIGRATION 043. The primary key became
-- (event_type, source, vessel_key, start_ts); the old (event_type, mmsi, start_ts) no
-- longer matches any constraint and every run failed with
-- "no unique or exclusion constraint matching the ON CONFLICT specification".
-- `source` is not listed in the INSERT because its column DEFAULT is 'aisstream', which
-- is exactly right for a detector that only reads the AIS track.
ON CONFLICT (event_type, source, vessel_key, start_ts) DO UPDATE SET
    end_ts=EXCLUDED.end_ts, imo=EXCLUDED.imo,
    start_lat=EXCLUDED.start_lat, start_lon=EXCLUDED.start_lon,
    end_lat=EXCLUDED.end_lat, end_lon=EXCLUDED.end_lon,
    duration_s=EXCLUDED.duration_s, classification=EXCLUDED.classification,
    method=EXCLUDED.method, detail=EXCLUDED.detail, computed_at=now()
"""


def _fix(r) -> Fix:
    return Fix(
        ts=r["ts"], lat=r["latitude"], lon=r["longitude"],
        implausible=(r["position_quality"] == "implausible"),
        flags=tuple(r["anomaly_flags"] or ()),
        implied_kn=r["implied_speed_kn"], inland_km=r["inland_km"],
    )


def _last_good_before(fixes: list[Fix], start_ts) -> Fix | None:
    """The last PLAUSIBLE fix strictly before an episode's start — the vessel's real
    location when jamming began (affected-zone origin)."""
    best = None
    for f in fixes:
        if f.ts >= start_ts:
            break
        if not f.implausible:
            best = f
    return best


async def run(dsn: str, since, dry_run: bool, source: str = TS.AIS) -> int:
    if source not in TS.SOURCES:
        logger.error("unknown source %r — expected one of %s", source, list(TS.SOURCES))
        return 2
    why = TS.refusal("gnss_anomaly", source)
    if why:
        logger.error("refusing to run gnss_anomaly on %s: %s", source, why)
        return 2
    conn = await asyncpg.connect(dsn)
    try:
        # 1) broadcast zone from every implausible position (in-process artifact).
        # ⛔ `.format(source=source)` IS LOAD-BEARING AND WAS MISSING. IMPL_POS_SQL carries a
        # `{source}` placeholder like MMSI_STATS_SQL below; fetched unformatted, the SQL that
        # reached Postgres filtered `source = '{source}'` LITERALLY and matched 0 rows where
        # 'aisstream' matches 10,712 (measured 2026-08-24). An empty zone makes
        # `zone.is_area()` false everywhere, so classify_episode() can never take its
        # area branch — and `decoy_match=True` is only reachable INSIDE that branch. The
        # store shows the cost: 0 area-jamming and 0 decoy episodes across all history,
        # where the same data yielded 326 area-jamming after GNSS-REVIVE. The detector kept
        # exiting 0 and writing thousands of `candidate` rows throughout, which is why
        # nothing surfaced it — two test assertions were the only thing still pointing here.
        zone = JammingZone.from_positions(
            (r["mmsi"], r["ts"], r["latitude"], r["longitude"])
            for r in await conn.fetch(IMPL_POS_SQL.format(source=source)))
        logger.info("broadcast jamming zone: %d area cell-windows (>= %d vessels)",
                    zone.area_cells, AREA_MIN_VESSELS)

        # 2) outage windows for the affected-zone subtraction (G3-addendum machinery).
        ev = await conn.fetch(
            "SELECT ts, kind FROM signal_events WHERE source='aisstream' "
            "AND kind IN ('feed_disconnect','feed_connect') ORDER BY ts")
        cts = [r["ts"] for r in ev if r["kind"] == "feed_connect"]
        lp = await conn.fetch(
            "SELECT c AS ct, (SELECT max(v.ts) FROM vessel_states v "
            "WHERE v.source='aisstream' AND v.ts < c) AS last_pos "
            "FROM unnest($1::timestamptz[]) AS c", cts)
        windows = reconstruct_outage_windows(
            [(r["ts"], r["kind"]) for r in ev], datetime.now(timezone.utc),
            {r["ct"]: r["last_pos"] for r in lp})
        outage_pairs = [(w.start, w.end) for w in windows]

        # 3) vessels with implausible fixes + plausible-track flag.
        stats = await conn.fetch(MMSI_STATS_SQL.format(source=source), since)
        has_plausible = {r["mmsi"]: (r["plausible"] > 0) for r in stats}
        mmsis = list(has_plausible)
        logger.info("vessels with implausible fixes: %d (%d have no plausible track)",
                    len(mmsis), sum(1 for v in has_plausible.values() if not v))

        # 4) fix streams, grouped per vessel.
        by_mmsi: dict[str, list[Fix]] = defaultdict(list)
        for r in await conn.fetch(FIX_SQL.format(source=source), mmsis):
            by_mmsi[r["mmsi"]].append(_fix(r))
        idrows = await conn.fetch(
            "SELECT mmsi, imo FROM vessel_identity WHERE mmsi = ANY($1::text[])", mmsis)
        imo_of = {r["mmsi"]: r["imo"] for r in idrows}

        rows, breakdown, by_type, origins = [], Counter(), Counter(), []
        for mmsi in mmsis:
            fixes = by_mmsi.get(mmsi, [])
            for ep in episodes(fixes):
                if since is not None and ep.start_ts < since:
                    continue                       # incremental: only in-window episodes
                cls, gtype, extra = classify_episode(ep, zone, has_plausible[mmsi])
                breakdown[cls] += 1
                by_type[gtype] += 1
                lg = _last_good_before(fixes, ep.start_ts)
                if lg is not None:
                    origins.append((mmsi, lg.ts, lg.lat, lg.lon))
                detail = {
                    "gnss_type": gtype,
                    "n_implausible_fixes": ep.n_fixes,
                    "anomaly_flags": list(ep.flags),
                    "implied_speed_max": ep.implied_speed_max,
                    "inland_km_max": ep.inland_km_max,
                    "affected_lat": (round(lg.lat, 5) if lg else None),
                    "affected_lon": (round(lg.lon, 5) if lg else None),
                    **extra,
                }
                dur = (ep.end_ts - ep.start_ts).total_seconds()
                rows.append((mmsi, ep.start_ts, ep.end_ts, imo_of.get(mmsi),
                             ep.start_lat, ep.start_lon, ep.end_lat, ep.end_lon,
                             dur, cls, METHOD, json.dumps(detail), source))

        # 5) affected zone (outage-subtracted) — reported; consumed by G4b.
        affected = affected_zone_cells(origins, outage_pairs)
        aff_area = sum(1 for n in affected.values() if n >= AREA_MIN_VESSELS)

        logger.info("=" * 60)
        logger.info("GNSS-ANOMALY EPISODES: %d", len(rows))
        for c in (CANDIDATE, AREA_JAMMING, GLITCH):
            logger.info("  classification %-13s %d", c, breakdown.get(c, 0))
        logger.info("  by gnss_type: %s", dict(by_type))
        logger.info("  affected-zone cells: %d (%d area), outage-subtracted", len(affected), aff_area)
        logger.info("=" * 60)

        if dry_run:
            logger.info("--dry-run: nothing written.")
            return 0
        async with conn.transaction():
            if since is None:
                # ⚠ Scoped by SOURCE. The window scope the other detectors need is not
                # required here: this branch runs only when `since is None` (a full scan),
                # so a full per-source delete is exactly what it can rebuild.
                await conn.execute(
                    "DELETE FROM maritime_events WHERE event_type='gnss_anomaly' "
                    "AND source=$1", source)
            await conn.executemany(UPSERT, rows)
        total = await conn.fetchval(
            "SELECT count(*) FROM maritime_events WHERE event_type='gnss_anomaly' AND source=$1", source)
        logger.info("wrote %d gnss_anomaly events (%s); rows now: %d", len(rows),
                    "full replace" if since is None else "incremental upsert", total)
        return 0
    finally:
        await conn.close()


def _parse_since(since: str | None):
    if not since:
        return None
    try:
        return datetime.fromtimestamp(int(since), tz=timezone.utc)
    except ValueError:
        dt = datetime.fromisoformat(since.replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def main() -> int:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s | %(levelname)-7s | %(message)s", datefmt="%H:%M:%S")
    load_dotenv(REPO_ROOT / ".env")
    p = argparse.ArgumentParser(description="G4a GNSS-anomaly episode detection")
    p.add_argument("--since", type=str, default=None,
                   help="only episodes starting at/after this ISO-8601/epoch (incremental)")
    p.add_argument("--dry-run", action="store_true", help="report the funnel, write nothing")
    p.add_argument("--source", default=TS.AIS, choices=list(TS.SOURCES),
                   help="which track to read (default aisstream); stored and recomputed "
                        "INDEPENDENTLY per source")
    a = p.parse_args()
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        print("ERROR: DATABASE_URL not set (env or .env)", file=sys.stderr)
        return 2
    return asyncio.run(run(dsn, _parse_since(a.since), a.dry_run, a.source))


if __name__ == "__main__":
    sys.exit(main())
