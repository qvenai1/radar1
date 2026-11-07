#!/usr/bin/env python3
"""Maritime layer IMO-1 — per-identity IMO-integrity backfill + continuous runner.

ONE script (the backfill_position_quality.py pattern): the one-off backfill of every
vessel_identity row and the ongoing currency run a systemd timer fires. No detection
logic lives here — backend.imo_integrity is the only source of verdicts; this script
owns all I/O and the recompute-and-replace discipline.

WHY NO WINDOWING (the difference from G2): vessel_identity is a tiny PLAIN table
(~3,758 rows), not a 33M-row compressed hypertable. A full recompute is milliseconds,
so there is no --since / overlap / decompress machinery — every run recomputes every
identity and the WHERE-CHANGED guard makes reruns free. The timer runs the same full
recompute.

WHAT IT DOES:
  * fetches every identity (mmsi, imo, name, length_m, beam_m);
  * for IMOs carried by >1 MMSI, fetches each MMSI's activity window
    (min/max ts from vessel_states) so the module can tell a clean re-flag from a
    time-overlap collision;
  * builds the mmsi_multi_imo set (MMSIs that broadcast >1 distinct IMO in
    vessel_states — 0 in all data measured; the latent guard, wired not vaporware);
  * passes PLAIN data into imo_integrity.classify_identity(...), writes imo_quality /
    imo_flags, stamps imo_method from the module and imo_computed_at = now().

NULL-imo identities (~848) are NEVER written — they keep imo_quality / imo_method
NULL ("no IMO reported" != "reported and checked"), mirroring G2's NULL-position
rows. The post-backfill assertion proves imo_method IS NULL <=> imo IS NULL.

IDEMPOTENT BY CONSTRUCTION: same data => identical verdicts (the module is
deterministic). Writes go through a WHERE-CHANGED guard (IS DISTINCT FROM on every
verdict column EXCEPT imo_computed_at — the M2b/G2 churn-guard precedent), so a no-op
re-run changes 0 rows and does not even touch imo_computed_at.

Modes:
    --dry-run   compute + print the verdict/flag distribution, write nothing
    (neither)   recompute + write (full history)

Usage:
    python scripts/backfill_imo_integrity.py --dry-run
    python scripts/backfill_imo_integrity.py
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

from backend.imo_integrity import (  # noqa: E402
    Member,
    build_collision_event,
    classify_identity,
)

logger = logging.getLogger("backfill_imo_integrity")

# every identity + its static evidence (the whole table — it is tiny).
IDENTITIES_SQL = """
SELECT mmsi, imo, name, length_m, beam_m
FROM vessel_identity
ORDER BY mmsi
"""

# per-MMSI activity window, only for the MMSIs that share an IMO (the collision axis).
WINDOWS_SQL = """
SELECT mmsi, min(ts) AS first_ts, max(ts) AS last_ts
FROM vessel_states
WHERE mmsi = ANY($1::text[])
GROUP BY mmsi
"""

# the latent mmsi_multi_imo guard: MMSIs that carried >1 distinct IMO in the archive
# mirror (vessel_states.imo). Measured 0 — wired so the guard is real, not declared.
MULTI_IMO_SQL = """
SELECT mmsi FROM vessel_states
WHERE imo IS NOT NULL
GROUP BY mmsi HAVING count(DISTINCT imo) > 1
"""

CREATE_TEMP = """
CREATE TEMP TABLE _iq (
    mmsi TEXT, imo_quality TEXT, imo_flags TEXT[], imo_method TEXT
) ON COMMIT PRESERVE ROWS
"""

# WHERE-CHANGED guard: (re)stamp only identities whose verdict/flags/method actually
# differ — imo_computed_at is deliberately NOT in the comparison, so a no-op re-run
# touches nothing. Only imo-bearing identities enter _iq, so NULL-imo rows are never
# updated and keep imo_quality / imo_method NULL.
UPDATE_SQL = """
UPDATE vessel_identity v SET
    imo_quality     = q.imo_quality,
    imo_flags       = q.imo_flags,
    imo_method      = q.imo_method,
    imo_computed_at = now()
FROM _iq q
WHERE v.mmsi = q.mmsi
  AND (v.imo_quality IS DISTINCT FROM q.imo_quality
    OR v.imo_flags   IS DISTINCT FROM q.imo_flags
    OR v.imo_method  IS DISTINCT FROM q.imo_method)
"""

_COPY_COLS = ("mmsi", "imo_quality", "imo_flags", "imo_method")

# the latent collision event — recompute-and-replace on the maritime_events PK
# (event_type, mmsi, start_ts), the detector idempotency pattern. 0 rows today.
COLLISION_UPSERT = """
INSERT INTO maritime_events
    (event_type, mmsi, start_ts, end_ts, imo, classification, method, detail, computed_at)
VALUES ($1, $2, $3, $4, $5, $6, $7, $8::jsonb, now())
-- ⛔ CONFLICT TARGET UPDATED BY MIGRATION 043. The primary key became
-- (event_type, source, vessel_key, start_ts); the old (event_type, mmsi, start_ts) no
-- longer matches any constraint and every run failed with
-- "no unique or exclusion constraint matching the ON CONFLICT specification".
-- `source` is not listed in the INSERT because its column DEFAULT is 'aisstream', which
-- is exactly right for a detector that only reads the AIS track.
ON CONFLICT (event_type, source, vessel_key, start_ts) DO UPDATE SET
    end_ts = EXCLUDED.end_ts, imo = EXCLUDED.imo,
    classification = EXCLUDED.classification, method = EXCLUDED.method,
    detail = EXCLUDED.detail, computed_at = now()
"""


async def _compute(conn):
    """Return (rows, stats). rows = write-tuples for imo-bearing identities only."""
    identities = await conn.fetch(IDENTITIES_SQL)

    # group MMSIs by IMO to find shared-IMO sets
    by_imo: dict[str, list] = defaultdict(list)
    for r in identities:
        if r["imo"]:
            by_imo[r["imo"]].append(r)
    shared_mmsis = [r["mmsi"] for rows in by_imo.values() if len(rows) > 1 for r in rows]

    windows = {}
    if shared_mmsis:
        for w in await conn.fetch(WINDOWS_SQL, shared_mmsis):
            windows[w["mmsi"]] = (w["first_ts"], w["last_ts"])
    multi_imo = {r["mmsi"] for r in await conn.fetch(MULTI_IMO_SQL)}

    def member(r):
        f, l = windows.get(r["mmsi"], (None, None))
        return Member(mmsi=r["mmsi"], first_ts=f, last_ts=l, name=r["name"],
                      length_m=r["length_m"], beam_m=r["beam_m"])

    stats = {"verdict": Counter(), "flag": Counter(), "null_imo": 0}
    rows = []
    for r in identities:
        if not r["imo"]:
            stats["null_imo"] += 1
            continue
        group = by_imo[r["imo"]]
        shared = [member(g) for g in group] if len(group) > 1 else None
        res = classify_identity(r["imo"], shared=shared, mmsi_multi_imo=r["mmsi"] in multi_imo)
        stats["verdict"][res.imo_quality] += 1
        for fl in res.imo_flags:
            stats["flag"][fl] += 1
        rows.append((r["mmsi"], res.imo_quality, list(res.imo_flags) or None, res.method))

    # the latent collision path: one imo_integrity event per shared IMO that is a
    # genuine mmsi_collision (0 today — a guard, not a populated detector).
    collisions = []
    for imo, group in by_imo.items():
        if len(group) > 1:
            ev = build_collision_event(imo, [member(g) for g in group])
            if ev is not None:
                collisions.append(ev)
    return rows, collisions, stats


async def run(dsn, dry_run):
    conn = await asyncpg.connect(dsn)
    try:
        rows, collisions, stats = await _compute(conn)
        changed = 0
        if not dry_run:
            await conn.execute(CREATE_TEMP)
            async with conn.transaction():
                await conn.execute("TRUNCATE _iq")
                await conn.copy_records_to_table("_iq", records=rows, columns=list(_COPY_COLS))
                status = await conn.execute(UPDATE_SQL)
            changed = int(status.split()[-1])
            for ev in collisions:              # latent path — 0 rows today
                await conn.execute(COLLISION_UPSERT, ev["event_type"], ev["mmsi"],
                                   ev["start_ts"], ev["end_ts"], ev["imo"],
                                   ev["classification"], ev["method"], json.dumps(ev["detail"]))

        _summary(stats, len(rows), changed, dry_run, len(collisions))

        if not dry_run:
            null_method = await conn.fetchval(
                "SELECT count(*) FROM vessel_identity WHERE imo_method IS NULL")
            null_imo = await conn.fetchval(
                "SELECT count(*) FROM vessel_identity WHERE imo IS NULL")
            logger.info("post-backfill: imo_method IS NULL = %d (must equal NULL-imo = %d) -> %s",
                        null_method, null_imo, "OK" if null_method == null_imo else "MISMATCH")
        return 0
    finally:
        await conn.close()


def _summary(stats, n_imo, changed, dry_run, n_collisions):
    logger.info("=" * 60)
    logger.info("%s SUMMARY — %d imo-bearing identities (+ %d NULL-imo skipped)",
                "DRY-RUN" if dry_run else "WRITE", n_imo, stats["null_imo"])
    for v in ("valid", "checksum_fail", "malformed", "contested"):
        logger.info("  verdict %-13s %d", v, stats["verdict"].get(v, 0))
    for f, n in sorted(stats["flag"].items(), key=lambda x: -x[1]):
        logger.info("  flag    %-18s %d", f, n)
    logger.info("  imo_integrity collision events: %d (latent guard)", n_collisions)
    if not dry_run:
        logger.info("  rows CHANGED (write-guard): %d", changed)
    logger.info("=" * 60)


def main():
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s | %(levelname)-7s | %(message)s", datefmt="%H:%M:%S")
    load_dotenv(REPO_ROOT / ".env")
    p = argparse.ArgumentParser(description="IMO-1 per-identity IMO-integrity backfill + runner")
    p.add_argument("--dry-run", action="store_true", help="compute + summarise, write nothing")
    a = p.parse_args()
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        print("ERROR: DATABASE_URL not set (env or .env)", file=sys.stderr)
        return 2
    return asyncio.run(run(dsn, a.dry_run))


if __name__ == "__main__":
    sys.exit(main())
