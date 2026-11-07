#!/usr/bin/env python3
"""Enrichment layer E2: bulk offline backfill of airframe reference facts from
OpenSky's aircraftDatabase.csv into the aircraft_info cache.

This is a LAYER, not a comparison: one offline snapshot fills the airframe facts
(registration / aircraft_type / icao_type / manufacturer / operator) for aircraft
the interactive adsbdb enricher has NOT enriched, with zero per-hex API calls.

PRECEDENCE (a fill, not an overwrite war):
  * adsbdb (interactive, live) OUTRANKS the CSV snapshot. E2 fills ONLY rows with
    found=false (E1-only or negative-cache rows); it NEVER touches a found=true
    adsbdb row. Enforced in SQL: `ON CONFLICT ... DO UPDATE ... WHERE found=false`.
  * E1's registration_country* columns are independent and never touched here.
  * New rows are inserted found=false + fetched_at=epoch, so the on-click enricher
    still fetches them (its negative cache is never tripped) — the interactive
    path can later supersede a CSV fill (found flips to true = adsbdb).

PROVENANCE per row: source='opensky_csv', csv_snapshot_date=<dump Last-Modified>,
source_updated_at=now().

CSV -> aircraft_info mapping (OpenSky aircraftDatabase.csv columns):
  icao24            -> icao24            (key, lowercased)
  registration      -> registration
  model             -> aircraft_type     (human model name, e.g. "A320 214")
  typecode          -> icao_type         (ICAO type designator, e.g. "A320")
  manufacturername  -> manufacturer      (HUMAN name, not manufacturericao)
  operator (or owner if operator empty) -> operator

Streaming + idempotent + resumable: the 500k-row CSV is streamed (never fully in
memory — only the ~rows matching DB icao24s are kept); the DB-side change-guard
means a re-run updates only rows that would actually change (0 spurious updates),
so an interrupted run resumes cleanly.

Fetch/refresh the snapshot (large file — gitignored under enrichment_data/, never
committed):
    curl -sL -o enrichment_data/aircraftDatabase.csv \
        https://opensky-network.org/datasets/metadata/aircraftDatabase.csv
Pass its HTTP Last-Modified date to --snapshot-date (provenance), e.g.
    python scripts/backfill_from_opensky_csv.py --snapshot-date 2024-11-04
Check the dump's date with: curl -sIL <url> | grep -i last-modified
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import logging
import os
import sys
from datetime import date
from pathlib import Path

import asyncpg
from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CSV = REPO_ROOT / "enrichment_data" / "aircraftDatabase.csv"
# Last-Modified of the fetched OpenSky dump (HTTP header). Override on refresh.
DEFAULT_SNAPSHOT = "2024-11-04"
SOURCE = "opensky_csv"

logger = logging.getLogger("backfill_opensky_csv")

UPSERT = """
INSERT INTO aircraft_info
    (icao24, found, fetched_at, registration, aircraft_type, icao_type,
     manufacturer, operator, source, csv_snapshot_date, source_updated_at)
SELECT d.icao24, false, to_timestamp(0),
       d.registration, d.aircraft_type, d.icao_type, d.manufacturer, d.operator,
       $7, $8::date, now()
FROM unnest($1::text[], $2::text[], $3::text[], $4::text[], $5::text[], $6::text[])
     AS d(icao24, registration, aircraft_type, icao_type, manufacturer, operator)
ON CONFLICT (icao24) DO UPDATE SET
    registration      = EXCLUDED.registration,
    aircraft_type     = EXCLUDED.aircraft_type,
    icao_type         = EXCLUDED.icao_type,
    manufacturer      = EXCLUDED.manufacturer,
    operator          = EXCLUDED.operator,
    source            = EXCLUDED.source,
    csv_snapshot_date = EXCLUDED.csv_snapshot_date,
    source_updated_at = EXCLUDED.source_updated_at
-- PRECEDENCE: never overwrite a live adsbdb row (found=true); and skip rows that
-- already match (idempotent — 0 spurious updates, source_updated_at excluded from
-- the comparison so a no-change re-run does not bump it).
WHERE aircraft_info.found = false
  AND (
    aircraft_info.registration      IS DISTINCT FROM EXCLUDED.registration OR
    aircraft_info.aircraft_type     IS DISTINCT FROM EXCLUDED.aircraft_type OR
    aircraft_info.icao_type         IS DISTINCT FROM EXCLUDED.icao_type OR
    aircraft_info.manufacturer      IS DISTINCT FROM EXCLUDED.manufacturer OR
    aircraft_info.operator          IS DISTINCT FROM EXCLUDED.operator OR
    aircraft_info.source            IS DISTINCT FROM EXCLUDED.source OR
    aircraft_info.csv_snapshot_date IS DISTINCT FROM EXCLUDED.csv_snapshot_date
  )
"""


def _clean(v: str | None) -> str | None:
    """CSV empty string -> NULL; otherwise trimmed value."""
    if v is None:
        return None
    v = v.strip()
    return v or None


def map_row(row: dict[str, str]) -> tuple | None:
    """OpenSky CSV row (dict) -> (icao24, registration, aircraft_type, icao_type,
    manufacturer, operator) or None if unusable (bad icao24, or no facts at all)."""
    icao24 = (row.get("icao24") or "").strip().lower()
    if len(icao24) != 6 or any(c not in "0123456789abcdef" for c in icao24):
        return None
    registration = _clean(row.get("registration"))
    aircraft_type = _clean(row.get("model"))
    icao_type = _clean(row.get("typecode"))
    manufacturer = _clean(row.get("manufacturername"))
    operator = _clean(row.get("operator")) or _clean(row.get("owner"))
    if not any((registration, aircraft_type, icao_type, manufacturer, operator)):
        return None  # empty airframe record — nothing to fill
    return (icao24, registration, aircraft_type, icao_type, manufacturer, operator)


async def run(dsn: str, csv_path: Path, snapshot: str, batch_size: int, dry_run: bool) -> int:
    if not csv_path.exists():
        logger.error("CSV not found: %s (fetch it first — see the script/runbook)", csv_path)
        return 2
    try:
        snapshot_date = date.fromisoformat(snapshot)
    except ValueError:
        logger.error("--snapshot-date must be YYYY-MM-DD (the dump's Last-Modified)")
        return 2

    conn = await asyncpg.connect(dsn)
    try:
        needed = {r["icao24"] for r in await conn.fetch("SELECT DISTINCT icao24 FROM state_vectors")}
        logger.info("=" * 60)
        logger.info("E2 PLAN — OpenSky aircraftDatabase.csv")
        logger.info("  CSV: %s", csv_path)
        logger.info("  snapshot (Last-Modified): %s", snapshot_date)
        logger.info("  distinct icao24 in DB to fill: %d", len(needed))
        logger.info("=" * 60)

        # Stream the CSV; keep ONLY rows whose icao24 is in the DB (dedup, last wins).
        matched: dict[str, tuple] = {}
        read = bad = 0
        with csv_path.open(encoding="utf-8", newline="") as fh:
            reader = csv.DictReader(fh)
            for row in reader:
                read += 1
                try:
                    mapped = map_row(row)
                except (ValueError, TypeError, AttributeError):
                    bad += 1
                    continue
                if mapped is None:
                    continue
                if mapped[0] in needed:
                    matched[mapped[0]] = mapped
                if read % 100_000 == 0:
                    logger.info("  streamed %d CSV rows, matched %d so far", read, len(matched))
        logger.info("CSV streamed: %d rows read, %d unusable, %d matched DB aircraft",
                    read, bad, len(matched))
        if dry_run:
            logger.info("--dry-run: nothing written.")
            return 0
        if not matched:
            logger.info("Nothing to fill.")
            return 0

        rows = list(matched.values())
        changed = 0
        for i in range(0, len(rows), batch_size):
            chunk = rows[i:i + batch_size]
            cols = list(zip(*chunk, strict=True))  # 6 parallel tuples
            status = await conn.execute(
                UPSERT, list(cols[0]), list(cols[1]), list(cols[2]),
                list(cols[3]), list(cols[4]), list(cols[5]), SOURCE, snapshot_date,
            )
            # asyncpg returns e.g. "INSERT 0 N" — N = rows actually inserted+updated
            changed += int(status.rsplit(" ", 1)[-1])
            logger.info("  processed %d/%d matched (cumulative rows changed: %d)",
                        min(i + batch_size, len(rows)), len(rows), changed)

        logger.info("DONE: %d matched aircraft, %d rows inserted/updated this run.",
                    len(rows), changed)
        return 0
    finally:
        await conn.close()


def main() -> int:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s | %(levelname)-7s | %(message)s", datefmt="%H:%M:%S")
    load_dotenv(REPO_ROOT / ".env")
    p = argparse.ArgumentParser(description="E2 bulk backfill from OpenSky aircraftDatabase.csv")
    p.add_argument("--csv", type=Path, default=DEFAULT_CSV)
    p.add_argument("--snapshot-date", default=DEFAULT_SNAPSHOT,
                   help="YYYY-MM-DD Last-Modified of the CSV dump (provenance)")
    p.add_argument("--batch-size", type=int, default=5000)
    p.add_argument("--dry-run", action="store_true")
    a = p.parse_args()
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        print("ERROR: DATABASE_URL not set (env or .env)", file=sys.stderr)
        return 2
    return asyncio.run(run(dsn, a.csv, a.snapshot_date, a.batch_size, a.dry_run))


if __name__ == "__main__":
    sys.exit(main())
