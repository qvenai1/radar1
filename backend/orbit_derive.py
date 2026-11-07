"""
Deterministic orbit classification DERIVED from a TLE — scraplan's live orbital
layer (Sat-Orbit). Unlike UCS's self-reported 2023 orbit fields, this is COMPUTED
from the current TLE via the reference SGP4 implementation (python-sgp4, Brandon
Rhodes) and is always current — recomputed whenever the TLE refreshes.

Pure and I/O-free: `tle_to_orbit(line1, line2)` parses the two lines with SGP4
and returns the standard derived quantities + a reproducible class, or None for
an invalid/decayed/unparseable element (the caller logs and skips it — never a
garbage row). The Alpha-5 catalog-number decoding lives in the collector's
parse_3le (S2c fix); SGP4 itself keeps the raw satnum, so 6-digit ids derive
fine here.

DERIVED QUANTITIES (deterministic from the two lines, via SGP4 WGS-72):
  apogee_km / perigee_km  — altitudes above the surface (sat.alta/altp * Re)
  semimajor_axis_km       — sat.a * Re
  eccentricity            — sat.ecco
  inclination_deg         — sat.inclo
  period_min              — 2π / no_kozai
  mean_motion             — revolutions per day

ORBIT-CLASS THRESHOLDS (documented + citable; evaluated in this order):
  1. HEO  — eccentricity >= 0.25                     (Molniya/Tundra: highly elliptical)
  2. LEO  — apogee altitude  < 2000 km
  3. GEO  — |mean altitude - 35786 km| <= 1500 km    (geosynchronous belt)
  4. MEO  — 2000 km <= mean altitude < 34286 km      (between LEO and the GEO belt)
  5. other— anything above the GEO belt (high circular / graveyard / gaps)
  mean altitude = (apogee + perigee) / 2.

FLAGS (tolerances documented):
  is_geostationary — GEO class AND inclination <= 1.0 deg AND ecc <= 0.01 AND
                     |period - 1436.07 min| <= 30 min (near-equatorial, circular,
                     one sidereal day). A GEO-belt but inclined/eccentric object
                     is GEO class yet NOT geostationary.
  is_sun_synchronous — computed ONLY for LEO (SSO is a LEO regime); True iff the
                     orbit's nodal precession matches the mean-solar rate
                     (0.98565 deg/day) within a required-inclination tolerance of
                     1.0 deg. NULL for non-LEO orbits (not applicable — not
                     guessed).

PROVENANCE: source='derived-sgp4'; tle_epoch is the epoch of the TLE this row was
computed from; the caller stamps computed_at. The table is a CACHE of the LATEST
derivation — a newer TLE recomputes and updates the row.
"""

from __future__ import annotations

import logging
import math
from datetime import datetime, timedelta, timezone
from typing import Any

from sgp4.api import WGS72, Satrec

logger = logging.getLogger("orbit_derive")

SOURCE = "derived-sgp4"

# WGS-72 constants (SGP4's own model — keep the derivation self-consistent).
R_EARTH_KM = 6378.135
J2 = 0.001082616
SIDEREAL_DAY_MIN = 1436.068                     # Earth sidereal rotation, minutes
SUN_SYNC_DEG_PER_DAY = 360.0 / 365.2421897      # mean-solar precession, ~0.98565

# orbit-class thresholds (documented above)
HEO_MIN_ECC = 0.25
LEO_MAX_ALT_KM = 2000.0
GEO_ALT_KM = 35786.0
GEO_BAND_KM = 1500.0
# flag tolerances
GEOSTATIONARY_INCL_DEG = 1.0
GEOSTATIONARY_ECC = 0.01
GEOSTATIONARY_PERIOD_TOL_MIN = 30.0
SUNSYNC_INCL_TOL_DEG = 1.0

_UNIX_EPOCH_JD = 2440587.5


def _jd_to_dt(jd: float, fr: float) -> datetime:
    """Julian date (+ fraction) → tz-aware UTC datetime."""
    seconds = (jd + fr - _UNIX_EPOCH_JD) * 86400.0
    return datetime(1970, 1, 1, tzinfo=timezone.utc) + timedelta(seconds=seconds)


def classify_orbit(apogee_km: float, perigee_km: float, ecc: float) -> str:
    """Reproducible LEO/MEO/GEO/HEO/other from altitude + eccentricity."""
    mean_alt = (apogee_km + perigee_km) / 2.0
    if ecc >= HEO_MIN_ECC:
        return "HEO"
    if apogee_km < LEO_MAX_ALT_KM:
        return "LEO"
    if abs(mean_alt - GEO_ALT_KM) <= GEO_BAND_KM:
        return "GEO"
    if LEO_MAX_ALT_KM <= mean_alt < (GEO_ALT_KM - GEO_BAND_KM):
        return "MEO"
    return "other"


def _sun_synchronous(orbit_class: str, a_km: float, ecc: float,
                     incl_deg: float, mean_motion_rev_day: float) -> bool | None:
    """SSO is a LEO regime → None for non-LEO (not applicable). For LEO: True iff
    the nodal precession matches the mean-solar rate within the inclination
    tolerance."""
    if orbit_class != "LEO":
        return None
    n_rad_s = mean_motion_rev_day * 2.0 * math.pi / 86400.0
    p = a_km * (1.0 - ecc * ecc)
    denom = 1.5 * n_rad_s * J2 * (R_EARTH_KM / p) ** 2
    if p <= 0 or denom == 0:
        return None
    target_rad_s = math.radians(SUN_SYNC_DEG_PER_DAY) / 86400.0
    cos_i_req = -target_rad_s / denom
    if abs(cos_i_req) > 1.0:
        return False                       # no SSO inclination exists at this altitude
    i_req_deg = math.degrees(math.acos(cos_i_req))
    return abs(incl_deg - i_req_deg) <= SUNSYNC_INCL_TOL_DEG


def tle_to_orbit(line1: str, line2: str) -> dict[str, Any] | None:
    """Two TLE lines → derived orbit dict, or None if invalid/decayed/unparseable
    (reason logged at DEBUG). Deterministic; no I/O."""
    try:
        sat = Satrec.twoline2rv(line1, line2, WGS72)
    except (ValueError, RuntimeError) as exc:
        logger.debug("unparseable TLE: %s", exc)
        return None
    if sat.error != 0:
        logger.debug("SGP4 flagged element (error=%d)", sat.error)
        return None

    ecc = float(sat.ecco)
    n_rad_min = float(sat.no_kozai)
    if not (0.0 <= ecc < 1.0) or n_rad_min <= 0.0:
        logger.debug("non-elliptical/degenerate element (ecc=%.4f, n=%.6f)", ecc, n_rad_min)
        return None

    a_km = float(sat.a) * R_EARTH_KM
    apogee_km = float(sat.alta) * R_EARTH_KM
    perigee_km = float(sat.altp) * R_EARTH_KM
    if a_km <= 0.0 or perigee_km < 0.0:
        logger.debug("decayed/invalid geometry (a=%.1f, perigee=%.1f)", a_km, perigee_km)
        return None

    incl_deg = math.degrees(float(sat.inclo))
    period_min = 2.0 * math.pi / n_rad_min
    mean_motion_rev_day = 1440.0 / period_min
    orbit_class = classify_orbit(apogee_km, perigee_km, ecc)

    is_geostationary = (
        orbit_class == "GEO"
        and incl_deg <= GEOSTATIONARY_INCL_DEG
        and ecc <= GEOSTATIONARY_ECC
        and abs(period_min - SIDEREAL_DAY_MIN) <= GEOSTATIONARY_PERIOD_TOL_MIN
    )
    is_sun_synchronous = _sun_synchronous(orbit_class, a_km, ecc, incl_deg, mean_motion_rev_day)

    return {
        "orbit_class": orbit_class,
        "apogee_km": round(apogee_km, 3),
        "perigee_km": round(perigee_km, 3),
        "eccentricity": round(ecc, 7),
        "inclination_deg": round(incl_deg, 4),
        "period_min": round(period_min, 4),
        "mean_motion": round(mean_motion_rev_day, 8),
        "semimajor_axis_km": round(a_km, 3),
        "is_geostationary": is_geostationary,
        "is_sun_synchronous": is_sun_synchronous,
        "tle_epoch": _jd_to_dt(sat.jdsatepoch, sat.jdsatepochF),
    }
