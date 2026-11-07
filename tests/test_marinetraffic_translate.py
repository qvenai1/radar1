"""
MarineTraffic → VesselState translation tests (pure, offline).

The fixtures are REAL positions.jsonl lines captured from mtscraper against
the live eastern-Mediterranean map, so the mapping is pinned to the actual
contract, not an assumed one.
"""

from __future__ import annotations

from datetime import datetime

from maritime.adapters.marinetraffic_translate import (
    _fix_timestamp,
    _ship_type,
    parse_mt_record,
)

# A real line: container ship VALENCE, no MMSI, shiptype "7" (cargo class).
MT_CARGO = {
    "ship_id": "2786",
    "fetched_at": "2026-07-21T11:07:54.543602+00:00",
    "source": "interception",
    "in_area": True,
    "data": {
        "ship_id": "2786", "mmsi": None,
        "latitude": 31.874666, "longitude": 34.576546,
        "speed_knots": 9.3, "course": 98.0, "heading": 100.0,
        "shipname": "VALENCE", "shiptype": "7", "flag": "MT",
        "destination": "ILASH", "length_m": 299.0, "width_m": 48.0,
        "elapsed_min": 2.0,
    },
    "distance_km": 158.535,
}


# --------------------------- record parsing ---------------------------

def test_parses_core_position_and_identity():
    p = parse_mt_record(MT_CARGO)
    assert p is not None
    assert p.ship_id == "2786"
    assert p.mmsi is None                       # MarineTraffic tile feed omits it
    assert p.position["lat"] == 31.874666 and p.position["lon"] == 34.576546
    assert p.position["speed"] == 9.3           # already knots — not re-scaled
    assert p.position["course"] == 98.0 and p.position["heading"] == 100.0
    assert p.position["nav_status"] is None     # not in the MT feed


def test_static_identity_mapped():
    p = parse_mt_record(MT_CARGO)
    assert p.static["name"] == "VALENCE"
    assert p.static["ship_type"] == "cargo"     # coarse class "7" → cargo
    assert p.static["ship_type_code"] is None   # 1-digit class → no precise ITU code
    assert p.static["length_m"] == 299.0 and p.static["beam_m"] == 48.0  # width→beam
    assert p.static["destination"] == "ILASH"
    assert p.flag == "MT"                        # kept as provenance, not a VesselState field


def test_fix_time_is_fetched_minus_elapsed():
    # fetched_at 11:07:54.543 − 2 min = 11:05:54.543
    p = parse_mt_record(MT_CARGO)
    ts = datetime.fromisoformat(p.position["timestamp"])
    assert (ts.hour, ts.minute, ts.second) == (11, 5, 54)
    assert ts.tzinfo is not None                 # tz-aware UTC


def test_mmsi_passes_through_when_present():
    rec = {**MT_CARGO, "data": {**MT_CARGO["data"], "mmsi": "636092297"}}
    p = parse_mt_record(rec)
    assert p.mmsi == "636092297"


def test_no_ship_id_rejected():
    rec = {**MT_CARGO, "data": {**MT_CARGO["data"], "ship_id": None}, "ship_id": None}
    assert parse_mt_record(rec) is None


def test_no_position_rejected():
    rec = {**MT_CARGO, "data": {**MT_CARGO["data"], "latitude": None, "longitude": None}}
    assert parse_mt_record(rec) is None


def test_out_of_range_position_rejected():
    rec = {**MT_CARGO, "data": {**MT_CARGO["data"], "latitude": 91.0, "longitude": 200.0}}
    assert parse_mt_record(rec) is None


def test_non_dict_rejected():
    assert parse_mt_record("not a record") is None
    assert parse_mt_record(None) is None


def test_in_area_and_distance_preserved():
    p = parse_mt_record(MT_CARGO)
    assert p.in_area is True
    assert p.distance_km == 158.535


def test_flat_record_tolerated():
    # A record without the nested "data" block still parses from top level.
    flat = {"ship_id": "9", "fetched_at": "2026-07-21T11:00:00+00:00",
            "latitude": 34.5, "longitude": 35.7, "speed_knots": 1.0}
    p = parse_mt_record(flat)
    assert p is not None and p.ship_id == "9" and p.position["lat"] == 34.5


# --------------------------- ship-type mapping ---------------------------

def test_ship_type_high_confidence_labels():
    # MarineTraffic's 6/7/8 match both the names and AIS — labelled specifically.
    assert _ship_type("7") == (None, "cargo")
    assert _ship_type("8") == (None, "tanker")
    assert _ship_type("6") == (None, "passenger")


def test_ship_type_uncertain_categories_bucket_to_other():
    # MarineTraffic's scheme is proprietary, NOT the AIS decade: digit 2 is
    # supply/anchor-handling (not wing-in-ground), digit 3 is drillships/pipelayers
    # (not fishing). We refuse the specific-but-wrong label and use 'other'.
    for digit in ("2", "3", "4", "5", "9"):
        assert _ship_type(digit) == (None, "other")


def test_ship_type_unspecified_and_aids_are_none():
    assert _ship_type("0") == (None, None)   # unspecified / VTS
    assert _ship_type("1") == (None, None)   # navigation markers/aids


def test_ship_type_junk():
    assert _ship_type("") == (None, None)
    assert _ship_type(None) == (None, None)
    assert _ship_type("boat") == (None, None)


# --------------------------- fix-time edge cases ---------------------------

def test_fix_timestamp_without_elapsed_keeps_fetched():
    assert _fix_timestamp("2026-07-21T11:07:54+00:00", None) == "2026-07-21T11:07:54+00:00"


def test_fix_timestamp_naive_gets_utc():
    assert _fix_timestamp("2026-07-21T11:07:54", None).endswith("+00:00")


def test_fix_timestamp_unparseable_passthrough_and_missing():
    assert _fix_timestamp("garbage", 2.0) == "garbage"
    assert _fix_timestamp(None, 2.0) is None
