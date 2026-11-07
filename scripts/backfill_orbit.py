#!/usr/bin/env python3
"""Satellite RESEARCH layer Sat-Orbit: compute deterministic orbit classification
from the CURRENT TLEs in satellite_elements and upsert into satellite_orbit
(migration 019). Derivation is backend/orbit_derive.py (python-sgp4); this script
is just the DB glue.

LIVE, always-current: this is a REFRESH, not an append. Re-running with the same
TLEs changes nothing (idempotent); when the CelesTrak collector refreshes a TLE
(new epoch), re-running recomputes and UPDATES that object's row. Run it after the
satellite-elements refresh (scraplan-satellites.timer) to keep the orbital layer
current.

Invalid/decayed/unparseable TLEs are skipped with a logged reason — never a
garbage classification row.

Usage
    python scripts/backfill_orbit.py            # derive for the whole catalog
    python scripts/backfill_orbit.py --dry-run  # report, write nothing
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

from backend.orbit_derive import SOURCE, tle_to_orbit  # noqa: E402

logger = logging.getLogger("backfill_orbit")

UPSERT = """
INSERT INTO satellite_orbit
    (norad_id, orbit_class, apogee_km, perigee_km, eccentricity, inclination_deg,
     period_min, mean_motion, semimajor_axis_km, is_geostationary,
     is_sun_synchronous, source, tle_epoch, computed_at)
VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13, now())
ON CONFLICT (norad_id) DO UPDATE SET
    orbit_class=EXCLUDED.orbit_class, apogee_km=EXCLUDED.apogee_km,
    perigee_km=EXCLUDED.perigee_km, eccentricity=EXCLUDED.eccentricity,
    inclination_deg=EXCLUDED.inclination_deg, period_min=EXCLUDED.period_min,
    mean_motion=EXCLUDED.mean_motion, semimajor_axis_km=EXCLUDED.semimajor_axis_km,
    is_geostationary=EXCLUDED.is_geostationary,
    is_sun_synchronous=EXCLUDED.is_sun_synchronous,
    source=EXCLUDED.source, tle_epoch=EXCLUDED.tle_epoch, computed_at=now()
-- idempotent: same TLE (same epoch + derived values) => 0-row no-op; a refreshed
-- TLE (new epoch) updates the row and advances tle_epoch. computed_at bumps only
-- on a real change.
WHERE (
    satellite_orbit.orbit_class        IS DISTINCT FROM EXCLUDED.orbit_class OR
    satellite_orbit.apogee_km          IS DISTINCT FROM EXCLUDED.apogee_km OR
    satellite_orbit.perigee_km         IS DISTINCT FROM EXCLUDED.perigee_km OR
    satellite_orbit.eccentricity       IS DISTINCT FROM EXCLUDED.eccentricity OR
    satellite_orbit.inclination_deg    IS DISTINCT FROM EXCLUDED.inclination_deg OR
    satellite_orbit.period_min         IS DISTINCT FROM EXCLUDED.period_min OR
    satellite_orbit.mean_motion        IS DISTINCT FROM EXCLUDED.mean_motion OR
    satellite_orbit.semimajor_axis_km  IS DISTINCT FROM EXCLUDED.semimajor_axis_km OR
    satellite_orbit.is_geostationary   IS DISTINCT FROM EXCLUDED.is_geostationary OR
    satellite_orbit.is_sun_synchronous IS DISTINCT FROM EXCLUDED.is_sun_synchronous OR
    satellite_orbit.tle_epoch          IS DISTINCT FROM EXCLUDED.tle_epoch
)
"""


def orbit_row(norad_id: int, o: dict) -> tuple:
    return (
        norad_id, o["orbit_class"], o["apogee_km"], o["perigee_km"],
        o["eccentricity"], o["inclination_deg"], o["period_min"], o["mean_motion"],
        o["semimajor_axis_km"], o["is_geostationary"], o["is_sun_synchronous"],
        SOURCE, o["tle_epoch"],
    )


async def run(dsn: str, dry_run: bool) -> int:
    conn = await asyncpg.connect(dsn)
    try:
        # freshest TLE per object (a norad can sit in several CelesTrak groups)
        rows = await conn.fetch(
            "SELECT DISTINCT ON (norad_id) norad_id, tle_line1, tle_line2 "
            "FROM satellite_elements ORDER BY norad_id, epoch DESC NULLS LAST"
        )
        logger.info("=" * 60)
        logger.info("Sat-Orbit — deterministic orbit classification (SGP4)")
        logger.info("  current TLEs to derive: %d", len(rows))
        logger.info("=" * 60)

        derived = skipped = changed = 0
        dist: dict[str, int] = {}
        for r in rows:
            o = tle_to_orbit(r["tle_line1"], r["tle_line2"])
            if o is None:
                skipped += 1
                logger.debug("skipped norad %s (invalid/decayed TLE)", r["norad_id"])
                continue
            derived += 1
            dist[o["orbit_class"]] = dist.get(o["orbit_class"], 0) + 1
            if not dry_run:
                status = await conn.execute(UPSERT, *orbit_row(r["norad_id"], o))
                changed += int(status.rsplit(" ", 1)[-1])

        logger.info("=" * 60)
        logger.info("Sat-Orbit DONE: %d derived, %d skipped (invalid/decayed)", derived, skipped)
        logger.info("  orbit_class distribution: %s",
                    ", ".join(f"{k}={v}" for k, v in sorted(dist.items(), key=lambda x: -x[1])))
        logger.info("  rows inserted/updated this run: %d%s", changed,
                    " (dry-run: none)" if dry_run else "")
        logger.info("=" * 60)
        return 0
    finally:
        await conn.close()


def main() -> int:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s | %(levelname)-7s | %(message)s", datefmt="%H:%M:%S")
    load_dotenv(REPO_ROOT / ".env")
    p = argparse.ArgumentParser(description="Sat-Orbit: derive orbit classification from current TLEs")
    p.add_argument("--dry-run", action="store_true")
    a = p.parse_args()
    dsn = os.getenv("OPENSKY_DB_URL") or os.getenv("DATABASE_URL", "")
    if not dsn:
        print("ERROR: set OPENSKY_DB_URL or DATABASE_URL", file=sys.stderr)
        return 2
    return asyncio.run(run(dsn, a.dry_run))


if __name__ == "__main__":
    sys.exit(main())
