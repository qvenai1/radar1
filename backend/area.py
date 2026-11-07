"""Area-search — the space (satellite) domain: which satellites' ground-tracks
crossed an arbitrary box in a window (D4).

Air + sea area-search are plain SQL box queries (native-point GiST, migration 031)
and live in the endpoint. Space has NO stored positions and satellite_passes is
AOI-scoped + forward-only — so an arbitrary box × window is an SGP4 RECOMPUTE,
reusing the passes_derive machinery (the same batched SatrecArray path as
scripts/backfill_passes.py). CPU-bound, so the endpoint runs this in a thread.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import numpy as np
from sgp4.api import Satrec, SatrecArray

from .passes_derive import gmst_rad, sample_times, teme_to_geodetic

# Keep the batched propagation bounded: a fine-enough step to catch a fast LEO
# crossing a reasonably-sized box, capped so the grid never explodes. A LEO
# sub-point moves ~7 km/s, so 15 s ≈ 105 km between samples — a box smaller than
# that can be stepped over (documented sampling limit; a segment-intersection test
# is the future refinement).
MIN_STEP_S = 15.0
MAX_STEPS = 1200


def _step_seconds(window_h: float) -> float:
    return max(MIN_STEP_S, window_h * 3600.0 / MAX_STEPS)


def satellites_crossing_box(
    tle_rows: list[tuple[int, str, str]],
    lat_min: float, lat_max: float, lon_min: float, lon_max: float,
    start: datetime, window_h: float,
) -> list[dict[str, Any]]:
    """Batched SGP4 over `start`..+window_h; return satellites whose sub-point
    entered the box, each with the FIRST in-box time + that sub-point. PURE compute
    (no I/O) so the caller can run it in a thread. `tle_rows` = (norad, line1, line2).
    """
    norads: list[int] = []
    sats: list[Satrec] = []
    for norad, l1, l2 in tle_rows:
        try:
            sats.append(Satrec.twoline2rv(l1, l2))
            norads.append(norad)
        except (ValueError, RuntimeError):
            continue
    if not sats:
        return []

    jd, fr, dts = sample_times(start, window_h, _step_seconds(window_h))
    e, r_teme, _v = SatrecArray(sats).sgp4(jd, fr)          # e:(N,T)  r:(N,T,3)
    g = gmst_rad(jd, fr)                                    # (T,)
    lats, lons = teme_to_geodetic(
        r_teme[:, :, 0], r_teme[:, :, 1], r_teme[:, :, 2], g[None, :])
    bad = np.asarray(e) != 0
    inbox = (~bad) & ~np.isnan(lats) & (lats >= lat_min) & (lats <= lat_max) \
        & (lons >= lon_min) & (lons <= lon_max)

    out: list[dict[str, Any]] = []
    for n in range(len(sats)):
        row = inbox[n]
        if not row.any():
            continue
        first = int(np.argmax(row))                        # first True index
        out.append({
            "norad": norads[n],
            "ts": dts[first],
            "lat": float(lats[n, first]),
            "lon": float(lons[n, first]),
        })
    return out
