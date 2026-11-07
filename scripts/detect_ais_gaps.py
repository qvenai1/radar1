#!/usr/bin/env python3
"""Behavioural analysis A1 — AIS-gap CANDIDATE detection (confound-removed).

Over vessel_states history: LAG per MMSI to find consecutive-position intervals
>= the gap threshold (CLOSED gaps — the vessel reappeared). Each gap is
classified by backend.gap_detect after removing the three confounds:
  * feed outages (reconstructed from signal_events feed_disconnect/connect),
  * stationary/dedup (nav_status/speed at the last fix),
  * coverage edge (the data-driven backend.coverage_mask, handling Cyprus).
Only underway + dense-coverage + not-outage gaps are 'candidate'; every other
gap is stored with its honest reason. Identity (imo/name/flag) is attached from
the M2a/M2b spine; the M1 columns say whose waters it went dark in.

G3 — the position stream EXCLUDES implausible fixes (coverage_mask.PLAUSIBLE_FILTER:
position_quality IS DISTINCT FROM 'implausible'; NULL and 'suspect' pass) BEFORE the
LEAD gap construction, and the coverage mask is built with the same exclusion. So a
gap can neither start nor end at a spoofed/teleport fix, and a vessel whose only
"activity" was a GNSS teleport cannot fabricate an underway state. Flag, never delete
— the rows keep everything; A1 just ignores the implausible ones.

Idempotent: recompute-and-replace on the natural key (event_type, mmsi,
start_ts) via ON CONFLICT DO UPDATE — a re-run replaces, never duplicates.

INCREMENTAL: this same script with --since <ISO/epoch> re-detects only gaps
whose start_ts is within the recent window; a systemd timer runs it that way, so
the batch is the incremental engine too (no separate stateful path). A currently-
OPEN gap becomes a candidate once it closes (bounded by the timer interval).

Usage:
    python scripts/detect_ais_gaps.py --dry-run        # funnel report, write nothing
    python scripts/detect_ais_gaps.py                  # full history
    python scripts/detect_ais_gaps.py --since 2026-07-18T00:00:00Z   # incremental window
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

from backend.coverage_mask import PLAUSIBLE_FILTER, build_coverage_mask  # noqa: E402
from backend.gap_detect import (  # noqa: E402
    CANDIDATE, GAP_THRESHOLD_S, JAMMING_EXCLUDED, METHOD, OUTAGE_EXCLUDED,
    classify_gap, overlapping_window, reconstruct_outage_windows,
)
from backend.gnss_anomaly_events import (  # noqa: E402
    AREA_MIN_VESSELS, Fix, affected_zone_cells, episodes, window_key, zone_cell,
)

logger = logging.getLogger("detect_ais_gaps")

# CLOSED gaps: each row = a last-seen fix (gap start) with the NEXT fix (gap end)
# for the same vessel, where the interval >= threshold. Joined to the identity
# spine; M1 columns carry whose waters the start fix was in.
GAP_SQL = f"""
WITH seq AS (
  SELECT mmsi, ts, latitude, longitude, speed, nav_status,
         country, near_country, distance_km,
         LEAD(ts)        OVER w AS next_ts,
         LEAD(latitude)  OVER w AS next_lat,
         LEAD(longitude) OVER w AS next_lon
  FROM vessel_states
  WHERE {PLAUSIBLE_FILTER}      -- G3: exclude implausible fixes BEFORE gap construction,
  WINDOW w AS (PARTITION BY mmsi ORDER BY ts)  -- so a gap can't start/end at a spoofed
)                                              -- fix and a teleport can't fake underway state

SELECT s.mmsi, s.ts AS start_ts, s.next_ts AS end_ts,
       s.latitude AS start_lat, s.longitude AS start_lon,
       s.next_lat AS end_lat, s.next_lon AS end_lon,
       EXTRACT(EPOCH FROM (s.next_ts - s.ts)) AS gap_s,
       s.speed, s.nav_status, s.country, s.near_country, s.distance_km,
       vi.imo, vi.name, vi.flag_country
FROM seq s
LEFT JOIN vessel_identity vi ON vi.mmsi = s.mmsi
WHERE s.next_ts IS NOT NULL
  AND EXTRACT(EPOCH FROM (s.next_ts - s.ts)) >= {int(GAP_THRESHOLD_S)}
  AND ($1::timestamptz IS NULL OR s.ts >= $1)
"""

UPSERT = """
INSERT INTO maritime_events (
    event_type, mmsi, start_ts, end_ts, imo,
    start_lat, start_lon, end_lat, end_lon, duration_s,
    classification, method, detail, computed_at)
VALUES ('ais_gap', $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12::jsonb, now())
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


async def run(dsn: str, since: str | None, dry_run: bool) -> int:
    conn = await asyncpg.connect(dsn)
    try:
        mask = await build_coverage_mask(conn)
        logger.info("coverage mask: %s", mask.params)
        ev = await conn.fetch(
            "SELECT ts, kind FROM signal_events "
            "WHERE source = 'aisstream' AND kind IN ('feed_disconnect','feed_connect') "
            "ORDER BY ts")
        # Liveness bound for inferred (unpaired-connect) windows: the last archived
        # AIS position strictly before each feed_connect (G3 Finding B — the outage
        # is only from the last live position to the reconnect, not the whole span).
        connect_ts = [r["ts"] for r in ev if r["kind"] == "feed_connect"]
        lp = await conn.fetch(
            "SELECT c AS connect_ts, (SELECT max(v.ts) FROM vessel_states v "
            "WHERE v.source = 'aisstream' AND v.ts < c) AS last_pos "
            "FROM unnest($1::timestamptz[]) AS c", connect_ts)
        last_position_before = {r["connect_ts"]: r["last_pos"] for r in lp}
        now = datetime.now(timezone.utc)
        windows = reconstruct_outage_windows(
            [(r["ts"], r["kind"]) for r in ev], now, last_position_before)
        out_h = sum((w.end - w.start).total_seconds() for w in windows) / 3600.0
        n_inferred = sum(1 for w in windows if w.inferred)
        n_open = sum(1 for w in windows if w.basis == "recorded-open")
        logger.info("feed-outage windows: %d (%.1fh total) — %d recorded, %d inferred "
                    "(unpaired-connect), %d open (ongoing to run time)",
                    len(windows), out_h, len(windows) - n_inferred, n_inferred, n_open)

        # --- fifth confound (G4b): the GNSS-anomaly jamming context -------------
        # Rebuilt IN-PROCESS from vessel_states (mask discipline; no dependency on the
        # gnss detector having run). Two signals mark a gap as a GNSS-anomaly artifact:
        #   (a) spanned-implausible — the vessel transmitted an implausible fix DURING
        #       the gap (G3 filtered it out of the stream, so the plausible-stream
        #       silence is not real going-dark — a spoofing artifact), and
        #   (b) active-zone — the gap's start/end falls in an active area-jamming zone
        #       x window (the G4a affected-zone product: distinct jammed vessels per
        #       cell x window, with feed-outage windows SUBTRACTED so an outage silence
        #       never reads as a jam).
        imp_mmsis = [r["mmsi"] for r in await conn.fetch(
            "SELECT mmsi FROM vessel_states WHERE latitude IS NOT NULL GROUP BY mmsi "
            "HAVING count(*) FILTER (WHERE position_quality='implausible') > 0")]
        streams: dict[str, list[Fix]] = defaultdict(list)
        imp_ts: dict[str, list] = defaultdict(list)
        for r in await conn.fetch(
                "SELECT mmsi, ts, latitude, longitude, position_quality, anomaly_flags, "
                "implied_speed_kn, inland_km FROM vessel_states "
                "WHERE mmsi = ANY($1::text[]) AND latitude IS NOT NULL ORDER BY mmsi, ts",
                imp_mmsis):
            imp = r["position_quality"] == "implausible"
            streams[r["mmsi"]].append(Fix(r["ts"], r["latitude"], r["longitude"], imp,
                                          tuple(r["anomaly_flags"] or ()),
                                          r["implied_speed_kn"], r["inland_km"]))
            if imp:
                imp_ts[r["mmsi"]].append(r["ts"])
        origins = []
        for m, fixes in streams.items():
            for ep in episodes(fixes):
                lg = next((f for f in reversed(fixes) if f.ts < ep.start_ts and not f.implausible), None)
                if lg is not None:
                    origins.append((m, lg.ts, lg.lat, lg.lon))
        outage_pairs = [(w.start, w.end) for w in windows]
        affected = affected_zone_cells(origins, outage_pairs)
        active_zones = {cw for cw, n in affected.items() if n >= AREA_MIN_VESSELS}
        logger.info("jamming context: %d affected-zone cells, %d active (>= %d vessels)",
                    len(affected), len(active_zones), AREA_MIN_VESSELS)

        def _spans_implausible(mmsi, s_ts, e_ts):
            return e_ts is not None and any(s_ts < t < e_ts for t in imp_ts.get(mmsi, ()))

        def _in_active_zone(lat, lon, ts):
            return lat is not None and (*zone_cell(lat, lon), window_key(ts)) in active_zones

        since_dt = _parse_since(since)
        gaps = await conn.fetch(GAP_SQL, since_dt)
        logger.info("closed gaps >= %.0fmin%s: %d", GAP_THRESHOLD_S / 60,
                    f" since {since}" if since else " (full history)", len(gaps))

        rows, breakdown = [], Counter()
        for g in gaps:
            spans = _spans_implausible(g["mmsi"], g["start_ts"], g["end_ts"])
            zone = (_in_active_zone(g["start_lat"], g["start_lon"], g["start_ts"])
                    or (g["end_ts"] is not None
                        and _in_active_zone(g["end_lat"], g["end_lon"], g["end_ts"])))
            cls = classify_gap(
                gap_start=g["start_ts"], gap_end=g["end_ts"],
                start_lat=g["start_lat"], start_lon=g["start_lon"],
                nav_status=g["nav_status"], speed=g["speed"],
                mask=mask, outage_windows=windows,
                in_jamming_zone=(spans or bool(zone)))
            breakdown[cls] += 1
            detail = {
                "distance_at_start_km": g["distance_km"],
                "nav_status": g["nav_status"],
                "speed_at_last_fix": g["speed"],
                "coverage_cell_density": mask.cell_density(g["start_lat"], g["start_lon"]),
                "start_country": g["country"],
                "start_near_country": g["near_country"],
                "name": g["name"],
                "flag_country": g["flag_country"],
            }
            if cls == OUTAGE_EXCLUDED:
                # Provenance: was this excluded by a RECORDED outage or an INFERRED
                # one (open/unpaired-connect)? Lets a study weight recorded vs inferred.
                w = overlapping_window(g["start_ts"], g["end_ts"], windows)
                if w is not None:
                    detail["outage_inferred"] = w.inferred
                    detail["outage_basis"] = w.basis
            elif cls == JAMMING_EXCLUDED:
                # Fifth confound provenance — which signal(s) fired, honestly labelled.
                detail["jamming_basis"] = [b for b, on in
                                           (("spanned-implausible", spans), ("active-zone", zone)) if on]
                if spans:
                    detail["implausible_fixes_in_gap"] = sum(
                        1 for t in imp_ts.get(g["mmsi"], ()) if g["start_ts"] < t < g["end_ts"])
            rows.append((g["mmsi"], g["start_ts"], g["end_ts"], g["imo"],
                         g["start_lat"], g["start_lon"], g["end_lat"], g["end_lon"],
                         g["gap_s"], cls, METHOD, json.dumps(detail)))

        logger.info("=" * 60)
        logger.info("FUNNEL / classification breakdown:")
        for k in ("candidate", "edge", "stationary", "outage-excluded", "jamming-excluded"):
            logger.info("  %-16s %d", k, breakdown.get(k, 0))
        logger.info("  CANDIDATES: %d", breakdown.get(CANDIDATE, 0))
        logger.info("=" * 60)

        if dry_run:
            logger.info("--dry-run: nothing written.")
            return 0
        async with conn.transaction():
            # A FULL run is an authoritative recompute-and-replace: clear ais_gap
            # first so gaps that VANISH (e.g. a gap whose start fix is now an
            # excluded implausible/spoofed fix) leave no stale row — matching A2's
            # delete+insert discipline. An incremental --since run keeps UPSERT
            # (it must not drop gaps outside its window); vanished gaps inside the
            # window are reconciled by the next full run.
            if since_dt is None:
                await conn.execute("DELETE FROM maritime_events WHERE event_type='ais_gap'")
            await conn.executemany(UPSERT, rows)
        total = await conn.fetchval(
            "SELECT count(*) FROM maritime_events WHERE event_type='ais_gap'")
        logger.info("wrote %d gap events (%s); maritime_events ais_gap rows now: %d",
                    len(rows), "full replace" if since_dt is None else "incremental upsert", total)
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
    p = argparse.ArgumentParser(description="A1 AIS-gap candidate detection (confound-removed)")
    p.add_argument("--since", type=str, default=None,
                   help="only gaps starting at/after this ISO-8601 / epoch (incremental window)")
    p.add_argument("--dry-run", action="store_true", help="report the funnel, write nothing")
    a = p.parse_args()
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        print("ERROR: DATABASE_URL not set (env or .env)", file=sys.stderr)
        return 2
    return asyncio.run(run(dsn, a.since, a.dry_run))


if __name__ == "__main__":
    sys.exit(main())
