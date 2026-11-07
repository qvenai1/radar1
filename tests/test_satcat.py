"""S1 — CelesTrak SATCAT row -> satellite_info mapping.

Pure-function tests for the SATCAT->columns mapping and its robustness (mirrors
tests/test_opensky_csv.py). The DB-level guarantees (bulk coverage of tracked
norad_ids; provenance source='celestrak_satcat' + satcat_snapshot_date per row;
idempotent double-run = 0 spurious updates; COALESCE never nulls a value with a
blank; satellite_elements/satellite_history + keys untouched) are exercised
against the live DB in the S1 verification gauntlet, matching this suite's
no-DB convention.
"""

from datetime import date

from scripts.backfill_from_satcat import map_row

# Owner-code decode map is injected (hermetic — no dependency on the JSON file).
SOURCES = {"US": "United States", "PRC": "People's Republic of China"}

# A real SATCAT row (NORAD 24876, GPS BIIR-2), header keys as in satcat.csv.
BASE = {
    "OBJECT_NAME": "GPS BIIR-2  (PRN 13)", "OBJECT_ID": "1997-035A",
    "NORAD_CAT_ID": "24876", "OBJECT_TYPE": "PAY", "OPS_STATUS_CODE": "+",
    "OWNER": "US", "LAUNCH_DATE": "1997-07-23", "LAUNCH_SITE": "AFETR",
    "DECAY_DATE": "", "PERIOD": "717.97", "INCLINATION": "55.47",
    "APOGEE": "20273", "PERIGEE": "20026", "RCS": "9.4361",
    "DATA_STATUS_CODE": "", "ORBIT_CENTER": "EA", "ORBIT_TYPE": "ORB",
}

# tuple order: (norad_id, intl_des, object_name, object_type, owner, owner_name,
#               launch_date, launch_site, decay_date, ops_status, rcs_m2)


def test_map_row_full():
    r = map_row(BASE, SOURCES)
    assert r == (
        24876, "1997-035A", "GPS BIIR-2  (PRN 13)", "PAY", "US", "United States",
        date(1997, 7, 23), "AFETR", None, "+", 9.4361,
    )


def test_country_decoded_from_owner_code():
    assert map_row(dict(BASE, OWNER="PRC"), SOURCES)[5] == "People's Republic of China"


def test_unknown_owner_code_leaves_name_null_no_guess():
    r = map_row(dict(BASE, OWNER="ZZZ"), SOURCES)
    assert r[4] == "ZZZ" and r[5] is None   # raw code kept; name not invented


def test_object_type_kept_raw():
    # raw SATCAT code is authoritative (the readable label is a presentation layer)
    assert map_row(dict(BASE, OBJECT_TYPE="R/B"), SOURCES)[3] == "R/B"
    assert map_row(dict(BASE, OBJECT_TYPE="DEB"), SOURCES)[3] == "DEB"


def test_launch_date_parsed_to_date():
    assert map_row(BASE, SOURCES)[6] == date(1997, 7, 23)


def test_decay_date_none_means_on_orbit():
    assert map_row(BASE, SOURCES)[8] is None
    assert map_row(dict(BASE, DECAY_DATE="2001-03-23"), SOURCES)[8] == date(2001, 3, 23)


def test_malformed_date_degrades_to_none_not_drop():
    r = map_row(dict(BASE, LAUNCH_DATE="not-a-date"), SOURCES)
    assert r is not None and r[6] is None and r[0] == 24876   # row kept, date NULL


def test_rcs_parsed_and_malformed_becomes_none():
    assert map_row(BASE, SOURCES)[10] == 9.4361
    assert map_row(dict(BASE, RCS=""), SOURCES)[10] is None
    assert map_row(dict(BASE, RCS="N/A"), SOURCES)[10] is None


def test_empty_strings_become_none():
    r = map_row(dict(BASE, OBJECT_ID="", LAUNCH_SITE="   ", OWNER=""), SOURCES)
    assert r[1] is None and r[7] is None and r[4] is None and r[5] is None


def test_bad_norad_skipped():
    assert map_row(dict(BASE, NORAD_CAT_ID="ABC"), SOURCES) is None    # non-numeric
    assert map_row(dict(BASE, NORAD_CAT_ID=""), SOURCES) is None       # empty
    assert map_row(dict(BASE, NORAD_CAT_ID="0"), SOURCES) is None      # not positive
    assert map_row(dict(BASE, NORAD_CAT_ID="-5"), SOURCES) is None     # negative (non-digit)


def test_row_with_no_facts_skipped():
    # keyable norad but no identity fact at all -> not worth a row
    assert map_row({"NORAD_CAT_ID": "12345"}, SOURCES) is None


def test_missing_columns_do_not_crash():
    # a partial/short row maps on what's present, never raises
    r = map_row({"NORAD_CAT_ID": "25544", "OBJECT_NAME": "ISS (ZARYA)"}, SOURCES)
    assert r == (25544, None, "ISS (ZARYA)", None, None, None, None, None, None, None, None)


def test_default_sources_map_loads_from_repo():
    # with no injected map, the module falls back to backend/data/satcat_sources.json;
    # 'US' is a stable CelesTrak code and must decode.
    assert map_row(BASE)[5] == "United States"
