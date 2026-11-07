"""Sat-Passes — AOI ground-track passes (backend/passes_derive).

Validates the sub-point math against an independent reference (skyfield-verified
values), the pure pass classification/grouping over the REAL geo_area, the GEO
longitude-band special case, a deterministic real-ISS overpass of the region, and
idempotency. Propagation batch runtime + the capstone distributions are reported
in the Sat-Passes gauntlet.
"""

from datetime import datetime, timedelta, timezone

import numpy as np
from sgp4.api import Satrec

from backend.geo_area import get_area
from backend.passes_derive import (
    classify_passes, gmst_rad, ground_track, jd_to_dt, sample_times, teme_to_geodetic,
)

AREA = get_area()
ISS_L1 = "1 25544U 98067A   26194.12129675  .00004316  00000+0  86456-4 0  9991"
ISS_L2 = "2 25544  51.6304 171.7447 0006685 289.3803  70.6462 15.48996109575778"


# ---- sub-point math validated against skyfield (< 0.01 deg) -----------------

def test_subpoint_matches_reference():
    s = Satrec.twoline2rv(ISS_L1, ISS_L2)
    jd0, fr0 = s.jdsatepoch, s.jdsatepochF
    # (minutes after epoch): skyfield-verified (lat, lon)
    ref = {0: (-0.0000, -162.9325), 30: (44.8175, -42.0299), 90: (-8.8518, 167.1536)}
    for dm, (rlat, rlon) in ref.items():
        _e, r, _v = s.sgp4(jd0, fr0 + dm / 1440.0)
        lat, lon = teme_to_geodetic(r[0], r[1], r[2], gmst_rad(jd0, fr0 + dm / 1440.0))
        assert abs(float(lat) - rlat) < 0.01
        assert abs(float(lon) - rlon) < 0.01


# ---- pure classification over the real geo_area ----------------------------

def _times(n):
    t0 = datetime(2026, 7, 13, 0, 0, tzinfo=timezone.utc)
    return [t0 + timedelta(seconds=60 * i) for i in range(n)]


def test_ground_track_over_syria_is_a_pass():
    # outside … three samples inside Syria … outside  → one pass naming Syria
    lats = [0.0, 10.0, 33.4, 33.5, 33.6, 10.0, 0.0]
    lons = [120.0, 60.0, 36.2, 36.3, 36.4, 20.0, 0.0]
    passes = classify_passes(25544, _times(7), np.array(lats), np.array(lons), AREA, False)
    assert len(passes) == 1
    assert "Syria" in passes[0]["countries"]
    assert passes[0]["is_persistent"] is False
    assert passes[0]["duration_s"] > 0


def test_track_never_over_aoi_has_no_pass():
    lats = [0.0, -10.0, -20.0, 5.0]
    lons = [-150.0, -160.0, -170.0, 150.0]   # Pacific
    assert classify_passes(25544, _times(4), np.array(lats), np.array(lons), AREA, False) == []


def test_two_separate_crossings_are_two_passes():
    # inside Syria … out … inside Iraq  → two discrete passes
    lats = [33.5, 33.5, 0.0, 0.0, 0.0, 33.3, 33.3]
    lons = [36.3, 36.4, 120.0, 121.0, 122.0, 44.3, 44.4]
    passes = classify_passes(25544, _times(7), np.array(lats), np.array(lons), AREA, False)
    assert len(passes) == 2


# ---- GEO longitude-band special case ---------------------------------------

def test_geo_over_region_longitude_band_one_persistent_row():
    n = 30
    lats = np.zeros(n)                 # geosync: sub-point ~equatorial
    lons = np.full(n, 38.0)            # stationed over ~38 E (over the region's longitudes)
    passes = classify_passes(40000, _times(n), lats, lons, AREA, True)
    assert len(passes) == 1
    assert passes[0]["is_persistent"] is True
    assert passes[0]["countries"] or passes[0]["near_countries"]


def test_geo_over_pacific_not_flagged():
    n = 30
    passes = classify_passes(40000, _times(n), np.zeros(n), np.full(n, -150.0), AREA, True)
    assert passes == []


# ---- deterministic real ISS overpass of the region -------------------------

def test_iss_overflies_region_in_24h():
    s = Satrec.twoline2rv(ISS_L1, ISS_L2)
    start = jd_to_dt(s.jdsatepoch, s.jdsatepochF)
    jd, fr, dts = sample_times(start, 24.0, 60.0)
    lats, lons = ground_track(s, jd, fr)
    passes = classify_passes(25544, dts, lats, lons, AREA, False)
    assert len(passes) >= 1
    crossed = {c for p in passes for c in p["countries"]}
    assert "Syria" in crossed              # verified overpass of the region


def test_classification_is_idempotent():
    s = Satrec.twoline2rv(ISS_L1, ISS_L2)
    jd, fr, dts = sample_times(jd_to_dt(s.jdsatepoch, s.jdsatepochF), 24.0, 60.0)
    lats, lons = ground_track(s, jd, fr)
    a = classify_passes(25544, dts, lats, lons, AREA, False)
    b = classify_passes(25544, dts, lats, lons, AREA, False)
    assert a == b                          # same TLE + window → identical passes
