"""Sat-Orbit — deterministic orbit classification from TLEs (backend/orbit_derive).

Pure-function tests against REAL TLEs (one per orbit class), the documented class
thresholds, the Alpha-5 6-digit case (reuses the S2c fix — SGP4 derives it), the
geostationary/sun-synchronous flags (incl. the non-LEO NULL case), and
invalid-TLE skipping. Idempotency + the catalog distribution are exercised
against the live DB in the Sat-Orbit gauntlet.
"""

from datetime import datetime

from backend.orbit_derive import classify_orbit, tle_to_orbit

# Real TLEs (verified), one per class.
ISS = ("1 25544U 98067A   26194.12129675  .00004316  00000+0  86456-4 0  9991",
       "2 25544  51.6304 171.7447 0006685 289.3803  70.6462 15.48996109575778")
GPS = ("1 26407U 00040A   26199.21470377  .00000042  00000+0  00000+0 0  9995",
       "2 26407  54.8482 213.9297 0120433 302.7808  63.9034  2.00557914190576")
TDRS3 = ("1 19548U 88091B   26199.23794822 -.00000309  00000+0  00000+0 0  9998",
         "2 19548  12.5788 340.7966 0038122 355.1328 356.6773  1.00280117125698")
NIMIQ4 = ("1 33373U 08044A   26199.24970098 -.00000228  00000+0  00000+0 0  9992",
          "2 33373   0.0156  49.0844 0001865  56.9665 197.8950  1.00270980 65297")
MOLNIYA = ("1 25847U 99036A   26198.92797825  .00000326  00000+0  00000+0 0  9997",
           "2 25847  63.5287 152.4792 6756422 276.7411  16.4676  2.00635118198036")
SENTINEL2A = ("1 40697U 15028A   26199.33358472 -.00000029  00000+0  54540-5 0  9998",
              "2 40697  98.5675 273.7415 0001289  95.7172 264.4158 14.30814734578182")


# ---- known satellites classify + elements within tolerance -----------------

def test_iss_is_leo():
    o = tle_to_orbit(*ISS)
    assert o["orbit_class"] == "LEO"
    assert 400 < o["apogee_km"] < 450 and 400 < o["perigee_km"] < 430
    assert 90 < o["period_min"] < 95
    assert o["is_geostationary"] is False
    assert o["is_sun_synchronous"] is False        # LEO but prograde 51.6 deg
    assert isinstance(o["tle_epoch"], datetime)


def test_gps_is_meo():
    o = tle_to_orbit(*GPS)
    assert o["orbit_class"] == "MEO"
    assert 19000 < o["perigee_km"] and o["apogee_km"] < 21000
    assert 700 < o["period_min"] < 730
    assert o["is_sun_synchronous"] is None         # non-LEO → not applicable (NULL)


def test_tdrs_inclined_geo_not_geostationary():
    o = tle_to_orbit(*TDRS3)
    assert o["orbit_class"] == "GEO"
    assert o["is_geostationary"] is False           # 12.6 deg inclined geosync
    assert o["is_sun_synchronous"] is None
    assert 1400 < o["period_min"] < 1470


def test_nimiq4_is_geostationary():
    o = tle_to_orbit(*NIMIQ4)
    assert o["orbit_class"] == "GEO"
    assert o["is_geostationary"] is True            # ~0 deg, circular, sidereal
    assert o["inclination_deg"] < 1.0


def test_molniya_is_heo():
    o = tle_to_orbit(*MOLNIYA)
    assert o["orbit_class"] == "HEO"
    assert o["eccentricity"] > 0.6
    assert o["apogee_km"] > 35000 and o["perigee_km"] < 3000
    assert o["is_sun_synchronous"] is None


def test_sentinel2a_is_sun_synchronous():
    o = tle_to_orbit(*SENTINEL2A)
    assert o["orbit_class"] == "LEO"
    assert o["is_sun_synchronous"] is True          # ~98.6 deg SSO
    assert o["inclination_deg"] > 97


# ---- documented thresholds (not loose guessing) ----------------------------

def test_class_thresholds():
    assert classify_orbit(1000, 1000, 0.25) == "HEO"     # ecc boundary → HEO
    assert classify_orbit(1999, 1999, 0.24) == "LEO"     # just under HEO ecc, low
    assert classify_orbit(1999, 1999, 0.01) == "LEO"     # apogee < 2000 → LEO
    assert classify_orbit(2100, 2100, 0.01) == "MEO"     # above LEO, below GEO belt
    assert classify_orbit(35786, 35786, 0.001) == "GEO"  # geosync altitude
    assert classify_orbit(40000, 40000, 0.001) == "other"  # above GEO belt


# ---- Alpha-5 (6-digit id) reuses the S2c fix (SGP4 derives, not dropped) -----

def test_alpha5_six_digit_classifies():
    o = tle_to_orbit(
        "1 A0001U 24001A   26194.50000000  .00000000  00000+0  00000+0 0  9990",
        "2 A0001  53.0000 100.0000 0001000  90.0000 270.0000 15.50000000    01",
    )
    assert o is not None and o["orbit_class"] == "LEO"   # A0001 = 100001, not dropped


# ---- invalid / decayed → skipped, no crash, no garbage row -----------------

def test_invalid_tle_returns_none():
    assert tle_to_orbit("garbage line one", "garbage line two") is None
    assert tle_to_orbit("", "") is None
