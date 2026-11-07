#!/usr/bin/env python3
"""Maritime enrichment M2a backfill: flag state from the MMSI MID.

The sea-going analog of scripts/backfill_registration_country.py (aircraft E1).
Purely LOCAL computation (no network): for every distinct mmsi in vessel_states,
derive its flag state + station type via backend.mmsi_flag (the committed ITU
MID table) and cache it in vessel_identity with its provenance ('mmsi-mid').

Idempotent / resumable: the derivation is deterministic and the MID table is
static, so the upsert is ON CONFLICT (mmsi) DO NOTHING — an already-derived MMSI
is left exactly as-is (stable derived_at, 0 spurious changes) and a re-run only
fills MMSIs added since. It NEVER touches vessel_states or its natural key. (To
re-derive after the MID asset itself changes: TRUNCATE vessel_identity, re-run.)

Usage:
    python scripts/backfill_vessel_flag.py --dry-run   # report plan, write nothing
    python scripts/backfill_vessel_flag.py [--batch-size 5000]
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from collections import Counter
from pathlib import Path

import asyncpg
from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from backend.mmsi_flag import MID_SOURCE, mmsi_to_flag  # noqa: E402

logger = logging.getLogger("backfill_vessel_flag")

UPSERT = """
INSERT INTO vessel_identity (mmsi, flag_country, station_type, flag_source, derived_at)
SELECT d.mmsi, d.flag_country, d.station_type, $4, now()
FROM unnest($1::text[], $2::text[], $3::text[]) AS d(mmsi, flag_country, station_type)
ON CONFLICT (mmsi) DO NOTHING
"""


async def run(dsn: str, batch_size: int, dry_run: bool) -> int:
    conn = await asyncpg.connect(dsn)
    try:
        mmsis = [r["mmsi"] for r in await conn.fetch(
            "SELECT DISTINCT mmsi FROM vessel_states ORDER BY mmsi")]

        # Derive locally. Parallel arrays for the set-based upsert.
        d_mmsi, d_flag, d_station = [], [], []
        station_counts: Counter = Counter()
        flagged = none_country = 0
        for m in mmsis:
            country, station = mmsi_to_flag(m)
            d_mmsi.append(m); d_flag.append(country); d_station.append(station)
            station_counts[station] += 1
            if country is not None:
                flagged += 1
            else:
                none_country += 1

        logger.info("=" * 60)
        logger.info("M2a PLAN (local MMSI-MID flag derivation)")
        logger.info("  distinct mmsi in vessel_states: %d", len(mmsis))
        logger.info("  derived a flag_country:         %d", flagged)
        logger.info("  no flag (non-ship/unalloc MID): %d", none_country)
        logger.info("  station types: %s",
                    ", ".join(f"{k}={v}" for k, v in sorted(station_counts.items())))
        logger.info("  source label: %s", MID_SOURCE)
        logger.info("=" * 60)
        if dry_run:
            logger.info("--dry-run: nothing written.")
            return 0
        if not mmsis:
            logger.info("Nothing to write.")
            return 0

        before = await conn.fetchval("SELECT count(*) FROM vessel_identity")
        written = 0
        for i in range(0, len(d_mmsi), batch_size):
            sl = slice(i, i + batch_size)
            await conn.execute(UPSERT, d_mmsi[sl], d_flag[sl], d_station[sl], MID_SOURCE)
            written += len(d_mmsi[sl])
            logger.info("  upserted %d/%d", written, len(d_mmsi))
        after = await conn.fetchval("SELECT count(*) FROM vessel_identity")

        logger.info("=" * 60)
        logger.info("DONE: vessel_identity rows before=%d after=%d newly-inserted=%d "
                    "(existing left untouched)", before, after, after - before)
        logger.info("=" * 60)
        return 0
    finally:
        await conn.close()


def main() -> int:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s | %(levelname)-7s | %(message)s", datefmt="%H:%M:%S")
    load_dotenv(REPO_ROOT / ".env")
    p = argparse.ArgumentParser(description="M2a flag-state backfill (local, no network)")
    p.add_argument("--batch-size", type=int, default=5000)
    p.add_argument("--dry-run", action="store_true", help="report the plan, write nothing")
    a = p.parse_args()
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        print("ERROR: DATABASE_URL not set (env or .env)", file=sys.stderr)
        return 2
    return asyncio.run(run(dsn, a.batch_size, a.dry_run))


if __name__ == "__main__":
    sys.exit(main())
