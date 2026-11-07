#!/usr/bin/env python3
"""Satellite RESEARCH capstone Sat-Passes: compute AOI ground-track passes for the
current catalog over a forward window and REPLACE the satellite_passes cache
(migration 020). Derivation is backend/passes_derive.py; geo classification is the
shared backend/geo_area.py; propagation is python-sgp4's vectorized SatrecArray.

PERFORMANCE: all satellites are propagated in ONE batched SatrecArray call over the
whole time grid; sub-points are computed with vectorized numpy; a numpy bounding-box
prefilter drops the (many) satellites never near the AOI so the shapely polygon
classify runs only on candidates. Runtime is reported.

FORWARD-WINDOW CACHE: each run DELETEs the table and INSERTs the fresh window in one
transaction — idempotent (same TLEs + window → same rows). Wire it after the TLE
refresh + orbit recompute (the scraplan-satellites.service ExecStartPost chain) so
passes stay current.

Usage
    python scripts/backfill_passes.py                      # next 24 h, 60 s step
    python scripts/backfill_passes.py --window-hours 48 --step-seconds 30
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import asyncpg
import numpy as np
from dotenv import load_dotenv
from sgp4.api import Satrec, SatrecArray

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from backend.geo_area import get_area  # noqa: E402
from backend.passes_derive import (  # noqa: E402
    SOURCE, STEP_S, WINDOW_H, classify_passes, gmst_rad, is_geosync, jd_to_dt,
    sample_times, teme_to_geodetic,
)

logger = logging.getLogger("backfill_passes")

INSERT = """
INSERT INTO satellite_passes
    (norad_id, start_time, end_time, duration_s, countries, near_countries,
     closest_km, is_persistent, n_points, source, tle_epoch, window_start, window_end)
VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13)
"""


async def run(dsn: str, window_h: float, step_s: float) -> int:
    t0 = time.monotonic()
    area = get_area()                                  # shared AOI (fail-fast)
    conn = await asyncpg.connect(dsn)
    try:
        rows = await conn.fetch(
            "SELECT DISTINCT ON (norad_id) norad_id, tle_line1, tle_line2 "
            "FROM satellite_elements ORDER BY norad_id, epoch DESC NULLS LAST"
        )
        if not rows:
            logger.warning("no TLEs in satellite_elements — nothing to compute")
            return 1

        norads, sats, tle_epochs, geo_flags = [], [], [], []
        for r in rows:
            try:
                sr = Satrec.twoline2rv(r["tle_line1"], r["tle_line2"])
            except (ValueError, RuntimeError):
                continue
            norads.append(r["norad_id"])
            sats.append(sr)
            tle_epochs.append(jd_to_dt(sr.jdsatepoch, sr.jdsatepochF))
            geo_flags.append(is_geosync(r["tle_line2"]))

        start = datetime.now(timezone.utc).replace(microsecond=0)
        jd, fr, dts = sample_times(start, window_h, step_s)
        n_sats, n_steps = len(sats), len(dts)
        logger.info("=" * 60)
        logger.info("Sat-Passes — AOI ground-track passes (SGP4 batch)")
        logger.info("  satellites: %d | window: %.0f h | step: %.0f s | samples/sat: %d",
                    n_sats, window_h, step_s, n_steps)
        logger.info("=" * 60)

        # ---- ONE batched propagation over the whole time grid ----
        arr = SatrecArray(sats)
        e, r_teme, _v = arr.sgp4(jd, fr)               # e:(N,T) r:(N,T,3)
        g = gmst_rad(jd, fr)                            # (T,)
        lats, lons = teme_to_geodetic(
            r_teme[:, :, 0], r_teme[:, :, 1], r_teme[:, :, 2], g[None, :])
        bad = np.asarray(e) != 0
        lats = np.where(bad, np.nan, lats)
        lons = np.where(bad, np.nan, lons)

        # numpy bounding-box prefilter: which satellites ever come near the AOI
        inbox = (~np.isnan(lats)) & (lats >= 11.0) & (lats <= 43.5) \
            & (lons >= 22.0) & (lons <= 65.5)
        near_aoi = inbox.any(axis=1)                    # (N,) non-GEO candidates

        all_rows: list[tuple] = []
        considered = 0
        for n in range(n_sats):
            geo = geo_flags[n]
            if not geo and not near_aoi[n]:
                continue                                # never near the AOI — skip
            considered += 1
            passes = classify_passes(norads[n], dts, lats[n], lons[n], area, geo)
            for p in passes:
                all_rows.append((
                    p["norad_id"], p["start_time"], p["end_time"], p["duration_s"],
                    p["countries"], p["near_countries"], p["closest_km"],
                    p["is_persistent"], p["n_points"], SOURCE, tle_epochs[n],
                    dts[0], dts[-1],
                ))

        async with conn.transaction():
            await conn.execute("DELETE FROM satellite_passes")
            if all_rows:
                await conn.executemany(INSERT, all_rows)

        elapsed = time.monotonic() - t0
        sats_passing = len({row[0] for row in all_rows})
        persistent = sum(1 for row in all_rows if row[7])
        logger.info("=" * 60)
        logger.info("Sat-Passes DONE in %.1fs: %d satellites propagated, %d classified",
                    elapsed, n_sats, considered)
        logger.info("  AOI passes: %d rows | %d distinct satellites | %d GEO-persistent",
                    len(all_rows), sats_passing, persistent)
        logger.info("=" * 60)
        return 0
    finally:
        await conn.close()


def main() -> int:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s | %(levelname)-7s | %(message)s", datefmt="%H:%M:%S")
    load_dotenv(REPO_ROOT / ".env")
    p = argparse.ArgumentParser(description="Sat-Passes: AOI ground-track passes from current TLEs")
    p.add_argument("--window-hours", type=float, default=WINDOW_H)
    p.add_argument("--step-seconds", type=float, default=STEP_S)
    a = p.parse_args()
    dsn = os.getenv("OPENSKY_DB_URL") or os.getenv("DATABASE_URL", "")
    if not dsn:
        print("ERROR: set OPENSKY_DB_URL or DATABASE_URL", file=sys.stderr)
        return 2
    return asyncio.run(run(dsn, a.window_hours, a.step_seconds))


if __name__ == "__main__":
    sys.exit(main())
