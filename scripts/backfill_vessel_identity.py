#!/usr/bin/env python3
"""Maritime enrichment M2b backfill: promote AIS-static identity into the spine.

Consolidates the identity already scattered across the vessel_states time series
into the vessel_identity spine (M2a's table). For every distinct mmsi it takes
the LATEST NON-NULL value of each slow-changing identity field (name, ship_type,
ship_type_code, callsign, imo) and upserts it — latest-wins on change.

DIMENSIONS (length_m/beam_m) are NOT backfilled here: vessel_states never stored
them (they were dropped at parse time before M2b), so they are historically
UNRECOVERABLE from the DB. They accumulate going forward from the ingest path
(the parser now extracts them) once the collector restarts. This is reported,
not silently skipped.

Self-sufficient + idempotent: the upsert derives station_type/flag on INSERT via
the M2a function (so it produces a complete, valid row regardless of whether the
M2a backfill ran), and on conflict touches ONLY the identity block (flag/station
from M2a are preserved). A WHERE-changed guard makes a re-run a no-op — 0
spurious writes, stable identity_updated_at. NEVER touches vessel_states.

Usage:
    python scripts/backfill_vessel_identity.py --dry-run   # report plan, write nothing
    python scripts/backfill_vessel_identity.py [--batch-size 2000]
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from pathlib import Path

import asyncpg
from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from backend.mmsi_flag import mmsi_to_flag  # noqa: E402

logger = logging.getLogger("backfill_vessel_identity")

# Latest non-null value of each identity field, per vessel — one pass over the
# vessel_states hypertable (a read; no DML, no chunk decompression).
LATEST_IDENTITY = """
SELECT mmsi,
  (array_agg(name           ORDER BY ts DESC) FILTER (WHERE name IS NOT NULL))[1]           AS name,
  (array_agg(ship_type      ORDER BY ts DESC) FILTER (WHERE ship_type IS NOT NULL))[1]      AS ship_type,
  (array_agg(ship_type_code ORDER BY ts DESC) FILTER (WHERE ship_type_code IS NOT NULL))[1] AS ship_type_code,
  (array_agg(callsign       ORDER BY ts DESC) FILTER (WHERE callsign IS NOT NULL))[1]       AS callsign,
  (array_agg(imo            ORDER BY ts DESC) FILTER (WHERE imo IS NOT NULL))[1]            AS imo
FROM vessel_states
GROUP BY mmsi
ORDER BY mmsi
"""

# Self-sufficient upsert: station_type/flag on INSERT (from M2a's function),
# identity block on conflict (COALESCE keeps known values; WHERE-changed makes a
# re-run a no-op). length_m/beam_m are intentionally absent (see module docs).
UPSERT = """
INSERT INTO vessel_identity AS vi
    (mmsi, station_type, flag_country, flag_source, derived_at,
     name, ship_type, ship_type_code, callsign, imo,
     identity_source, identity_updated_at)
SELECT d.mmsi, d.station_type, d.flag_country, 'mmsi-mid', now(),
       d.name, d.ship_type, d.ship_type_code, d.callsign, d.imo,
       'ais-static', now()
FROM unnest($1::text[], $2::text[], $3::text[],
            $4::text[], $5::text[], $6::int[], $7::text[], $8::text[])
     AS d(mmsi, station_type, flag_country, name, ship_type, ship_type_code, callsign, imo)
ON CONFLICT (mmsi) DO UPDATE SET
    name                = COALESCE(EXCLUDED.name, vi.name),
    ship_type           = COALESCE(EXCLUDED.ship_type, vi.ship_type),
    ship_type_code      = COALESCE(EXCLUDED.ship_type_code, vi.ship_type_code),
    callsign            = COALESCE(EXCLUDED.callsign, vi.callsign),
    imo                 = COALESCE(EXCLUDED.imo, vi.imo),
    identity_source     = 'ais-static',
    identity_updated_at = now()
WHERE vi.name           IS DISTINCT FROM COALESCE(EXCLUDED.name, vi.name)
   OR vi.ship_type      IS DISTINCT FROM COALESCE(EXCLUDED.ship_type, vi.ship_type)
   OR vi.ship_type_code IS DISTINCT FROM COALESCE(EXCLUDED.ship_type_code, vi.ship_type_code)
   OR vi.callsign       IS DISTINCT FROM COALESCE(EXCLUDED.callsign, vi.callsign)
   OR vi.imo            IS DISTINCT FROM COALESCE(EXCLUDED.imo, vi.imo)
"""

FIELDS = ("name", "ship_type", "ship_type_code", "callsign", "imo")


async def run(dsn: str, batch_size: int, dry_run: bool) -> int:
    conn = await asyncpg.connect(dsn)
    try:
        rows = await conn.fetch(LATEST_IDENTITY)
        cov = {f: sum(1 for r in rows if r[f] is not None) for f in FIELDS}
        n = len(rows)
        logger.info("=" * 60)
        logger.info("M2b PLAN (promote AIS-static identity from vessel_states)")
        logger.info("  distinct mmsi in vessel_states: %d", n)
        for f in FIELDS:
            logger.info("  with %-14s %5d  (%4.1f%%)", f + ":", cov[f], 100.0 * cov[f] / max(n, 1))
        logger.info("  length_m / beam_m: 0  (not in vessel_states — accumulate at ingest)")
        logger.info("=" * 60)
        if dry_run:
            logger.info("--dry-run: nothing written.")
            return 0
        if n == 0:
            logger.info("Nothing to write.")
            return 0

        # Derive station_type/flag per mmsi (for the INSERT case) and build arrays.
        cols: dict[str, list] = {k: [] for k in
                                 ("mmsi", "station_type", "flag_country", *FIELDS)}
        for r in rows:
            flag, station = mmsi_to_flag(r["mmsi"])
            cols["mmsi"].append(r["mmsi"])
            cols["station_type"].append(station)
            cols["flag_country"].append(flag)
            for f in FIELDS:
                cols[f].append(r[f])

        changed_before = await conn.fetchval(
            "SELECT count(*) FROM vessel_identity WHERE identity_source IS NOT NULL")
        written = 0
        for i in range(0, n, batch_size):
            sl = slice(i, i + batch_size)
            await conn.execute(
                UPSERT,
                cols["mmsi"][sl], cols["station_type"][sl], cols["flag_country"][sl],
                cols["name"][sl], cols["ship_type"][sl], cols["ship_type_code"][sl],
                cols["callsign"][sl], cols["imo"][sl],
            )
            written += len(cols["mmsi"][sl])
            logger.info("  upserted %d/%d", written, n)
        changed_after = await conn.fetchval(
            "SELECT count(*) FROM vessel_identity WHERE identity_source IS NOT NULL")

        logger.info("=" * 60)
        logger.info("DONE: rows with an identity block before=%d after=%d",
                    changed_before, changed_after)
        logger.info("=" * 60)
        return 0
    finally:
        await conn.close()


def main() -> int:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s | %(levelname)-7s | %(message)s", datefmt="%H:%M:%S")
    load_dotenv(REPO_ROOT / ".env")
    p = argparse.ArgumentParser(description="M2b identity-spine backfill (local, from vessel_states)")
    p.add_argument("--batch-size", type=int, default=2000)
    p.add_argument("--dry-run", action="store_true", help="report the plan, write nothing")
    a = p.parse_args()
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        print("ERROR: DATABASE_URL not set (env or .env)", file=sys.stderr)
        return 2
    return asyncio.run(run(dsn, a.batch_size, a.dry_run))


if __name__ == "__main__":
    sys.exit(main())
