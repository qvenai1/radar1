#!/usr/bin/env python3
"""BASELINE-1 — build each satellite's USUAL AOI-crossing geometry (migration 041).

⛔ WHY THIS RUNS BACKWARD. The bot should be able to say "this pass is closer than usual
for this object" instead of listing every predicted pass. That needs a baseline, and this
database had none: `satellite_passes` is fully recomputed each run (one distinct
`computed_at`) and `satellite_elements` keeps 1-2 epochs per satellite. Nothing to derive
"usual" from, and waiting for history would have meant weeks of a sentence we could not yet
justify.

SGP4 propagates backward exactly as it propagates forward, so this runs the CURRENT element
over the PRECEDING window and aggregates the crossings it finds.

⛔ AND THAT IS A DERIVATION, NOT A RECORD. No sensor saw these passes. They are the same
computation as the forward prediction, pointed the other way, so the result characterises
THE ORBIT'S GEOMETRY — how often this track crosses the AOI and how close it comes — and is
stored with `method` saying so. Only DISTRIBUTIONS are kept: SGP4 error grows with
|t − epoch| in both directions, so an individual back-propagated pass is not accurate
enough to assert, while a count and a quantile over dozens of them are robust to it.

⚠ IT REUSES `classify_passes` UNCHANGED. A second definition of "a pass" would drift from
the forward one, and then "usual" and "today" would be measuring different things — the
exact defect that makes a baseline worse than none.

Usage:
    .venv/bin/python scripts/build_pass_baseline.py            # 14-day window
    .venv/bin/python scripts/build_pass_baseline.py --days 30
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import asyncpg
import numpy as np
from dotenv import load_dotenv
from sgp4.api import Satrec, SatrecArray

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.geo_area import get_area                      # noqa: E402
from backend.pass_baseline import aggregate                 # noqa: E402
from backend.passes_derive import (                         # noqa: E402
    STEP_S, classify_passes, gmst_rad, is_geosync, jd_to_dt, sample_times,
    teme_to_geodetic,
)

load_dotenv()
logger = logging.getLogger("build_pass_baseline")

#: 14 days. Long enough that a twice-daily LEO crossing yields ~28 samples (well past
#: pass_baseline.MIN_SAMPLE = 8), short enough that SGP4 error from a current element stays
#: modest — the error grows with |t − epoch|, and a 90-day window would be characterising a
#: propagated fiction rather than the orbit.
DEFAULT_DAYS = 14

UPSERT = """
INSERT INTO satellite_pass_baseline
    (norad_id, window_days, n_passes, passes_per_day, closest_min, closest_p10,
     closest_median, usual_countries, method, tle_epoch, computed_at)
VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11)
ON CONFLICT (norad_id) DO UPDATE SET
    window_days = EXCLUDED.window_days, n_passes = EXCLUDED.n_passes,
    passes_per_day = EXCLUDED.passes_per_day, closest_min = EXCLUDED.closest_min,
    closest_p10 = EXCLUDED.closest_p10, closest_median = EXCLUDED.closest_median,
    usual_countries = EXCLUDED.usual_countries, method = EXCLUDED.method,
    tle_epoch = EXCLUDED.tle_epoch, computed_at = EXCLUDED.computed_at
"""

METHOD = "back-propagated-sgp4"


async def run(dsn: str, days: int, step_s: float, military_only: bool) -> int:
    t0 = time.monotonic()
    area = get_area()
    conn = await asyncpg.connect(dsn)
    try:
        # ⚠ Scoped to the objects the alert feed can actually surface, so the run costs what
        # it must and no more. The feed's own filter is `users ILIKE '%Military%'` minus the
        # navigation constellations (GNSS-1); mirroring it here means every object that can
        # produce an alert has a baseline, and nothing else is propagated.
        q = ("SELECT DISTINCT ON (e.norad_id) e.norad_id, e.tle_line1, e.tle_line2 "
             "FROM satellite_elements e ")
        if military_only:
            q += ("JOIN satellite_strategic s ON s.norad_id = e.norad_id "
                  "WHERE s.users ILIKE '%Military%' "
                  "  AND s.purpose IS DISTINCT FROM 'Navigation/Global Positioning' ")
        q += "ORDER BY e.norad_id, e.epoch DESC NULLS LAST"
        rows = await conn.fetch(q)
        if not rows:
            logger.warning("no TLEs to baseline")
            return 1

        norads, sats, epochs, geo = [], [], [], []
        for r in rows:
            try:
                sr = Satrec.twoline2rv(r["tle_line1"], r["tle_line2"])
            except (ValueError, RuntimeError):
                continue
            norads.append(r["norad_id"]); sats.append(sr)
            epochs.append(jd_to_dt(sr.jdsatepoch, sr.jdsatepochF))
            geo.append(is_geosync(r["tle_line2"]))

        # ⛔ START IN THE PAST, RUN FORWARD TO NOW. `sample_times` builds a forward grid from
        # whatever start it is given, so "backward" is expressed as an earlier start rather
        # than by inverting the stepping — one time grid, one code path, no sign errors.
        now = datetime.now(timezone.utc).replace(microsecond=0)
        start = now - timedelta(days=days)
        jd, fr, dts = sample_times(start, days * 24.0, step_s)

        logger.info("=" * 62)
        logger.info("BASELINE-1 — back-propagated AOI crossing geometry")
        logger.info("  satellites: %d | window: %d d (back to %s) | step: %.0f s | samples: %d",
                    len(sats), days, start.date(), step_s, len(dts))
        logger.info("  ⚠ derived, NOT observed — no sensor saw these passes")
        logger.info("=" * 62)

        arr = SatrecArray(sats)
        e, r_teme, _v = arr.sgp4(jd, fr)
        g = gmst_rad(jd, fr)
        lats, lons = teme_to_geodetic(
            r_teme[:, :, 0], r_teme[:, :, 1], r_teme[:, :, 2], g[None, :])
        bad = np.asarray(e) != 0
        lats = np.where(bad, np.nan, lats); lons = np.where(bad, np.nan, lons)

        inbox = (~np.isnan(lats)) & (lats >= 11.0) & (lats <= 43.5) \
            & (lons >= 22.0) & (lons <= 65.5)
        near = inbox.any(axis=1)

        out, skipped = [], 0
        for n in range(len(sats)):
            if not geo[n] and not near[n]:
                skipped += 1
                continue
            passes = classify_passes(norads[n], dts, lats[n], lons[n], area, geo[n])
            if not passes:
                skipped += 1
                continue
            agg = aggregate([p["closest_km"] for p in passes],
                            [(p["countries"] or []) + (p["near_countries"] or [])
                             for p in passes], days)
            out.append((norads[n], agg["window_days"], agg["n_passes"],
                        agg["passes_per_day"], agg["closest_min"], agg["closest_p10"],
                        agg["closest_median"], agg["usual_countries"], METHOD,
                        epochs[n], now))

        if out:
            await conn.executemany(UPSERT, out)
        logger.info("baselined %d satellites (%d never near the AOI) in %.1fs",
                    len(out), skipped, time.monotonic() - t0)

        thin = sum(1 for o in out if o[2] < 8)
        if thin:
            logger.info("  ⚠ %d of them have <8 passes in the window — they will read as "
                        "UNKNOWN, never as 'routine' (pass_baseline.MIN_SAMPLE)", thin)
        return 0
    finally:
        await conn.close()


def main() -> int:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s")
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--days", type=int, default=DEFAULT_DAYS)
    ap.add_argument("--step-s", type=float, default=STEP_S)
    ap.add_argument("--all", action="store_true",
                    help="baseline every tracked object, not only the alertable military set")
    ap.add_argument("--db-url", default=os.environ.get("DATABASE_URL"))
    a = ap.parse_args()
    if not a.db_url:
        logger.error("DATABASE_URL is not set")
        return 2
    return asyncio.run(run(a.db_url, a.days, a.step_s, military_only=not a.all))


if __name__ == "__main__":
    raise SystemExit(main())
