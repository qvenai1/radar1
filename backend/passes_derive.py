"""
Sat-Passes — the REGIONAL capstone layer: which tracked satellites pass over the
seven-country AOI, and when, computed deterministically from TLEs (python-sgp4).

PASS DEFINITION (documented, observer-independent, reproducible):
  A "pass" is a contiguous interval during which the satellite's SUB-SATELLITE
  GROUND-TRACK POINT (the geodetic lat/lon directly beneath it) falls INSIDE a
  seven-country polygon OR NEAR it (within geo_area's 50 km border buffer) — the
  SAME classifier (backend/geo_area.py) aircraft, vessels, and reentry use. This
  is an OVERFLIGHT definition (satellite directly over the region), chosen over a
  visibility/elevation definition because it is observer-independent and directly
  analogous to every other geo-classified layer in scraplan.

  Sub-satellite point: SGP4 TEME position → ECEF via GMST rotation → WGS-84
  geodetic lat/lon (the standard satellite.js/Vallado method; sub-degree accurate,
  which is ample for country-polygon classification). Validated against skyfield.

TIME-STEP / WINDOW: propagate every STEP_S seconds over a forward WINDOW_H-hour
window (defaults 60 s / 24 h; configurable). At 60 s a LEO ground point moves
~450 km, so an AOI crossing (region ~3000 km across) yields several samples —
enough to bound each pass. The window is a forward PREDICTION recomputed as TLEs
refresh (see the backfill), not history.

GEO SPECIAL CASE (documented): a geosynchronous satellite's sub-point is ~fixed
in longitude, so it is either persistently over/near the AOI longitude band for
the whole window or never. Emitting a "pass" per time-step would be millions of
trivial rows. Instead, for a GEO/geosync satellite we emit AT MOST ONE row with
is_persistent=True spanning the window when ANY sample is INSIDE/NEAR the AOI, and
none otherwise. Non-GEO satellites get discrete passes (contiguous in-AOI runs).

PROVENANCE: source='derived-sgp4', tle_epoch (per satellite), computed_at.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import numpy as np


SOURCE = "derived-sgp4"

# defaults (configurable in the backfill)
STEP_S = 60.0
WINDOW_H = 24.0
# a geosync satellite drifts <~1 rev/day; treat mean motion below this (rev/day)
# as GEO/geosync for the persistent-longitude handling.
GEO_MEAN_MOTION_MAX = 1.1

# WGS-84
_WGS84_A = 6378.137
_WGS84_F = 1.0 / 298.257223563
_WGS84_E2 = _WGS84_F * (2.0 - _WGS84_F)
_UNIX_EPOCH_JD = 2440587.5


def gmst_rad(jd: Any, fr: Any) -> Any:
    """Greenwich Mean Sidereal Time (radians) for JD(+fraction). Vallado's
    polynomial (UT1≈UTC); scalar or numpy array. Matches satellite.js gstime."""
    t = (np.asarray(jd, dtype=float) + np.asarray(fr, dtype=float) - 2451545.0) / 36525.0
    sec = (67310.54841
           + (876600.0 * 3600.0 + 8640184.812866) * t
           + 0.093104 * t * t
           - 6.2e-6 * t * t * t)
    deg = (sec / 240.0) % 360.0
    return np.radians(deg)


def teme_to_geodetic(x: Any, y: Any, z: Any, gmst: Any) -> tuple[Any, Any]:
    """TEME position (km) + GMST (rad) → WGS-84 geodetic (lat_deg, lon_deg).
    Vectorized (numpy). Ignores polar motion/nutation — sub-degree accurate, ample
    for country-polygon classification."""
    x = np.asarray(x, dtype=float); y = np.asarray(y, dtype=float); z = np.asarray(z, dtype=float)
    cosg, sing = np.cos(gmst), np.sin(gmst)
    xe = x * cosg + y * sing        # TEME → ECEF (rotate by -GMST about z)
    ye = -x * sing + y * cosg
    ze = z
    lon = np.degrees(np.arctan2(ye, xe))
    r = np.hypot(xe, ye)
    lat = np.arctan2(ze, r * (1.0 - _WGS84_E2))     # initial geodetic latitude
    for _ in range(5):                               # converges in a few iterations
        sinlat = np.sin(lat)
        n = _WGS84_A / np.sqrt(1.0 - _WGS84_E2 * sinlat * sinlat)
        lat = np.arctan2(ze + _WGS84_E2 * n * sinlat, r)
    return np.degrees(lat), (lon + 180.0) % 360.0 - 180.0


def jd_to_dt(jd: float, fr: float) -> datetime:
    return datetime(1970, 1, 1, tzinfo=timezone.utc) + timedelta(
        seconds=(jd + fr - _UNIX_EPOCH_JD) * 86400.0)


def _mean_motion_rev_day(line2: str) -> float | None:
    try:
        return float(line2[52:63])
    except (ValueError, IndexError):
        return None


def is_geosync(line2: str) -> bool:
    n = _mean_motion_rev_day(line2)
    return n is not None and n <= GEO_MEAN_MOTION_MAX


def classify_passes(
    norad_id: int, times: list[datetime], lats: Any, lons: Any,
    area: Any, is_geo: bool,
) -> list[dict[str, Any]]:
    """Group a satellite's ground-track samples into AOI passes (PURE — no I/O,
    no propagation). `times` is the per-sample UTC datetimes; lats/lons the
    sub-point degrees. `area` is a loaded AreaOfInterest. Points are gated by a
    cheap bounding box before the shapely classify. GEO → at most one persistent
    row; non-GEO → discrete contiguous passes."""
    lats = np.asarray(lats, dtype=float); lons = np.asarray(lons, dtype=float)

    if is_geo:
        # GEO/geosync special case (documented): the sub-point is ~equatorial, so
        # the ground-track POLYGON test does not apply to the northern AOI (a
        # geosync sat is never directly over 29-42 N). "Over the region" for a GEO
        # is instead "STATIONED OVER THE AOI LONGITUDE BAND": test a point at the
        # AOI's central latitude and the satellite's ~fixed stationing longitude
        # against geo_area. If it lands INSIDE/NEAR the AOI, emit AT MOST ONE
        # persistent row for the window (never per-step spam); otherwise none.
        valid = lons[~np.isnan(lons)]
        if valid.size == 0:
            return []
        geo_lon = float(np.median(valid))
        col = area.classify_columns(area.center[0], geo_lon)
        if col.country is None and col.near_country is None:
            return []
        return [{
            "norad_id": norad_id, "start_time": times[0], "end_time": times[-1],
            "duration_s": (times[-1] - times[0]).total_seconds(),
            "countries": [col.country] if col.country else [],
            "near_countries": [col.near_country] if col.near_country else [],
            "closest_km": round(col.distance_km, 1) if col.distance_km is not None else None,
            "is_persistent": True, "n_points": int(valid.size),
        }]

    # non-GEO: ground-track polygon passes. Cheap bounding-box gate (+~1.5 deg pad
    # for the NEAR buffer) before the shapely classify.
    latmin, latmax, lonmin, lonmax = 11.0, 43.5, 22.0, 65.5
    in_pts: list[tuple[int, str | None, str | None, float]] = []
    for i in range(len(times)):
        la, lo = float(lats[i]), float(lons[i])
        if not (latmin <= la <= latmax and lonmin <= lo <= lonmax):
            continue
        col = area.classify_columns(la, lo)
        if col.country is None and col.near_country is None:
            continue  # OUTSIDE the buffer
        in_pts.append((i, col.country, col.near_country, col.distance_km))
    if not in_pts:
        return []

    # split into contiguous runs (a gap of >1 step ends a pass)
    passes: list[dict[str, Any]] = []
    run: list[tuple[int, str | None, str | None, float]] = []

    def _flush() -> None:
        if not run:
            return
        idxs = [p[0] for p in run]
        countries = sorted({p[1] for p in run if p[1]})
        nears = sorted({p[2] for p in run if p[2]})
        closest = min(p[3] for p in run)
        start = times[idxs[0]]
        # end extends one step past the last in-point (interval, not instant)
        step = (times[1] - times[0]) if len(times) > 1 else timedelta(seconds=STEP_S)
        end = times[idxs[-1]] + step
        passes.append({
            "norad_id": norad_id, "start_time": start, "end_time": end,
            "duration_s": (end - start).total_seconds(),
            "countries": countries, "near_countries": nears,
            "closest_km": round(closest, 1), "is_persistent": False,
            "n_points": len(run),
        })

    prev_i = None
    for p in in_pts:
        if prev_i is not None and p[0] != prev_i + 1:
            _flush(); run = []
        run.append(p); prev_i = p[0]
    _flush()
    return passes


def ground_track(satrec: Any, jd: Any, fr: Any) -> tuple[Any, Any]:
    """One satellite's sub-point track over the jd/fr sample times → (lats, lons)
    degrees. Uses SGP4 array propagation; points with SGP4 errors are NaN."""
    e, r, _v = satrec.sgp4_array(np.asarray(jd), np.asarray(fr))
    g = gmst_rad(jd, fr)
    lat, lon = teme_to_geodetic(r[:, 0], r[:, 1], r[:, 2], g)
    bad = np.asarray(e) != 0
    lat = np.where(bad, np.nan, lat)
    lon = np.where(bad, np.nan, lon)
    return lat, lon


def sample_times(start: datetime, window_h: float, step_s: float) -> tuple[Any, Any, list[datetime]]:
    """Build (jd_array, fr_array, datetimes) for the forward window."""
    n = int(round(window_h * 3600.0 / step_s))
    dts = [start + timedelta(seconds=step_s * i) for i in range(n + 1)]
    base_jd = _UNIX_EPOCH_JD
    total = np.array([(dt - datetime(1970, 1, 1, tzinfo=timezone.utc)).total_seconds()
                      / 86400.0 for dt in dts])
    jd = np.full(len(dts), base_jd)
    fr = total
    return jd, fr, dts
