#!/usr/bin/env python3
"""Enrichment layer E1 backfill: country of registration from the icao24 hex.

Purely LOCAL computation (no network): for every distinct icao24 in
state_vectors, derive its registration country via backend.icao_country (the
ICAO 24-bit allocation) and store it on the aircraft_info cache table with its
own provenance (source + derived_at).

Source-neutral: every feed (opensky, airplanes_live, adsbexchange) has an
icao24, so all are covered by the same derivation.

Idempotent / resumable: the derivation is deterministic and the upsert is keyed
on aircraft_info's PK (icao24), so re-running yields identical facts and no
duplicate rows. Crucially it does NOT disturb the on-click adsbdb/planespotters
cache: ON CONFLICT updates ONLY the registration_country columns; a brand-new
row is inserted with found=false and fetched_at=epoch, so the interactive
enricher still treats it as needing a fetch (epoch is far older than any TTL) —
the negative cache is never tripped and operator_country logic is untouched.
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

from backend.icao_country import (  # noqa: E402
    REGISTRATION_COUNTRY_SOURCE,
    icao24_to_registration_country,
)

logger = logging.getLogger("backfill_registration_country")

# New rows carry found=false + an ANCIENT fetched_at so the interactive enricher
# (backend/enrich.py) still fetches adsbdb/planespotters on first click instead
# of seeing a fresh negative-cache row. ON CONFLICT touches only the E1 columns.
UPSERT = """
INSERT INTO aircraft_info (icao24, found, fetched_at,
    registration_country, registration_country_source, registration_country_derived_at)
SELECT d.icao24, false, to_timestamp(0), d.country, $3, now()
FROM unnest($1::text[], $2::text[]) AS d(icao24, country)
ON CONFLICT (icao24) DO UPDATE SET
    registration_country            = EXCLUDED.registration_country,
    registration_country_source     = EXCLUDED.registration_country_source,
    registration_country_derived_at = EXCLUDED.registration_country_derived_at
"""


async def run(dsn: str, batch_size: int, dry_run: bool) -> int:
    conn = await asyncpg.connect(dsn)
    try:
        icaos = [r["icao24"] for r in await conn.fetch(
            "SELECT DISTINCT icao24 FROM state_vectors ORDER BY icao24")]
        derived: list[tuple[str, str]] = []
        none_count = 0
        for hx in icaos:
            country = icao24_to_registration_country(hx)
            if country is None:
                none_count += 1
            else:
                derived.append((hx, country))

        logger.info("=" * 60)
        logger.info("E1 PLAN (local ICAO 24-bit allocation derivation)")
        logger.info("  distinct icao24 in state_vectors: %d", len(icaos))
        logger.info("  derived a registration_country:   %d", len(derived))
        logger.info("  unallocated/unknown -> None:      %d", none_count)
        logger.info("  source label: %s", REGISTRATION_COUNTRY_SOURCE)
        logger.info("=" * 60)
        if dry_run:
            logger.info("--dry-run: nothing written.")
            return 0
        if not derived:
            logger.info("Nothing to write.")
            return 0

        written = 0
        for i in range(0, len(derived), batch_size):
            chunk = derived[i:i + batch_size]
            hexes = [h for h, _ in chunk]
            countries = [c for _, c in chunk]
            await conn.execute(UPSERT, hexes, countries, REGISTRATION_COUNTRY_SOURCE)
            written += len(chunk)
            logger.info("  upserted %d/%d", written, len(derived))

        logger.info("DONE: %d aircraft now carry a registration_country.", written)
        return 0
    finally:
        await conn.close()


def main() -> int:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s | %(levelname)-7s | %(message)s", datefmt="%H:%M:%S")
    load_dotenv(REPO_ROOT / ".env")
    p = argparse.ArgumentParser(description="E1 registration-country backfill (local, no network)")
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
