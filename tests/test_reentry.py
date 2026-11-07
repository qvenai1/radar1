"""S2c — Space-Track TIP reentry parsing + geo-linking (tracker_reentry).

Pure-function tests: TIP record → normalized reentry row, longitude
normalization, and geo classification of the nominal predicted impact point
against the REAL shared classifier (backend/geo_area.py), consistent with how
aircraft are classified. Idempotency, the identity join, and the daily rate
limit are exercised against the live DB / API in the S2c verification gauntlet
(matching the suite's no-DB convention for the S1/E2 layers).
"""

from datetime import datetime, timezone

from backend.geo_area import get_area
from tracker_reentry import (
    _norm_lon, classify_reentry, parse_tip_record, reentry_row,
)

AREA = get_area()

# A real TIP JSON record shape (all values strings, as Space-Track returns them).
SAMPLE = {
    "NORAD_CAT_ID": "60", "MSG_EPOCH": "2012-03-28 12:30:00",
    "INSERT_EPOCH": "2012-03-28 06:34:36", "DECAY_EPOCH": "2012-03-28 01:14:00",
    "WINDOW": "14", "REV": "58623", "DIRECTION": "descending",
    "LAT": "-16.8", "LON": "286.8", "INCL": "49.9", "NEXT_REPORT": "0",
    "ID": "3027", "HIGH_INTEREST": "N", "OBJECT_NUMBER": "60",
}


def _tip(**over):
    return {**SAMPLE, **over}


# ---- parsing ---------------------------------------------------------------

def test_parse_tip_full():
    r = parse_tip_record(SAMPLE)
    assert r["id"] == 3027
    assert r["norad_id"] == 60
    assert r["decay_epoch"] == datetime(2012, 3, 28, 1, 14, tzinfo=timezone.utc)
    assert r["msg_epoch"] == datetime(2012, 3, 28, 12, 30, tzinfo=timezone.utc)
    assert r["window_min"] == 14
    assert r["lat"] == -16.8
    assert abs(r["lon"] - (-73.2)) < 1e-9          # 286.8 E -> -73.2
    assert r["direction"] == "descending"
    assert r["incl"] == 49.9
    assert r["rev"] == 58623
    assert r["next_report"] == 0
    assert r["high_interest"] is False


def test_parse_high_interest_true():
    assert parse_tip_record(_tip(HIGH_INTEREST="Y"))["high_interest"] is True


def test_parse_skips_unkeyable():
    assert parse_tip_record(_tip(ID="")) is None            # no message id
    assert parse_tip_record({"ID": "5"}) is None            # no norad
    assert parse_tip_record("not a dict") is None


def test_malformed_subfields_degrade_but_row_kept():
    r = parse_tip_record(_tip(WINDOW="", LAT="n/a", DECAY_EPOCH="garbage", INCL=""))
    assert r is not None and r["id"] == 3027                # keyable → kept
    assert r["window_min"] is None and r["lat"] is None
    assert r["decay_epoch"] is None and r["incl"] is None


def test_raw_uncertainty_preserved():
    # imprecision fields are not hidden: window (± minutes) + next_report survive
    r = parse_tip_record(_tip(WINDOW="120", NEXT_REPORT="6"))
    assert r["window_min"] == 120 and r["next_report"] == 6


def test_lon_normalization():
    assert abs(_norm_lon(286.8) - (-73.2)) < 1e-9
    assert abs(_norm_lon(36.2765) - 36.2765) < 1e-9        # already in range
    assert abs(_norm_lon(329.2687) - (-30.7313)) < 1e-9
    assert _norm_lon(None) is None


# ---- geo-linking (REAL geo_area, same classifier as aircraft) --------------

def test_geo_inside_sets_country():
    # nominal impact over Damascus, Syria
    r = classify_reentry(parse_tip_record(_tip(LAT="33.5138", LON="36.2765")), AREA)
    assert r["country"] == "Syria"
    assert r["near_country"] is None
    assert r["geo_source"] == "scraplan-aoi"
    assert isinstance(r["distance_km"], float) and r["distance_km"] > 0


def test_geo_near_sets_near_country():
    # just north of the Turkey coast — outside all, within the 50 km buffer
    r = classify_reentry(parse_tip_record(_tip(LAT="42.3", LON="35.0")), AREA)
    assert r["country"] is None
    assert r["near_country"] in AREA.country_names
    assert r["geo_source"] == "scraplan-aoi"


def test_geo_outside_no_country_but_classified():
    # mid-Atlantic (55.9607, -30.7313), given in 0..360 E as 329.2687
    r = classify_reentry(parse_tip_record(_tip(LAT="55.9607", LON="329.2687")), AREA)
    assert r["country"] is None and r["near_country"] is None
    assert r["geo_source"] == "scraplan-aoi"                # still classified
    assert isinstance(r["distance_km"], float)


def test_geo_missing_latlon_leaves_geo_null():
    rec = _tip()
    del rec["LAT"]; del rec["LON"]
    r = classify_reentry(parse_tip_record(rec), AREA)
    assert r["country"] is None and r["near_country"] is None
    assert r["geo_source"] is None and r["distance_km"] is None


# ---- row tuple -------------------------------------------------------------

def test_reentry_row_tuple_shape():
    r = classify_reentry(parse_tip_record(_tip(LAT="33.5138", LON="36.2765")), AREA)
    row = reentry_row(r)
    assert len(row) == 18
    assert row[0] == 3027 and row[1] == 60                 # id, norad first
    assert row[13] == "Syria"                              # country in place
    assert row[-1] == "spacetrack_tip"                     # source last
