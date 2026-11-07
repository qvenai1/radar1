#!/usr/bin/env python3
"""Behavioural analysis A3 — IDENTITY-CHANGE candidate detection.

The pattern the M2b IMO spine was built to enable. Two change types, both cheap
SQL over vessel_states (GROUP BY imo / mmsi — no PostGIS, no O(n^2)):
  * RE-FLAG — same IMO under different MMSIs, with a different flag/name and a
    CLEAN temporal switch (old ends before new begins). The shadow-fleet signature.
  * RENAME / RETYPE — a confirmed (sustained, non-noise) name/ship_type change
    under one MMSI.

Noise filter (backend.identity_change): a change is CONFIRMED only when the new
value is sustained (>= a row floor), not a near-duplicate (spacing/typo), and not
garbage/placeholder. Prioritisation ranks (grey/sanctions flag, correlation with
A1 gaps / A2 rendezvous by the same hull, multiple changes → higher); it NEVER
accuses — a rename/re-flag is usually legal.

Stored as ONE canonical row per change in maritime_events
(event_type='identity_change', mmsi = new/subject MMSI, imo = permanent anchor,
start_ts = first_new_ts). Idempotent recompute-and-replace. No migration — the
generalized table + its imo index are reused as designed.

Usage:
    python scripts/detect_identity_change.py --dry-run
    python scripts/detect_identity_change.py
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from collections import Counter, defaultdict
from pathlib import Path

import asyncpg
from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from backend.mmsi_flag import mmsi_to_flag  # noqa: E402
from backend.identity_change import (  # noqa: E402
    CHANGE_REFLAG, CHANGE_RENAME, CHANGE_RETYPE, METHOD, MIN_REFLAG_ROWS,
    MIN_SUSTAINED_ROWS, clean_switch_gap_hours, identity_suspicion_rank,
    is_clean_switch, is_garbage_name, is_placeholder_mmsi, is_real_name_change,
    near_duplicate,
)

logger = logging.getLogger("detect_identity_change")

# Per (imo, mmsi): time range, rows, latest name — for re-flag detection.
IMO_MMSI_SQL = """
SELECT imo, mmsi, min(ts) first_ts, max(ts) last_ts, count(*) rows,
       (array_agg(name ORDER BY ts DESC) FILTER (WHERE name IS NOT NULL))[1] name
FROM vessel_states
WHERE imo IS NOT NULL AND imo <> '0'
GROUP BY imo, mmsi
"""

# Per (mmsi, value): time range, rows, latest imo — for rename/retype detection,
# only for MMSIs whose value varied.
def _value_sql(col: str) -> str:
    return f"""
    WITH multi AS (
      SELECT mmsi FROM vessel_states WHERE {col} IS NOT NULL
      GROUP BY mmsi HAVING count(DISTINCT {col}) > 1)
    SELECT vs.mmsi, vs.{col}::text val, min(vs.ts) first_ts, max(vs.ts) last_ts,
           count(*) rows,
           (array_agg(vs.imo ORDER BY vs.ts DESC) FILTER (WHERE vs.imo IS NOT NULL))[1] imo
    FROM vessel_states vs JOIN multi USING (mmsi) WHERE vs.{col} IS NOT NULL
    GROUP BY vs.mmsi, vs.{col}
    """

CORRELATE_SQL = """
SELECT event_type, classification, count(*) c
FROM maritime_events
WHERE event_type IN ('ais_gap','rendezvous')
  AND ($1::text IS NULL OR imo = $1)
  AND (mmsi = ANY($2::text[]) OR (detail->>'partner_mmsi') = ANY($2::text[]) OR imo = $1)
GROUP BY 1, 2
"""

UPSERT = """
INSERT INTO maritime_events (event_type, mmsi, start_ts, end_ts, imo,
    classification, method, detail, computed_at)
VALUES ('identity_change', $1, $2, NULL, $3, 'candidate', $4, $5::jsonb, now())
-- ⛔ CONFLICT TARGET UPDATED BY MIGRATION 043. The primary key became
-- (event_type, source, vessel_key, start_ts); the old (event_type, mmsi, start_ts) no
-- longer matches any constraint and every run failed with
-- "no unique or exclusion constraint matching the ON CONFLICT specification".
-- `source` is not listed in the INSERT because its column DEFAULT is 'aisstream', which
-- is exactly right for a detector that only reads the AIS track.
ON CONFLICT (event_type, source, vessel_key, start_ts) DO UPDATE SET
    imo=EXCLUDED.imo, method=EXCLUDED.method, detail=EXCLUDED.detail, computed_at=now()
"""


async def _correlate(conn, imo, mmsis):
    rows = await conn.fetch(CORRELATE_SQL, imo, mmsis)
    counts = {f"{r['event_type']}:{r['classification']}": r["c"] for r in rows}
    correlated = any(
        r["event_type"] == "rendezvous"
        or (r["event_type"] == "ais_gap" and r["classification"] == "candidate")
        for r in rows)
    return correlated, counts


def _iso(ts):
    return ts.isoformat() if ts is not None else None


async def run(dsn: str, dry_run: bool) -> int:
    conn = await asyncpg.connect(dsn)
    try:
        candidates = []            # (mmsi, start_ts, imo, detail)
        breakdown = Counter()

        # ---- RE-FLAG: same IMO, different MMSIs, clean temporal switch ----
        by_imo: dict[str, list] = defaultdict(list)
        for r in await conn.fetch(IMO_MMSI_SQL):
            by_imo[r["imo"]].append(r)
        reflag_raw = 0
        for imo, recs in by_imo.items():
            if len(recs) < 2:
                continue
            recs = sorted(recs, key=lambda r: r["first_ts"])
            recs = [r for r in recs if not is_placeholder_mmsi(r["mmsi"])]
            multiple = len(recs) >= 3
            for old, new in zip(recs, recs[1:], strict=False):  # pairwise — lengths differ by design
                reflag_raw += 1
                if old["rows"] < MIN_REFLAG_ROWS or new["rows"] < MIN_REFLAG_ROWS:
                    continue
                if not is_clean_switch(old["last_ts"], new["first_ts"]):
                    continue           # heavy overlap => likely IMO collision, not a re-flag
                of, nf = mmsi_to_flag(old["mmsi"])[0], mmsi_to_flag(new["mmsi"])[0]
                flag_changed = of != nf
                name_changed = is_real_name_change(old["name"], new["name"])
                if not (flag_changed or name_changed):
                    continue           # same flag AND same name => MMSI reassignment noise
                mmsis = [old["mmsi"], new["mmsi"]]
                correlated, corr = await _correlate(conn, imo, mmsis)
                rank = identity_suspicion_rank(change_type=CHANGE_REFLAG, old_flag=of,
                                               new_flag=nf, correlated=correlated, multiple=multiple)
                breakdown[rank] += 1
                candidates.append((new["mmsi"], new["first_ts"], imo, {
                    "change_type": CHANGE_REFLAG,
                    "field": "+".join(f for f, c in (("flag", flag_changed), ("name", name_changed)) if c),
                    "old_mmsi": old["mmsi"], "new_mmsi": new["mmsi"],
                    "old_flag": of, "new_flag": nf,
                    "old_name": old["name"], "new_name": new["name"],
                    "old_value": f"{old['name']} / {of}", "new_value": f"{new['name']} / {nf}",
                    "last_old_ts": _iso(old["last_ts"]), "first_new_ts": _iso(new["first_ts"]),
                    "gap_hours": round(clean_switch_gap_hours(old["last_ts"], new["first_ts"]), 1),
                    "sustained_new_rows": new["rows"], "suspicion_rank": rank,
                    "correlated": correlated, "correlated_events": corr,
                }))

        # ---- RENAME / RETYPE: confirmed value change under one MMSI ----
        for col, ctype in (("name", CHANGE_RENAME), ("ship_type", CHANGE_RETYPE)):
            per_mmsi: dict[str, list] = defaultdict(list)
            for r in await conn.fetch(_value_sql(col)):
                per_mmsi[r["mmsi"]].append(r)
            for mmsi, vals in per_mmsi.items():
                if is_placeholder_mmsi(mmsi):
                    continue
                # keep sustained, non-garbage values; collapse near-duplicates.
                kept: list = []
                for v in sorted(vals, key=lambda r: -r["rows"]):
                    if v["rows"] < MIN_SUSTAINED_ROWS:
                        continue
                    if ctype == CHANGE_RENAME and is_garbage_name(v["val"]):
                        continue
                    if ctype == CHANGE_RENAME and any(near_duplicate(v["val"], k["val"]) for k in kept):
                        continue
                    kept.append(v)
                if len(kept) < 2:
                    continue
                kept = sorted(kept, key=lambda r: r["first_ts"])
                multiple = len(kept) >= 3
                for old, new in zip(kept, kept[1:], strict=False):  # pairwise — lengths differ by design
                    imo = new["imo"] or old["imo"]
                    correlated, corr = await _correlate(conn, imo, [mmsi])
                    rank = identity_suspicion_rank(change_type=ctype, old_flag=None,
                                                   new_flag=None, correlated=correlated, multiple=multiple)
                    breakdown[rank] += 1
                    candidates.append((mmsi, new["first_ts"], imo, {
                        "change_type": ctype, "field": col,
                        "old_value": old["val"], "new_value": new["val"],
                        "last_old_ts": _iso(old["last_ts"]), "first_new_ts": _iso(new["first_ts"]),
                        "gap_hours": round(clean_switch_gap_hours(old["last_ts"], new["first_ts"]), 1),
                        "sustained_new_rows": new["rows"], "suspicion_rank": rank,
                        "correlated": correlated, "correlated_events": corr,
                    }))

        logger.info("=" * 60)
        logger.info("FUNNEL: re-flag transitions raw=%d ; identity-change candidates=%d",
                    reflag_raw, len(candidates))
        by_type = Counter(d["change_type"] for _, _, _, d in candidates)
        logger.info("  by change_type: %s", dict(by_type))
        logger.info("  by rank: high=%d medium=%d low=%d",
                    breakdown["high"], breakdown["medium"], breakdown["low"])
        logger.info("=" * 60)
        if dry_run:
            logger.info("--dry-run: nothing written.")
            return 0
        rows = [(m, t, imo, METHOD, json.dumps(d)) for m, t, imo, d in candidates]
        async with conn.transaction():
            # ⚠ SCOPED BY SOURCE, not by window — deliberately, and the asymmetry is the
            # point. This detector's scan is UNBOUNDED (it groups over all history), so a
            # full delete is exactly what it can rebuild. Adding a window to the scan later
            # MUST add the same window here, or it becomes the truncate that cost 53
            # rendezvous events on 2026-08-15.
            await conn.execute(
                "DELETE FROM maritime_events WHERE event_type='identity_change' "
                "AND source='aisstream'")
            await conn.executemany(UPSERT, rows)
        total = await conn.fetchval(
            "SELECT count(*) FROM maritime_events WHERE event_type='identity_change'")
        logger.info("stored %d identity_change candidates; rows now: %d", len(rows), total)
        return 0
    finally:
        await conn.close()


def main() -> int:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s | %(levelname)-7s | %(message)s", datefmt="%H:%M:%S")
    load_dotenv(REPO_ROOT / ".env")
    p = argparse.ArgumentParser(description="A3 identity-change detection (confound-filtered)")
    p.add_argument("--dry-run", action="store_true", help="report the funnel, write nothing")
    a = p.parse_args()
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        print("ERROR: DATABASE_URL not set (env or .env)", file=sys.stderr)
        return 2
    return asyncio.run(run(dsn, a.dry_run))


if __name__ == "__main__":
    sys.exit(main())
