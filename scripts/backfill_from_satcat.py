#!/usr/bin/env python3
"""Satellite enrichment layer S1: bulk offline backfill of SATCAT IDENTITY facts
from CelesTrak's SATCAT into the satellite_info cache — the orbital equivalent of
scripts/backfill_from_opensky_csv.py (aircraft E2). Same discipline, keyed on
norad_id instead of icao24.

This is a LAYER (a fill), not a per-object API: one offline snapshot covers the
whole catalog, so we fill identity facts (international designator / object name /
owner+country / launch date+site / object type / decay / ops status / RCS) for
every NORAD id already present in satellite_elements / satellite_history (and,
since S2c, satellite_reentry — so reentry events carry SATCAT identity via the
join), with ZERO per-object network calls.

SATCAT is AUTHORITATIVE for identity, so S1 populates directly. Provenance is
recorded per row (source='celestrak_satcat', satcat_snapshot_date=<dump date>)
so a future S2 (Space-Track) or a refresh can supersede facts, exactly as adsbdb
supersedes the CSV for aircraft.

PRECEDENCE / SAFETY (final, DB-enforced):
  * NEVER overwrite a value with a blank — the UPSERT uses COALESCE(EXCLUDED, x)
    so an empty SATCAT field leaves any existing value intact.
  * IDEMPOTENT + RESUMABLE — the DO UPDATE ... WHERE change-guard means a re-run
    with the same snapshot updates only rows that would actually change (0
    spurious updates); source_updated_at is bumped only on a real change. An
    interrupted run resumes cleanly (power-loss context).
  * ADDITIVE — writes satellite_info only; satellite_elements / satellite_history
    and their keys are never touched.

SATCAT CSV -> satellite_info mapping (verified against the LIVE header 2026-07-17:
  OBJECT_NAME,OBJECT_ID,NORAD_CAT_ID,OBJECT_TYPE,OPS_STATUS_CODE,OWNER,
  LAUNCH_DATE,LAUNCH_SITE,DECAY_DATE,PERIOD,INCLINATION,APOGEE,PERIGEE,RCS,
  DATA_STATUS_CODE,ORBIT_CENTER,ORBIT_TYPE):
    NORAD_CAT_ID    -> norad_id                 (key, int)
    OBJECT_ID       -> international_designator
    OBJECT_NAME     -> object_name              (SATCAT's; may differ from TLE)
    OBJECT_TYPE     -> object_type              (raw: PAY | R/B | DEB | UNK)
    OWNER           -> owner                    (raw CelesTrak source code)
                    -> owner_name               (decoded via satcat_sources.json;
                                                 NULL if code unknown — no guess)
    LAUNCH_DATE     -> launch_date              (DATE or NULL)
    LAUNCH_SITE     -> launch_site
    DECAY_DATE      -> decay_date               (DATE or NULL = on-orbit)
    OPS_STATUS_CODE -> ops_status
    RCS             -> rcs_m2                    (m^2, float or NULL)

Fetch/refresh the snapshot (gitignored under enrichment_data/, never committed):
    curl -sL -o enrichment_data/satcat.csv https://celestrak.org/pub/satcat.csv
Pass its HTTP Last-Modified date to --snapshot-date (provenance), e.g.
    python scripts/backfill_from_satcat.py --snapshot-date 2026-07-17
Check the dump's date with: curl -sIL https://celestrak.org/pub/satcat.csv | grep -i last-modified
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import logging
import os
import sys
from datetime import date
from pathlib import Path

import asyncpg
from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CSV = REPO_ROOT / "enrichment_data" / "satcat.csv"
SOURCES_JSON = REPO_ROOT / "backend" / "data" / "satcat_sources.json"
# Last-Modified of the fetched CelesTrak SATCAT dump (HTTP header). Override on refresh.
DEFAULT_SNAPSHOT = "2026-07-17"
SOURCE = "celestrak_satcat"

logger = logging.getLogger("backfill_satcat")


def _load_sources() -> dict[str, str]:
    """CelesTrak SATCAT owner/source code -> country/organization name.
    Authoritative reference (backend/data/satcat_sources.json, scraped once from
    CelesTrak's published sources table). Empty dict if the file is missing —
    owner_name then stays NULL everywhere (owner code is still preserved)."""
    try:
        return json.loads(SOURCES_JSON.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        logger.warning("owner-code map %s unreadable — owner_name will be NULL", SOURCES_JSON)
        return {}


SOURCES = _load_sources()


def _clean(v: str | None) -> str | None:
    """CSV empty string -> NULL; otherwise trimmed value."""
    if v is None:
        return None
    v = v.strip()
    return v or None


def _as_date(v: str | None) -> date | None:
    """SATCAT 'YYYY-MM-DD' -> date, or None (empty / malformed — never raises)."""
    v = _clean(v)
    if v is None:
        return None
    try:
        return date.fromisoformat(v)
    except ValueError:
        return None


def _as_float(v: str | None) -> float | None:
    v = _clean(v)
    if v is None:
        return None
    try:
        return float(v)
    except ValueError:
        return None


def map_row(row: dict[str, str], sources: dict[str, str] | None = None) -> tuple | None:
    """SATCAT CSV row (dict) -> satellite_info parameter tuple, or None if unusable.

    Tuple order matches the UPSERT unnest arrays:
      (norad_id, international_designator, object_name, object_type, owner,
       owner_name, launch_date, launch_site, decay_date, ops_status, rcs_m2)

    Skips a row only when it cannot be keyed (no valid positive NORAD id) or it
    carries no identity fact at all. Malformed dates/RCS degrade to NULL rather
    than dropping the row (partial SATCAT lines are still worth their identity)."""
    sources = SOURCES if sources is None else sources
    raw_id = (row.get("NORAD_CAT_ID") or "").strip()
    if not raw_id.isdigit():
        return None
    norad_id = int(raw_id)
    if norad_id <= 0:
        return None

    international_designator = _clean(row.get("OBJECT_ID"))
    object_name = _clean(row.get("OBJECT_NAME"))
    object_type = _clean(row.get("OBJECT_TYPE"))
    owner = _clean(row.get("OWNER"))
    owner_name = sources.get(owner) if owner else None
    ops_status = _clean(row.get("OPS_STATUS_CODE"))
    launch_site = _clean(row.get("LAUNCH_SITE"))
    launch_date = _as_date(row.get("LAUNCH_DATE"))
    decay_date = _as_date(row.get("DECAY_DATE"))
    rcs_m2 = _as_float(row.get("RCS"))

    if not any((international_designator, object_name, object_type, owner,
                ops_status, launch_site, launch_date, decay_date, rcs_m2)):
        return None  # keyable but empty — nothing to enrich

    return (norad_id, international_designator, object_name, object_type, owner,
            owner_name, launch_date, launch_site, decay_date, ops_status, rcs_m2)


# COALESCE(EXCLUDED, existing) on every fact => a blank SATCAT field never nulls a
# stored value. The WHERE change-guard makes a same-snapshot re-run a no-op (0
# spurious updates) while a NEW snapshot refreshes provenance; source_updated_at
# is bumped only when a row actually changes.
UPSERT = """
INSERT INTO satellite_info
    (norad_id, international_designator, object_name, object_type, owner, owner_name,
     launch_date, launch_site, decay_date, ops_status, rcs_m2,
     source, satcat_snapshot_date, source_updated_at, fetched_at)
SELECT d.norad_id, d.intl_des, d.object_name, d.object_type, d.owner, d.owner_name,
       d.launch_date, d.launch_site, d.decay_date, d.ops_status, d.rcs_m2,
       $12, $13::date, now(), now()
FROM unnest($1::int[], $2::text[], $3::text[], $4::text[], $5::text[], $6::text[],
            $7::date[], $8::text[], $9::date[], $10::text[], $11::float8[])
     AS d(norad_id, intl_des, object_name, object_type, owner, owner_name,
          launch_date, launch_site, decay_date, ops_status, rcs_m2)
ON CONFLICT (norad_id) DO UPDATE SET
    international_designator = COALESCE(EXCLUDED.international_designator, satellite_info.international_designator),
    object_name  = COALESCE(EXCLUDED.object_name, satellite_info.object_name),
    object_type  = COALESCE(EXCLUDED.object_type, satellite_info.object_type),
    owner        = COALESCE(EXCLUDED.owner,       satellite_info.owner),
    owner_name   = COALESCE(EXCLUDED.owner_name,  satellite_info.owner_name),
    launch_date  = COALESCE(EXCLUDED.launch_date, satellite_info.launch_date),
    launch_site  = COALESCE(EXCLUDED.launch_site, satellite_info.launch_site),
    decay_date   = COALESCE(EXCLUDED.decay_date,  satellite_info.decay_date),
    ops_status   = COALESCE(EXCLUDED.ops_status,  satellite_info.ops_status),
    rcs_m2       = COALESCE(EXCLUDED.rcs_m2,       satellite_info.rcs_m2),
    source               = EXCLUDED.source,
    satcat_snapshot_date = EXCLUDED.satcat_snapshot_date,
    source_updated_at    = now()
WHERE (
    satellite_info.international_designator IS DISTINCT FROM COALESCE(EXCLUDED.international_designator, satellite_info.international_designator) OR
    satellite_info.object_name IS DISTINCT FROM COALESCE(EXCLUDED.object_name, satellite_info.object_name) OR
    satellite_info.object_type IS DISTINCT FROM COALESCE(EXCLUDED.object_type, satellite_info.object_type) OR
    satellite_info.owner       IS DISTINCT FROM COALESCE(EXCLUDED.owner,       satellite_info.owner) OR
    satellite_info.owner_name  IS DISTINCT FROM COALESCE(EXCLUDED.owner_name,  satellite_info.owner_name) OR
    satellite_info.launch_date IS DISTINCT FROM COALESCE(EXCLUDED.launch_date, satellite_info.launch_date) OR
    satellite_info.launch_site IS DISTINCT FROM COALESCE(EXCLUDED.launch_site, satellite_info.launch_site) OR
    satellite_info.decay_date  IS DISTINCT FROM COALESCE(EXCLUDED.decay_date,  satellite_info.decay_date) OR
    satellite_info.ops_status  IS DISTINCT FROM COALESCE(EXCLUDED.ops_status,  satellite_info.ops_status) OR
    satellite_info.rcs_m2      IS DISTINCT FROM COALESCE(EXCLUDED.rcs_m2,       satellite_info.rcs_m2) OR
    satellite_info.satcat_snapshot_date IS DISTINCT FROM EXCLUDED.satcat_snapshot_date
)
"""


async def run(dsn: str, csv_path: Path, snapshot: str, batch_size: int, dry_run: bool) -> int:
    if not csv_path.exists():
        logger.error("SATCAT not found: %s (fetch it first — see the script docstring)", csv_path)
        return 2
    try:
        snapshot_date = date.fromisoformat(snapshot)
    except ValueError:
        logger.error("--snapshot-date must be YYYY-MM-DD (the dump's Last-Modified)")
        return 2

    conn = await asyncpg.connect(dsn)
    try:
        needed = {r["norad_id"] for r in await conn.fetch(
            "SELECT norad_id FROM satellite_elements "
            "UNION SELECT norad_id FROM satellite_history")}
        # S2c: also enrich objects that appear in reentry predictions, so a
        # reentry event carries its SATCAT identity via the satellite_info join.
        # Guarded so this backfill still runs on a DB predating migration 017.
        if await conn.fetchval("SELECT to_regclass('satellite_reentry')") is not None:
            needed |= {r["norad_id"] for r in await conn.fetch(
                "SELECT norad_id FROM satellite_reentry")}
        logger.info("=" * 60)
        logger.info("S1 PLAN — CelesTrak SATCAT identity enrichment")
        logger.info("  SATCAT: %s", csv_path)
        logger.info("  snapshot (Last-Modified): %s", snapshot_date)
        logger.info("  owner-code decode entries: %d", len(SOURCES))
        logger.info("  distinct norad_id in DB to fill: %d", len(needed))
        logger.info("=" * 60)

        # Stream the whole catalog; keep ONLY rows whose norad_id is tracked
        # (dedup, last wins). Never hold the full 70k-row file in memory.
        matched: dict[int, tuple] = {}
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
                if read % 20_000 == 0:
                    logger.info("  streamed %d SATCAT rows, matched %d so far", read, len(matched))
        unmatched = sorted(needed - set(matched))
        logger.info("SATCAT streamed: %d rows, %d unusable, %d matched tracked satellites",
                    read, bad, len(matched))
        if unmatched:
            logger.warning("  %d tracked norad_id had NO SATCAT match (left unfilled, no guess): %s%s",
                           len(unmatched), unmatched[:10], " ..." if len(unmatched) > 10 else "")
        else:
            logger.info("  every tracked norad_id has a SATCAT match")

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
            cols = list(zip(*chunk, strict=True))  # 11 parallel tuples in tuple order
            status = await conn.execute(
                UPSERT,
                list(cols[0]), list(cols[1]), list(cols[2]), list(cols[3]),
                list(cols[4]), list(cols[5]), list(cols[6]), list(cols[7]),
                list(cols[8]), list(cols[9]), list(cols[10]),
                SOURCE, snapshot_date,
            )
            changed += int(status.rsplit(" ", 1)[-1])
            logger.info("  processed %d/%d matched (cumulative rows changed: %d)",
                        min(i + batch_size, len(rows)), len(rows), changed)

        logger.info("DONE: %d matched satellites, %d rows inserted/updated this run.",
                    len(rows), changed)
        return 0
    finally:
        await conn.close()


def main() -> int:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s | %(levelname)-7s | %(message)s", datefmt="%H:%M:%S")
    load_dotenv(REPO_ROOT / ".env")
    p = argparse.ArgumentParser(description="S1 bulk backfill from CelesTrak SATCAT")
    p.add_argument("--csv", type=Path, default=DEFAULT_CSV)
    p.add_argument("--snapshot-date", default=DEFAULT_SNAPSHOT,
                   help="YYYY-MM-DD Last-Modified of the SATCAT dump (provenance)")
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
