"""M2a — flag state (country of registration) from the MMSI MID.

Verifies the ITU MID lookup is faithful on known codes (including the seven AOI
flags and the major flags of convenience), that only ship-station MMSIs (first
digit 2-7) are flagged, that other station formats are honestly typed and NOT
mis-flagged, that unallocated/invalid inputs return None, and that the whole
derivation is deterministic.
"""

import json

import pytest

from backend.mmsi_flag import (
    DEFAULT_ASSET,
    MID_SOURCE,
    STATION_TYPES,
    MidAssetError,
    _load_mids,
    mmsi_to_flag,
)


@pytest.mark.parametrize("mmsi, country", [
    # seven-region AOI flags — names match backend/geo_area.py (cross-view join)
    ("468123456", "Syria"),            # MID 468
    ("271123456", "Turkey"),           # MID 271
    ("425123456", "Iraq"),             # MID 425
    ("438123456", "Jordan"),           # MID 438
    ("450123456", "Lebanon"),          # MID 450
    ("428123456", "Israel"),           # MID 428
    ("212345678", "Cyprus"),           # MID 212 (Cyprus has 209/210/212)
    # major flags of convenience
    ("351123456", "Panama"),           # MID 351 (Panama 351-357, 370-374)
    ("636123456", "Liberia"),          # MID 636
    ("538123456", "Marshall Islands"),  # MID 538
    ("249123456", "Malta"),            # MID 249
    # a few more, incl. normalized names
    ("338123456", "United States"),    # MID 338 (normalized from 'United States of America')
    ("622123456", "Egypt"),            # MID 622
    ("403123456", "Saudi Arabia"),     # MID 403
    ("477123456", "Hong Kong"),        # MID 477
])
def test_known_flags(mmsi, country):
    flag, station = mmsi_to_flag(mmsi)
    assert flag == country
    assert station == "ship"


def test_all_seven_aoi_flags_present_and_named_for_geo_join():
    # The seven AOI flags must resolve to the exact geo_area.py names so the
    # M1(geo) x M2a(flag) cross-view is a direct string join.
    want = {
        "468000000": "Syria", "271000000": "Turkey", "425000000": "Iraq",
        "438000000": "Jordan", "450000000": "Lebanon", "428000000": "Israel",
        "209000000": "Cyprus", "210000000": "Cyprus", "212000000": "Cyprus",
    }
    for mmsi, country in want.items():
        assert mmsi_to_flag(mmsi) == (country, "ship")


@pytest.mark.parametrize("mmsi, station", [
    ("002191000", "coast"),              # 00MID… coast station
    ("002570000", "coast"),
    ("992351000", "aid-to-navigation"),  # 99MID… aid to navigation
    ("111257001", "sar"),                # 111MID… SAR aircraft
    ("970123456", "sar"),                # 970… AIS-SART (SAR transmitter)
    ("972123456", "other"),              # 972… MOB device
    ("974123456", "other"),              # 974… EPIRB-AIS
    ("982191000", "other"),              # 98MID… craft associated with parent ship
    ("012345678", "other"),              # 0MID… group of ship stations
    ("812345678", "other"),              # 8… handheld VHF/DSC
])
def test_non_ship_stations_typed_not_flagged(mmsi, station):
    # Non-vessel MMSIs get an honest station_type and NEVER a flag country.
    flag, st = mmsi_to_flag(mmsi)
    assert st == station
    assert flag is None


def test_unallocated_mid_is_none_not_a_guess():
    # 217 sits between 216 (Armenia) and 218 (Germany) and is unallocated;
    # a ship station with it is typed 'ship' but has NO country (never guessed).
    flag, station = mmsi_to_flag("217123456")
    assert station == "ship" and flag is None
    # 200 / 299 likewise unallocated
    assert mmsi_to_flag("200000000") == (None, "ship")
    assert mmsi_to_flag("299000000") == (None, "ship")


@pytest.mark.parametrize("bad", [
    None, "", "   ", "abcdefghi", "12345", "1234567890",  # wrong length / non-digit
    12345, 3.14, "27112345",                              # not a 9-char string / 8 digits
])
def test_invalid_input_is_other_none(bad):
    flag, station = mmsi_to_flag(bad)
    assert flag is None and station == "other"


def test_int_mmsi_accepted_when_nine_digits():
    # A 9-digit int has no lost leading zeros, so it is accepted like the string.
    assert mmsi_to_flag(271123456) == ("Turkey", "ship")
    # An int that WOULD need a leading zero (coast station) is <9 digits -> invalid,
    # never silently re-padded into a wrong classification.
    assert mmsi_to_flag(2191000) == (None, "other")


def test_station_type_always_in_closed_set():
    for mmsi in ["468123456", "002191000", "992351000", "111257001",
                 "970123456", "812345678", "notanmmsi", "217123456"]:
        _, station = mmsi_to_flag(mmsi)
        assert station in STATION_TYPES


def test_deterministic_idempotent():
    a = [mmsi_to_flag(m) for m in ("468123456", "351123456", "002191000", "217123456")]
    b = [mmsi_to_flag(m) for m in ("468123456", "351123456", "002191000", "217123456")]
    assert a == b == [("Syria", "ship"), ("Panama", "ship"),
                      (None, "coast"), (None, "ship")]


def test_source_label_constant():
    assert MID_SOURCE == "mmsi-mid"


def test_asset_covers_full_published_mid_list():
    # The committed asset must be non-trivially complete and well-formed: every
    # key a 3-digit MID starting 2-7, every value a country name.
    raw = json.loads(DEFAULT_ASSET.read_text(encoding="utf-8"))
    mids = raw["mids"]
    assert len(mids) >= 280            # ~292 assigned MIDs in the ITU table
    assert all(len(k) == 3 and k.isdigit() and k[0] in "234567" for k in mids)
    assert all(isinstance(v, str) and v for v in mids.values())


def test_missing_asset_fails_fast(tmp_path):
    with pytest.raises(MidAssetError):
        _load_mids(tmp_path / "nope.json")


def test_malformed_asset_fails_fast(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"mids": {"999": "Nowhere"}}))  # MID must start 2-7
    with pytest.raises(MidAssetError):
        _load_mids(bad)
