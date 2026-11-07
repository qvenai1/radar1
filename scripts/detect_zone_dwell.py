#!/usr/bin/env python3
"""ZONES-3 — write the STRONG subset of zone dwell to maritime_events.

Mirrors the detect_rendezvous.py discipline: --dry-run first, recompute-and-replace so
re-running is idempotent (0 changed on the second run), time-bounded windows, the pure
module (backend.zone_dwell) owns the rules. ZONES-2's annotation endpoint stays the full
ranked surface; this runner writes ONLY the strong, small subset that earns composite
admission — nothing routine floods maritime_events.

Admission, scoped by evidence strength (ZONES-3):
  * safety_zone_intrusion -> ADMIT. Rare by construction (a few a week), on SURVEYED
    platform points (radius_basis 'sourced', 500 m per UNCLOS Art. 60(5)), strong on its
    own. Written event_type='zone_dwell', classification='candidate'.
  * standoff_dwell -> admit ONLY the baseline-UNFAMILIAR subset, and ONLY if the per-zone
    familiarity baseline (backend.zone_baseline) is data-SOUND. NO per-zone allowlist:
    the routine is suppressed by measurement (the coverage_mask.py precedent), never by
    silencing a location a priori. When the baseline is under-determined (the case today:
    1 of 17 trafficked zones reaches the vessel+session bars, none reaches 30 days), admit
    NONE of standoff — it stays annotation-only until the data supports a baseline.
  * field_dwell -> never (assumed-radius centroids, the weakest class).

G3 (position_quality IS DISTINCT FROM 'implausible') is applied in SQL; the A2 port/busy
masks are reused as the anchorage subtractor; the undetectable (mis-sited) zone is
excluded by detectable_zones(). Duration is a LOWER BOUND under feed outage.

Usage:
    python scripts/detect_zone_dwell.py --dry-run    # funnel + baseline verdict, write nothing
    python scripts/detect_zone_dwell.py
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import math
import os
import sys
from collections import Counter, defaultdict
from pathlib import Path

import asyncpg
from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from backend import zones as zone_gaz  # noqa: E402
from backend import zone_baseline as zbase  # noqa: E402
from backend.coverage_mask import build_busy_mask, build_port_mask  # noqa: E402
from backend import track_source as TS  # noqa: E402
from backend.zone_dwell import (  # noqa: E402
    METHOD, SAFETY_ZONE_INTRUSION, STANDOFF_DWELL,
    CANDIDATE_CAVEAT, admit_for_composite, zone_dwell_sessions,
)

logger = logging.getLogger("detect_zone_dwell")

DEFAULT_WINDOW_HOURS = 720            # ~30 days — the full retention; time-bounded always

# ⛔ MT-DETECT-2: reads `vessel_tracks`, keyed generically. The source is INTERPOLATED, not
# bound — with `source = $N` the planner cannot prune the UNION's other branch and evaluates
# both compressed hypertables (measured on detect_rendezvous: a 1-day scan did not finish in
# 90 s parameterised, 76 s inlined). The value is checked against TS.SOURCES first.
#
# ⚠ THE IDENTITY JOIN IS AIS-ONLY. `vessel_identity` is keyed by MMSI; an MT track has none,
# so length/beam/imo_quality are legitimately NULL there and the type-verifiability test
# below degrades to "unverifiable" rather than silently passing. That is the honest outcome:
# MT cannot support the dimension checks, and says so per session.
ZONE_FIX_SQL = """
SELECT t.vessel_key AS mmsi, t.ts, t.latitude, t.longitude, t.implied_speed_kn, t.speed,
       t.ship_type AS vs_type, vi.ship_type AS vi_type, vi.length_m, vi.beam_m,
       vi.imo_quality, vi.imo_flags, vi.imo
FROM vessel_tracks t
LEFT JOIN vessel_identity vi ON vi.mmsi = t.vessel_key AND t.key_kind = 'mmsi'
WHERE t.source = '{source}'
  AND t.ts > now() - make_interval(hours => $1)
  AND t.position_quality IS DISTINCT FROM 'implausible'
  AND point(t.longitude, t.latitude) <@ box(point($2, $3), point($4, $5))
ORDER BY t.vessel_key, t.ts
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
VALUES ('zone_dwell',
        CASE WHEN $10 = 'aisstream' THEN $1 END,
        CASE WHEN $10 = 'aisstream' THEN NULL ELSE $1 END,
        $10, $2, $3, $4, $5, $6, NULL, NULL, $7, 'candidate', $8, $9::jsonb, now())
-- ⛔ CONFLICT TARGET UPDATED BY MIGRATION 043. The primary key became
-- (event_type, source, vessel_key, start_ts); the old (event_type, mmsi, start_ts) no
-- longer matches any constraint and every run failed with
-- "no unique or exclusion constraint matching the ON CONFLICT specification".
-- `source` is not listed in the INSERT because its column DEFAULT is 'aisstream', which
-- is exactly right for a detector that only reads the AIS track.
ON CONFLICT (event_type, source, vessel_key, start_ts) DO UPDATE SET
    end_ts=EXCLUDED.end_ts, imo=EXCLUDED.imo,
    start_lat=EXCLUDED.start_lat, start_lon=EXCLUDED.start_lon,
    duration_s=EXCLUDED.duration_s, method=EXCLUDED.method,
    detail=EXCLUDED.detail, computed_at=now()
"""

STANDOFF_OUTER_M = 3000.0


def _type_unverifiable(length_m, beam_m, imo_quality, imo_flags) -> bool:
    if length_m is None and beam_m is None:
        return True
    if imo_quality in ("checksum_fail", "malformed", "contested"):
        return True
    if imo_flags and "dimension_conflict" in imo_flags:
        return True
    return False


async def run(dsn: str, hours: int, dry_run: bool, source: str = TS.AIS) -> int:
    if source not in TS.SOURCES:
        logger.error("unknown source %r — expected one of %s", source, list(TS.SOURCES))
        return 2
    why = TS.refusal("zone_dwell", source)
    if why:
        logger.error("refusing to run zone_dwell on %s: %s", source, why)
        return 2
    conn = await asyncpg.connect(dsn)
    try:
        zlist = zone_gaz.detectable_zones()
        port = await build_port_mask(conn)
        busy = await build_busy_mask(conn)
        logger.info("zones=%d (detectable), masks: port=%d busy=%d, window=%dh",
                    len(zlist), len(port), len(busy), hours)

        def in_anchorage(la, lo):
            return port.contains(la, lo) or busy.contains(la, lo)

        # 1) compute ALL dwell sessions over the window (the annotation surface)
        all_sessions = []
        ident: dict[str, dict] = {}
        for z in zlist:
            outer_m = STANDOFF_OUTER_M if z["zone_type"] == "platform" else z["radius_m"]
            dlat = outer_m / 111320.0
            dlon = outer_m / (111320.0 * max(math.cos(math.radians(z["lat"])), 1e-6))
            rows = await conn.fetch(ZONE_FIX_SQL.format(source=source), hours, z["lon"] - dlon, z["lat"] - dlat,
                                    z["lon"] + dlon, z["lat"] + dlat)
            by_mmsi: dict[str, list] = defaultdict(list)
            for r in rows:
                by_mmsi[r["mmsi"]].append(r)
            for mmsi, frows in by_mmsi.items():
                head = frows[0]
                ident[mmsi] = {"imo": head["imo"], "ship_type": head["vi_type"] or head["vs_type"]}
                tu = _type_unverifiable(head["length_m"], head["beam_m"],
                                        head["imo_quality"], head["imo_flags"])
                fixes = [(r["ts"], r["latitude"], r["longitude"], r["implied_speed_kn"], r["speed"])
                         for r in frows]
                all_sessions.extend(zone_dwell_sessions(
                    z, mmsi, fixes, ship_type=ident[mmsi]["ship_type"],
                    type_unverifiable=tu, in_anchorage=in_anchorage))

        by_signal = Counter(s.signal for s in all_sessions)

        # 2) familiarity baseline + the data-sufficiency verdict (reported before use)
        baselines = zbase.build_baselines(all_sessions)
        vd = zbase.verdict(baselines)
        rd = zbase.readiness(baselines, window_hours=hours)
        logger.info("baseline: zones_with_history=%d zones_sound=%d max_span_days=%.1f sufficient=%s "
                    "(thresholds vessels>=%d sessions>=%d days>=%.0f)",
                    vd["zones_with_history"], vd["zones_sound"], vd["max_span_days"], vd["sufficient"],
                    zbase.MIN_BASELINE_VESSELS, zbase.MIN_BASELINE_SESSIONS, zbase.MIN_BASELINE_DAYS)
        logger.info("readiness: baseline_ready=%s binding_bar=%s gap_to_binding=%s "
                    "zones_meeting(days/vessels/sessions)=%d/%d/%d best(days/vessels/sessions)=%.1f/%d/%d",
                    rd["baseline_ready"], rd["binding_bar"], rd["gap_to_binding"],
                    rd["zones_meeting"]["days"], rd["zones_meeting"]["vessels"], rd["zones_meeting"]["sessions"],
                    rd["best_observed"]["span_days"], rd["best_observed"]["vessels"], rd["best_observed"]["sessions"])

        # 3) admission, scoped by evidence strength (pure policy: zone_dwell.admit_for_composite)
        admitted = []
        n_standoff_unfamiliar = 0
        for s in all_sessions:
            b = baselines.get(s.zone_id)
            sound = bool(vd["sufficient"] and b is not None and b.is_sound())
            fam = zbase.classify_familiarity(b, mmsi=s.mmsi, ship_type=s.ship_type,
                                             duration_s=s.duration_s).familiar
            if admit_for_composite(s, baseline_sound=sound, is_familiar=fam):
                admitted.append(s)
                if s.signal == STANDOFF_DWELL:
                    n_standoff_unfamiliar += 1

        events = [_event_row(s, ident.get(s.mmsi, {})) + (source,) for s in admitted]
        logger.info("=" * 64)
        logger.info("FUNNEL: sessions=%d (intrusion=%d standoff=%d field=%d) -> admitted=%d",
                    len(all_sessions), by_signal[SAFETY_ZONE_INTRUSION], by_signal[STANDOFF_DWELL],
                    by_signal["field_dwell"], len(events))
        logger.info("  admitted: safety_zone_intrusion=%d  standoff_unfamiliar=%d  (field_dwell=0 by design)",
                    sum(1 for s in admitted if s.signal == SAFETY_ZONE_INTRUSION), n_standoff_unfamiliar)
        if not vd["sufficient"]:
            logger.info("  standoff_dwell DEFERRED: baseline under-determined (no zone reaches the bars) "
                        "-> admit none; it stays annotation-only until the data supports a baseline")
        logger.info("=" * 64)
        if dry_run:
            logger.info("--dry-run: nothing written.")
            return 0
        async with conn.transaction():
            # ⛔ SCOPED TO THE WINDOW THIS RUN SCANNED. This detector has ALWAYS been
            # windowed (--hours, default 720) while its DELETE was not, so a run whose
            # window no longer reaches the data would delete every zone_dwell event and
            # rebuild none. That is the defect that destroyed 53 rendezvous events on
            # 2026-08-15 (see detect_rendezvous.py) — the same shape, latent here because
            # the 30-day default still happened to cover the data.
            #
            # THE RULE: a recompute may only delete what it could have recomputed.
            await conn.execute(
                "DELETE FROM maritime_events WHERE event_type='zone_dwell' "
                "AND source=$2 "
                "AND start_ts > now() - make_interval(hours => $1)", hours, source)
            await conn.executemany(UPSERT, events)
        total = await conn.fetchval("SELECT count(*) FROM maritime_events WHERE event_type='zone_dwell'")
        logger.info("stored %d zone_dwell candidates; maritime_events zone_dwell rows: %d",
                    len(events), total)
        return 0
    finally:
        await conn.close()


def _event_row(s, ivars: dict):
    detail = {
        "signal_class": s.signal, "zone_id": s.zone_id, "zone_name": s.zone_name,
        "zone_type": s.zone_type, "radius_basis": s.radius_basis, "coord_precision": s.coord_precision,
        "duration_is_lower_bound": s.duration_is_lower_bound, "min_distance_m": s.min_distance_m,
        "min_implied_speed_kn": s.min_implied_speed_kn, "mean_implied_speed_kn": s.mean_implied_speed_kn,
        "reported_sog_kn": s.reported_sog_kn, "ship_type": s.ship_type,
        "ship_type_caveat": ("ship type is self-reported AIS static; a false declaration is a "
                             "cloaking vector against the type-gate"),
        "verdict": s.verdict, "exclusion_reasons": list(s.exclusion_reasons),
        "cloaking_exposed": s.cloaking_exposed, "caveat": CANDIDATE_CAVEAT,
    }
    return (s.mmsi, s.start_ts, s.end_ts, ivars.get("imo"), s.lat, s.lon,
            s.duration_s, METHOD, json.dumps(detail))


def main() -> int:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s | %(levelname)-7s | %(message)s", datefmt="%H:%M:%S")
    load_dotenv(REPO_ROOT / ".env")
    p = argparse.ArgumentParser(description="ZONES-3 zone-dwell event writer (strong subset only)")
    p.add_argument("--dry-run", action="store_true", help="report the funnel + baseline verdict, write nothing")
    p.add_argument("--hours", type=int, default=DEFAULT_WINDOW_HOURS, help="time-bounded window (default 720)")
    p.add_argument("--source", default=TS.AIS, choices=list(TS.SOURCES),
                   help="which track to read (default aisstream); stored and recomputed "
                        "INDEPENDENTLY per source")
    a = p.parse_args()
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        print("ERROR: DATABASE_URL not set (env or .env)", file=sys.stderr)
        return 2
    return asyncio.run(run(dsn, a.hours, a.dry_run, a.source))


if __name__ == "__main__":
    sys.exit(main())
