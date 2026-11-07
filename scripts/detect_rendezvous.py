#!/usr/bin/env python3
"""Behavioural analysis A2 — RENDEZVOUS candidate detection (open-sea meetings).

Two vessels loitering together (potential ship-to-ship transfer). The value is
confound removal + honest prioritisation:
  * SQL pre-filter: GROUP BY (10-min bucket, 0.01deg cell) -> co-occupied cells
    with >=2 distinct slow (<3kn) vessels, offshore (>10km). 657k rows -> a few
    thousand candidate pair-buckets. NO O(n^2) all-pairs.
  * Confound masks (backend.coverage_mask): drop port/anchorage cells (>=5
    distinct stationary vessels) and busy lane/waiting cells (>=8 distinct
    vessels). ~99.9% of the naive signal is removed here.
  * Exact pass (backend.rendezvous): time-match each pair's fixes (|dt|<5min),
    true pairwise distance (great-circle), group into sustained encounters (both
    <=3kn, <=500m, >=1h). Rank by ship type + flag (NEVER auto-label).
  * Store ONE canonical row per encounter (smaller MMSI = mmsi; partner in detail)
    in maritime_events (event_type='rendezvous'). Idempotent recompute-replace on
    the PK (event_type, mmsi, start_ts).

G3 — implausible fixes (GNSS spoof/teleport/on-land) are EXCLUDED from the pair
pre-filter AND the exact time-matching stream (coverage_mask.PLAUSIBLE_FILTER:
position_quality IS DISTINCT FROM 'implausible'; NULL and 'suspect' pass), and the
port/busy masks are rebuilt with the same exclusion — so a decoy cluster can neither
manufacture a phantom anchorage nor fabricate an at-sea meeting from two spoofed
positions that happen to coincide. The >=10 km offshore floor and all thresholds are
unchanged. Flag, never delete — the rows keep everything; A2 just ignores implausible.

Honest limitation (surfaced in the API/UI): a candidate is an INVESTIGATION LEAD,
not proof of a transfer; most candidates are legitimate offshore work or ferries.

Usage:
    python scripts/detect_rendezvous.py --dry-run    # funnel report, write nothing
    python scripts/detect_rendezvous.py
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from bisect import bisect_left
from collections import Counter, defaultdict
from pathlib import Path

import asyncpg
from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from backend.coverage_mask import (  # noqa: E402
    OFFSHORE_MIN_KM, PLAUSIBLE_FILTER, build_busy_mask, build_port_mask,
)
from backend import track_source as TS  # noqa: E402
from backend.rendezvous import (  # noqa: E402
    METHOD, PROX_BUCKET_S, RDV_MAX_SPEED_KN, encounters, suspicion_rank,
    time_matched_distances,
)
from backend.draught_delta import (  # noqa: E402  — DRAUGHT-1 corroborating annotation
    DraughtObs, annotation as draught_annotation, classify_participant,
)

logger = logging.getLogger("detect_rendezvous")

#: How far back the pair scan looks.
#:
#: ⛔ ADDED BECAUSE THE SECOND SOURCE MADE ITS ABSENCE EXPENSIVE. The query had no window at
#: all: it aggregated the entire table and self-joined the result. That was survivable on
#: the AIS path and is not on MarineTraffic — a --dry-run over MT's full 27-day, 2.9M-row
#: history had not returned after 10 minutes and was killed.
#:
#: ⚠ THE CAUSE IS DENSITY, NOT VOLUME, and measuring the two sources side by side is what
#: showed it. `vessel_states` stores a MOVED-VESSEL DELTA (identical reports deduped), while
#: mt_vessel_states is a raw scrape at a 2.2-minute median cadence — so MT holds far more
#: rows per vessel over the same span, on a compressed hypertable that must be decompressed
#: to group. Bounded to 24 h the same scan yields 126,109 cell-buckets and just 2,014
#: candidate pairs; the cost was the unbounded history behind it.
#:
#: 7 days is chosen to exceed the detector's own encounter horizon (RDV_MIN_DURATION_S is
#: measured in hours) with room for a late-arriving fix, not as a performance knob — a run
#: that needs more history should say so with --since-days.
DEFAULT_SINCE_DAYS = 7

# Candidate PAIRS: aggregate to (mmsi, 10-min bucket, 0.01deg cell) keeping the
# bucket's min speed + max distance-to-coast, keep slow+offshore, self-join within
# the cell to form ordered pairs. Returns distinct (ma, mb, cell) — the coarse
# superset the exact pass refines.
# ⛔ MT-DETECT-1: reads `vessel_tracks` (migration 043/044), not vessel_states, and keys on
# the generic `vessel_key` so one query serves both identifier spaces.
#
# ⚠ THE SOURCE IS INTERPOLATED, NOT BOUND, AND THAT IS A MEASURED NECESSITY. `vessel_tracks`
# is a UNION over vessel_states (1.87M rows) and mt_vessel_states (2.9M), both compressed
# hypertables. With `source = $1` the planner cannot prove at plan time which branch is
# empty, so it evaluates BOTH: the 1-day MarineTraffic scan did not finish in 90 s. With the
# value inlined the AIS branch is pruned outright and the same scan returns 317 candidate
# pairs in 76 s. The value is checked against TS.SOURCES before it reaches this string —
# it is never caller-supplied text.
# The geometry claim — two vessels slow, close and offshore — survives the source change
# intact; what does not survive is the PARTNER'S IDENTITY, which for MT is a ship_id that
# cannot be joined to the AIS identity layer (see track_source.GATES["rendezvous"]).
PAIR_SQL = f"""
WITH p AS (
  SELECT vessel_key,
         floor(extract(epoch from ts) / {PROX_BUCKET_S})::bigint tb,
         floor(latitude / 0.01)::int clat, floor(longitude / 0.01)::int clon,
         min(speed) minspd, max(distance_km) dist
  FROM vessel_tracks
  WHERE source = '{{source}}'
    AND ts > now() - make_interval(days => $1)
    AND latitude IS NOT NULL AND longitude IS NOT NULL AND speed IS NOT NULL
    AND {PLAUSIBLE_FILTER}
  GROUP BY 1, 2, 3, 4),
slow AS (SELECT * FROM p WHERE minspd <= {RDV_MAX_SPEED_KN} AND dist > {OFFSHORE_MIN_KM})
SELECT DISTINCT a.vessel_key ma, b.vessel_key mb, a.clat, a.clon
FROM slow a JOIN slow b USING (tb, clat, clon)
WHERE a.vessel_key < b.vessel_key
"""

FIX_SQL = f"""
SELECT vessel_key AS mmsi, ts, latitude, longitude, speed, distance_km,
       country, near_country, draught
FROM vessel_tracks
WHERE source = '{{source}}' AND vessel_key = ANY($1::text[])
  AND ts > now() - make_interval(days => $2)
  AND latitude IS NOT NULL AND longitude IS NOT NULL
  AND {PLAUSIBLE_FILTER}
ORDER BY vessel_key, ts
"""

# ⚠ $12 is the SOURCE and $13 the key kind: the same value goes to `mmsi` or to `ship_id`
# depending on which identifier space it belongs to, and the CHECK constraint added in
# migration 043 (num_nonnulls(mmsi, ship_id) = 1) makes a mistake here fail loudly at insert
# rather than silently producing a keyless row.
UPSERT = """
INSERT INTO maritime_events (
    event_type, mmsi, ship_id, source, start_ts, end_ts, imo,
    start_lat, start_lon, end_lat, end_lon, duration_s,
    classification, method, detail, computed_at)
VALUES ('rendezvous',
        CASE WHEN $12 = 'aisstream' THEN $1 END,
        CASE WHEN $12 = 'aisstream' THEN NULL ELSE $1 END,
        $12, $2, $3, $4, $5, $6, $7, $8, $9, 'candidate', $10, $11::jsonb, now())
ON CONFLICT (event_type, source, vessel_key, start_ts) DO UPDATE SET
    end_ts=EXCLUDED.end_ts, imo=EXCLUDED.imo,
    start_lat=EXCLUDED.start_lat, start_lon=EXCLUDED.start_lon,
    end_lat=EXCLUDED.end_lat, end_lon=EXCLUDED.end_lon,
    duration_s=EXCLUDED.duration_s, method=EXCLUDED.method,
    detail=EXCLUDED.detail, computed_at=now()
"""


def _nearest_fix(fixes: list, ts) -> tuple:
    """The fix (record) nearest in time to ts."""
    tss = [f["ts"] for f in fixes]
    i = bisect_left(tss, ts)
    cands = [j for j in (i - 1, i) if 0 <= j < len(fixes)]
    return min(cands, key=lambda j: abs((fixes[j]["ts"] - ts).total_seconds()))


async def run(dsn: str, dry_run: bool, source: str = TS.AIS,
              since_days: int = DEFAULT_SINCE_DAYS) -> int:
    # ⛔ THE GATE IS CONSULTED BEFORE ANY WORK, and it can refuse for two different reasons
    # (a missing field, or a claim that does not survive the source). Refusing here means an
    # operator who asks for an unsupported combination is TOLD why, rather than getting an
    # empty run that looks like "nothing found".
    # ⛔ VALIDATED BEFORE IT IS EVER FORMATTED INTO SQL. `source` is interpolated rather
    # than bound (see the note on PAIR_SQL), so this membership test is the thing standing
    # between an argument and the query text. argparse already constrains it; this refuses
    # to depend on a caller that might not.
    if source not in TS.SOURCES:
        logger.error("unknown source %r — expected one of %s", source, list(TS.SOURCES))
        return 2
    why = TS.refusal("rendezvous", source)
    if why:
        logger.error("refusing to run rendezvous on %s: %s", source, why)
        return 2
    conn = await asyncpg.connect(dsn)
    try:
        port = await build_port_mask(conn)
        busy = await build_busy_mask(conn)
        logger.info("masks: port cells=%d, busy cells=%d, offshore floor=%.0fkm",
                    len(port), len(busy), OFFSHORE_MIN_KM)

        raw_pairs = await conn.fetch(PAIR_SQL.format(source=source), since_days)
        # Drop pairs whose co-occupancy cell is a port/anchorage or busy lane.
        pairs: set[tuple[str, str]] = set()
        for r in raw_pairs:
            lat, lon = (r["clat"] + 0.5) * 0.01, (r["clon"] + 0.5) * 0.01  # cell centre
            if port.contains(lat, lon) or busy.contains(lat, lon):
                continue
            pairs.add((r["ma"], r["mb"]))
        logger.info("candidate pair-cells: %d raw -> %d pairs after port/busy exclusion",
                    len(raw_pairs), len(pairs))

        # Fetch fixes once for all involved vessels; group by mmsi.
        involved = sorted({m for pr in pairs for m in pr})
        fix_rows = await conn.fetch(FIX_SQL.format(source=source), involved, since_days)
        fixes: dict[str, list] = defaultdict(list)
        for r in fix_rows:
            fixes[r["mmsi"]].append(r)
        # Identity for both vessels.
        # ⚠ IDENTITY IS AN AIS-ONLY JOIN. `vessel_identity` is keyed by MMSI, and an MT
        # track has none (measured 100 % NULL) — so for MT this is legitimately empty and
        # the events carry no imo/flag/ship_type. Querying it anyway with ship_ids would
        # match nothing at best and collide with a same-numbered MMSI at worst.
        ident = {}
        if source == TS.AIS:
            idrows = await conn.fetch(
                "SELECT mmsi, imo, name, flag_country, ship_type FROM vessel_identity "
                "WHERE mmsi = ANY($1::text[])", involved)
            ident = {r["mmsi"]: r for r in idrows}

        events, ranks = [], Counter()
        dr_states, dr_reasons = Counter(), Counter()   # DRAUGHT-1 per-participant tally
        n_transfer = 0
        n_encounters = 0
        for ma, mb in pairs:                        # ma < mb already (canonical)
            fa, fb = fixes.get(ma, []), fixes.get(mb, [])
            series = time_matched_distances(
                [(f["ts"], f["latitude"], f["longitude"], f["speed"]) for f in fa],
                [(f["ts"], f["latitude"], f["longitude"], f["speed"]) for f in fb])
            for enc in encounters(series):
                n_encounters += 1
                # Location = primary (smaller-mmsi) vessel's fix nearest the start.
                sfix = fa[_nearest_fix(fa, enc["start_ts"])]
                efix = fa[_nearest_fix(fa, enc["end_ts"])]
                lat, lon = sfix["latitude"], sfix["longitude"]
                # Final confound guard at the true encounter location.
                if port.contains(lat, lon) or busy.contains(lat, lon):
                    continue
                if (sfix["distance_km"] or 0) <= OFFSHORE_MIN_KM:
                    continue
                ia, ib = ident.get(ma) or {}, ident.get(mb) or {}
                rank = suspicion_rank(ia.get("ship_type"), ib.get("ship_type"),
                                      ia.get("flag_country"), ib.get("flag_country"))
                ranks[rank] += 1
                # DRAUGHT-1: corroborating draught-delta across the encounter, per
                # participant, from their whole-history draught-bearing fixes (fa/fb).
                # Pure module owns the 3-state verdict; A2's own classification is
                # untouched — this is additive corroboration, never a filter/re-rank.
                pd_a = classify_participant(
                    [DraughtObs(f["ts"], f["draught"]) for f in fa if f["draught"] and f["draught"] > 0],
                    enc["start_ts"], enc["end_ts"])
                pd_b = classify_participant(
                    [DraughtObs(f["ts"], f["draught"]) for f in fb if f["draught"] and f["draught"] > 0],
                    enc["start_ts"], enc["end_ts"])
                detail = {
                    "partner_mmsi": mb, "partner_imo": ib.get("imo"),
                    "partner_name": ib.get("name"), "partner_flag": ib.get("flag_country"),
                    "partner_ship_type": ib.get("ship_type"),
                    "primary_name": ia.get("name"), "primary_flag": ia.get("flag_country"),
                    "primary_ship_type": ia.get("ship_type"),
                    "min_distance_m": enc["min_distance_m"],
                    "max_duration_min": round(enc["duration_s"] / 60.0, 1),
                    "both_max_speed": enc["both_max_speed"],
                    "offshore_km": round(sfix["distance_km"], 1),
                    "start_country": sfix["country"], "start_near_country": sfix["near_country"],
                    "suspicion_rank": rank,
                    "draught": draught_annotation(pd_a, pd_b),
                }
                for pd in (pd_a, pd_b):
                    dr_states[pd.state] += 1
                    if pd.reason:
                        dr_reasons[pd.reason] += 1
                if detail["draught"]["transfer_shaped"]:
                    n_transfer += 1
                events.append((ma, enc["start_ts"], enc["end_ts"], ia.get("imo"),
                               lat, lon, efix["latitude"], efix["longitude"],
                               enc["duration_s"], METHOD, json.dumps(detail), source))

        logger.info("=" * 60)
        logger.info("FUNNEL: pairs=%d -> encounters(>=1h)=%d -> stored candidates=%d",
                    len(pairs), n_encounters, len(events))
        logger.info("  by suspicion rank: high=%d medium=%d low=%d",
                    ranks["high"], ranks["medium"], ranks["low"])
        logger.info("  DRAUGHT-1 (per participant, of %d encounters × 2): changed=%d unchanged=%d insufficient=%d",
                    len(events), dr_states["changed"], dr_states["unchanged"], dr_states["insufficient_data"])
        logger.info("    insufficient reasons: %s", dict(dr_reasons) or "{}")
        logger.info("    transfer_shaped encounters (both sides changed, opposite-sign): %d", n_transfer)
        logger.info("=" * 60)
        if dry_run:
            logger.info("--dry-run: nothing written.")
            return 0
        # Recompute-replace: clear prior rendezvous rows, then insert (idempotent —
        # same history yields the same canonical rows).
        async with conn.transaction():
            # ⛔ SCOPED TO THIS SOURCE **AND TO THE WINDOW THIS RUN ACTUALLY SCANNED**.
            #
            # The source scope is obvious once a second source exists: without it a
            # MarineTraffic run deletes every AIS-derived rendezvous in the table.
            #
            # THE WINDOW SCOPE IS THE ONE THAT BIT, AND IT DESTROYED LIVE DATA. Adding
            # --since-days turned this into a recompute-replace whose DELETE was WIDER than
            # its scan: the AIS feed has been frozen since 2026-08-05, so once it aged past
            # the 7-day default the scan matched no fixes, produced no events — and the
            # unscoped DELETE removed all 53 existing AIS rendezvous rows. Observed in the
            # timer log at 16:40: "pairs=0 -> stored candidates=0 ... rendezvous rows: 0".
            #
            # ⛔ THE RULE: A RECOMPUTE MAY ONLY DELETE WHAT IT COULD HAVE RECOMPUTED. Any
            # narrowing of the scan must narrow the delete with it, or the run silently
            # becomes a truncate whenever the data moves out of range — the loudest possible
            # example of failing OPEN.
            await conn.execute(
                "DELETE FROM maritime_events WHERE event_type='rendezvous' AND source=$1 "
                "AND start_ts > now() - make_interval(days => $2)",
                source, since_days)
            await conn.executemany(UPSERT, events)
        total = await conn.fetchval(
            "SELECT count(*) FROM maritime_events WHERE event_type='rendezvous' "
            "AND source=$1", source)
        logger.info("stored %d rendezvous candidates; maritime_events rendezvous rows: %d",
                    len(events), total)
        return 0
    finally:
        await conn.close()


def main() -> int:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s | %(levelname)-7s | %(message)s", datefmt="%H:%M:%S")
    load_dotenv(REPO_ROOT / ".env")
    p = argparse.ArgumentParser(description="A2 rendezvous candidate detection (confound-removed)")
    p.add_argument("--dry-run", action="store_true", help="report the funnel, write nothing")
    p.add_argument("--since-days", type=int, default=DEFAULT_SINCE_DAYS,
                   help=f"how far back to scan (default {DEFAULT_SINCE_DAYS}). The scan was "
                        f"unbounded before MT-DETECT-1 — see DEFAULT_SINCE_DAYS.")
    p.add_argument("--source", default=TS.AIS, choices=list(TS.SOURCES),
                   help="which track to read (default aisstream). Each source is stored "
                        "and recomputed INDEPENDENTLY — they are separate observations, "
                        "not one set to merge.")
    a = p.parse_args()
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        print("ERROR: DATABASE_URL not set (env or .env)", file=sys.stderr)
        return 2
    return asyncio.run(run(dsn, a.dry_run, a.source, a.since_days))


if __name__ == "__main__":
    sys.exit(main())
